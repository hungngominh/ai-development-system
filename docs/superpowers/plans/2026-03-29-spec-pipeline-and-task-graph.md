# Spec Pipeline + Task Graph Generator — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first closable loop: raw idea → normalize → Gate 1 → spec bundle → task graph → Gate 2 → approved graph ready for execution engine.

**Architecture:** Three phases. Task 0: DB migration prep. Phase A (Tasks 1-6): spec pipeline — normalize raw text, human confirms, generate 5-file spec bundle. Phase B (Tasks 7-14): task graph generator — deterministic skeleton + rule engine + optional LLM enrichment + Gate 2 + bridge to execution engine. Both phases are synchronous, engine-integrated (run → task_run → artifact → event). All work happens in the `.worktrees/minimal-worker-loop` worktree.

**Tech Stack:** Python 3.11+, psycopg v3 (sync), pytest. Existing infra: `promote_output()`, `build_temp_path()`, DB repos, checksum/stability layer.

**Specs:**
- `docs/superpowers/specs/2026-03-29-spec-pipeline-design.md`
- `docs/superpowers/specs/2026-03-29-task-graph-generator-design.md`

---

## File Structure

### New files (Phase A — Spec Pipeline)

```
src/ai_dev_system/
    normalize.py              # normalize_idea(), validate_brief(), SCOPE_TYPES, COMPLEXITY_HINTS
    spec_bundle.py            # generate_spec_bundle(), validate_spec_bundle(), SpecBundle, REQUIRED_FILES
    pipeline.py               # run_spec_pipeline(), PipelineAborted, ValidationError, _write_json_to_temp
    gate/
        __init__.py
        interface.py          # GateIO protocol
        core.py               # run_gate_1(), GateResult
        cli.py                # CLIGateIO
        stub.py               # StubGateIO

tests/
    unit/
        test_normalize.py
        test_spec_bundle.py
        test_gate_core.py
    integration/
        test_pipeline.py
```

### New files (Phase B — Task Graph Generator)

```
src/ai_dev_system/
    task_graph/
        __init__.py
        skeleton.py           # CORE_SKELETON, build_skeleton()
        rules.py              # RULES, apply_rules(), add_parallel/before/after, _find
        enricher.py           # LLMClient protocol, enrich_task(), enrich_all(), ENRICHABLE_FIELDS
        validator.py          # validate_graph(), CORE_IDS, REQUIRED_FIELDS, _has_cycle
        generator.py          # generate_task_graph(), GraphValidationError
    gate/
        gate2.py              # Gate2IO, Gate2Result, run_gate_2()
        stub_gate2.py         # StubGate2IO

tests/
    unit/
        test_skeleton.py
        test_rules.py
        test_validator.py
        test_enricher.py
    integration/
        test_generator.py
        test_gate2.py
        test_pipeline_full.py
```

### Modified files

```
src/ai_dev_system/
    storage/paths.py          # Add APPROVED_BRIEF to ARTIFACT_TYPE_TO_KEY
    db/repos/runs.py          # Add RunRepo.create()
    db/repos/task_runs.py     # Add TaskRunRepo.create_sync(), create_from_graph()
```

---

## Task 0: DB Schema Preparation

**Files:**
- No code files — SQL migration only

This task prepares the database for the new pipeline. Must run before any integration tests.

- [ ] **Step 1: Add APPROVED_BRIEF to artifact_type enum**

```sql
ALTER TYPE artifact_type ADD VALUE IF NOT EXISTS 'APPROVED_BRIEF';
```

- [ ] **Step 2: Make task_graph_artifact_id nullable**

Pipeline-level task_runs (normalize, gate, spec bundle) don't have a task graph artifact.

```sql
ALTER TABLE task_runs ALTER COLUMN task_graph_artifact_id DROP NOT NULL;
```

- [ ] **Step 3: Verify**

```bash
cd .worktrees/minimal-worker-loop && python -c "
import psycopg, os
conn = psycopg.connect(os.environ['DATABASE_URL'])
conn.execute(\"SELECT 'APPROVED_BRIEF'::artifact_type\")
print('APPROVED_BRIEF enum OK')
row = conn.execute(\"SELECT is_nullable FROM information_schema.columns WHERE table_name='task_runs' AND column_name='task_graph_artifact_id'\").fetchone()
print(f'task_graph_artifact_id nullable: {row[0]}')
conn.close()
"
```

Expected: Both checks pass

- [ ] **Step 4: Commit migration note**

```bash
echo "-- Migration: 2026-03-29 spec-pipeline prep
ALTER TYPE artifact_type ADD VALUE IF NOT EXISTS 'APPROVED_BRIEF';
ALTER TABLE task_runs ALTER COLUMN task_graph_artifact_id DROP NOT NULL;
" > .worktrees/minimal-worker-loop/migrations/002_spec_pipeline_prep.sql
git add .worktrees/minimal-worker-loop/migrations/
git commit -m "chore: DB migration — add APPROVED_BRIEF enum, nullable task_graph_artifact_id"
```

---

## Phase A: Spec Pipeline

### Task 1: normalize_idea + validate_brief

**Files:**
- Create: `src/ai_dev_system/normalize.py`
- Create: `tests/unit/test_normalize.py`

- [ ] **Step 1: Write failing test — normalize_idea produces valid brief**

```python
# tests/unit/test_normalize.py
from ai_dev_system.normalize import normalize_idea, validate_brief, SCOPE_TYPES, COMPLEXITY_HINTS


def test_normalize_produces_valid_brief():
    brief = normalize_idea("Build a forum for sharing knowledge")
    assert brief["raw_idea"] == "Build a forum for sharing knowledge"
    assert brief["id"]  # non-empty UUID
    assert brief["version"] == 1
    assert brief["source_hash"]  # non-empty sha256
    assert brief["problem"] == ""
    assert brief["target_users"] == ""
    assert brief["goal"] == ""
    assert brief["constraints"] == {"hard": [], "soft": []}
    assert brief["assumptions"] == []
    assert brief["scope"] == {"type": "unknown", "complexity_hint": "unknown"}
    assert brief["success_signals"] == []
    errors = validate_brief(brief)
    assert errors == []


def test_normalize_strips_whitespace():
    brief = normalize_idea("  Build a forum  ")
    assert brief["raw_idea"] == "Build a forum"


def test_normalize_rejects_empty():
    import pytest
    with pytest.raises(ValueError, match="non-empty"):
        normalize_idea("")
    with pytest.raises(ValueError, match="non-empty"):
        normalize_idea("   ")


def test_normalize_source_hash_deterministic():
    b1 = normalize_idea("same idea")
    b2 = normalize_idea("same idea")
    assert b1["source_hash"] == b2["source_hash"]
    assert b1["id"] != b2["id"]  # UUID is unique each time
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd .worktrees/minimal-worker-loop && python -m pytest tests/unit/test_normalize.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'ai_dev_system.normalize'`

- [ ] **Step 3: Implement normalize.py**

```python
# src/ai_dev_system/normalize.py
import hashlib
from uuid import uuid4

SCOPE_TYPES = {"product", "feature", "experiment", "unknown"}
COMPLEXITY_HINTS = {"low", "medium", "high", "unknown"}


def normalize_idea(raw_text: str) -> dict:
    """Parse raw text into structured brief skeleton."""
    stripped = raw_text.strip()
    if not stripped:
        raise ValueError("raw_idea must be non-empty")
    return {
        "id": str(uuid4()),
        "version": 1,
        "raw_idea": stripped,
        "source_hash": hashlib.sha256(stripped.encode()).hexdigest(),
        "problem": "",
        "target_users": "",
        "goal": "",
        "constraints": {"hard": [], "soft": []},
        "assumptions": [],
        "scope": {"type": "unknown", "complexity_hint": "unknown"},
        "success_signals": [],
    }


def validate_brief(brief: dict) -> list[str]:
    """Validate brief against schema. Returns list of errors (empty = valid)."""
    errors = []
    if not brief.get("id"):
        errors.append("id is required")
    v = brief.get("version")
    if not isinstance(v, int) or v < 1:
        errors.append("version must be int >= 1")
    if not brief.get("raw_idea", "").strip():
        errors.append("raw_idea must be non-empty")
    if brief.get("scope", {}).get("type") not in SCOPE_TYPES:
        errors.append(f"scope.type must be one of {SCOPE_TYPES}")
    if brief.get("scope", {}).get("complexity_hint") not in COMPLEXITY_HINTS:
        errors.append(f"scope.complexity_hint must be one of {COMPLEXITY_HINTS}")
    # Strict: no extra keys (top-level)
    allowed = {"id", "version", "raw_idea", "source_hash", "problem", "target_users",
               "goal", "constraints", "assumptions", "scope", "success_signals"}
    extra = set(brief.keys()) - allowed
    if extra:
        errors.append(f"Extra keys not allowed: {extra}")
    # Strict: no extra keys (nested)
    constraints = brief.get("constraints", {})
    if isinstance(constraints, dict):
        extra_c = set(constraints.keys()) - {"hard", "soft"}
        if extra_c:
            errors.append(f"Extra keys in constraints: {extra_c}")
    scope = brief.get("scope", {})
    if isinstance(scope, dict):
        extra_s = set(scope.keys()) - {"type", "complexity_hint"}
        if extra_s:
            errors.append(f"Extra keys in scope: {extra_s}")
    return errors
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd .worktrees/minimal-worker-loop && python -m pytest tests/unit/test_normalize.py -v
```

Expected: 4 passed

- [ ] **Step 5: Write failing test — validate_brief catches errors**

```python
# tests/unit/test_normalize.py (append)

def test_validate_missing_id():
    brief = normalize_idea("test")
    brief["id"] = ""
    assert "id is required" in validate_brief(brief)


def test_validate_bad_version():
    brief = normalize_idea("test")
    brief["version"] = 0
    assert any("version" in e for e in validate_brief(brief))


def test_validate_bad_scope_type():
    brief = normalize_idea("test")
    brief["scope"]["type"] = "invalid"
    assert any("scope.type" in e for e in validate_brief(brief))


def test_validate_extra_top_level_key():
    brief = normalize_idea("test")
    brief["extra"] = "nope"
    assert any("Extra keys" in e for e in validate_brief(brief))


def test_validate_extra_nested_key():
    brief = normalize_idea("test")
    brief["constraints"]["priority"] = "high"
    assert any("constraints" in e for e in validate_brief(brief))
```

- [ ] **Step 6: Run test — should pass immediately (implementation already handles these)**

```bash
cd .worktrees/minimal-worker-loop && python -m pytest tests/unit/test_normalize.py -v
```

Expected: 9 passed

- [ ] **Step 7: Commit**

```bash
git add src/ai_dev_system/normalize.py tests/unit/test_normalize.py
git commit -m "feat: normalize_idea + validate_brief with strict schema"
```

---

### Task 2: GateIO protocol + Gate 1 core + StubGateIO

**Files:**
- Create: `src/ai_dev_system/gate/__init__.py`
- Create: `src/ai_dev_system/gate/interface.py`
- Create: `src/ai_dev_system/gate/core.py`
- Create: `src/ai_dev_system/gate/stub.py`
- Create: `tests/unit/test_gate_core.py`

- [ ] **Step 1: Write failing test — gate approve flow**

```python
# tests/unit/test_gate_core.py
from ai_dev_system.gate.core import run_gate_1, GateResult
from ai_dev_system.gate.stub import StubGateIO
from ai_dev_system.normalize import normalize_idea


def test_gate_approve_no_edits():
    brief = normalize_idea("Build a forum")
    io = StubGateIO(approve=True)
    result = run_gate_1(brief, io)
    assert result.status == "approved"
    assert result.brief["raw_idea"] == "Build a forum"
    assert io.presented is not None


def test_gate_approve_with_edits():
    brief = normalize_idea("Build a forum")
    io = StubGateIO(edits={"problem": "No knowledge sharing"}, approve=True)
    result = run_gate_1(brief, io)
    assert result.status == "approved"
    assert result.brief["problem"] == "No knowledge sharing"
    assert result.brief["raw_idea"] == "Build a forum"  # immutable


def test_gate_reject():
    brief = normalize_idea("Build a forum")
    io = StubGateIO(approve=False)
    result = run_gate_1(brief, io)
    assert result.status == "rejected"
    assert result.brief["problem"] == ""  # original, not edited


def test_gate_deep_edit_nested():
    brief = normalize_idea("Build a forum")
    io = StubGateIO(edits={"constraints": {"hard": ["Must use PostgreSQL"]}}, approve=True)
    result = run_gate_1(brief, io)
    assert result.brief["constraints"]["hard"] == ["Must use PostgreSQL"]
    assert result.brief["constraints"]["soft"] == []  # not corrupted
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd .worktrees/minimal-worker-loop && python -m pytest tests/unit/test_gate_core.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Create gate package**

```python
# src/ai_dev_system/gate/__init__.py
# Gate package
```

```python
# src/ai_dev_system/gate/interface.py
from typing import Protocol


class GateIO(Protocol):
    def present(self, brief: dict) -> None: ...
    def collect_edit(self, brief: dict) -> dict: ...
    def confirm(self, brief: dict) -> bool: ...
```

```python
# src/ai_dev_system/gate/core.py
from dataclasses import dataclass
from typing import Literal


@dataclass
class GateResult:
    status: Literal["approved", "rejected"]
    brief: dict


def run_gate_1(brief: dict, io) -> GateResult:
    """Present brief, collect edits, confirm. IO-agnostic."""
    io.present(brief)
    updated = io.collect_edit(brief)
    if io.confirm(updated):
        return GateResult(status="approved", brief=updated)
    return GateResult(status="rejected", brief=brief)
```

```python
# src/ai_dev_system/gate/stub.py
import copy


class StubGateIO:
    """Test double: auto-approves with optional field overrides."""

    def __init__(self, edits: dict | None = None, approve: bool = True):
        self.edits = edits or {}
        self.approve = approve
        self.presented = None

    def present(self, brief: dict) -> None:
        self.presented = brief

    def collect_edit(self, brief: dict) -> dict:
        updated = copy.deepcopy(brief)
        for key, value in self.edits.items():
            if isinstance(value, dict) and isinstance(updated.get(key), dict):
                updated[key].update(value)
            else:
                updated[key] = value
        return updated

    def confirm(self, brief: dict) -> bool:
        return self.approve
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd .worktrees/minimal-worker-loop && python -m pytest tests/unit/test_gate_core.py -v
```

Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/gate/ tests/unit/test_gate_core.py
git commit -m "feat: GateIO protocol, Gate 1 core logic, StubGateIO"
```

---

### Task 3: Spec bundle generator

**Files:**
- Create: `src/ai_dev_system/spec_bundle.py`
- Create: `tests/unit/test_spec_bundle.py`

- [ ] **Step 1: Write failing test — generates 5 files from brief**

```python
# tests/unit/test_spec_bundle.py
import os
from pathlib import Path
from ai_dev_system.normalize import normalize_idea
from ai_dev_system.spec_bundle import generate_spec_bundle, validate_spec_bundle, REQUIRED_FILES


def test_generate_creates_all_files(tmp_path):
    brief = normalize_idea("Build a forum")
    brief["problem"] = "No knowledge sharing"
    brief["goal"] = "Share knowledge internally"
    brief["constraints"]["hard"] = ["Must use PostgreSQL"]
    brief["success_signals"] = ["Search < 5s"]
    bundle = generate_spec_bundle(brief, tmp_path / "specs")
    assert bundle.version == 1
    assert bundle.root_dir == tmp_path / "specs"
    for filename in REQUIRED_FILES:
        assert filename in bundle.files
        assert bundle.files[filename].exists()
        assert bundle.files[filename].stat().st_size > 0


def test_generate_problem_md_content(tmp_path):
    brief = normalize_idea("Build a forum")
    brief["problem"] = "No knowledge sharing"
    brief["target_users"] = "Internal devs"
    bundle = generate_spec_bundle(brief, tmp_path / "specs")
    content = bundle.files["problem.md"].read_text()
    assert "Build a forum" in content  # raw_idea
    assert "No knowledge sharing" in content
    assert "Internal devs" in content
    assert "Do Not Interpret" in content  # immutable marker


def test_generate_constraints_with_prefixes(tmp_path):
    brief = normalize_idea("test")
    brief["constraints"]["hard"] = ["Must use PostgreSQL"]
    brief["constraints"]["soft"] = ["Prefer Python"]
    bundle = generate_spec_bundle(brief, tmp_path / "specs")
    content = bundle.files["constraints.md"].read_text()
    assert "[HARD]" in content
    assert "[SOFT]" in content
    assert "PostgreSQL" in content
    assert "Python" in content


def test_generate_empty_fields_show_placeholder(tmp_path):
    brief = normalize_idea("test")
    bundle = generate_spec_bundle(brief, tmp_path / "specs")
    content = bundle.files["problem.md"].read_text()
    assert "(not specified" in content


def test_validate_spec_bundle_clean(tmp_path):
    brief = normalize_idea("test")
    bundle = generate_spec_bundle(brief, tmp_path / "specs")
    warnings = validate_spec_bundle(bundle.root_dir)
    assert warnings == []


def test_validate_spec_bundle_missing_file(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "problem.md").write_text("x")
    warnings = validate_spec_bundle(tmp_path)
    assert len(warnings) == 4  # 4 missing files
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd .worktrees/minimal-worker-loop && python -m pytest tests/unit/test_spec_bundle.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement spec_bundle.py**

```python
# src/ai_dev_system/spec_bundle.py
from dataclasses import dataclass
from pathlib import Path

REQUIRED_FILES = ["problem.md", "requirements.md", "constraints.md",
                  "success_criteria.md", "assumptions.md"]


@dataclass
class SpecBundle:
    version: int
    root_dir: Path
    files: dict[str, Path]


def generate_spec_bundle(approved_brief: dict, output_dir: Path) -> SpecBundle:
    """Write 5 spec files from approved brief. Returns SpecBundle."""
    output_dir.mkdir(parents=True, exist_ok=True)
    files = {}
    files["problem.md"] = _write_problem(approved_brief, output_dir)
    files["requirements.md"] = _write_requirements(approved_brief, output_dir)
    files["constraints.md"] = _write_constraints(approved_brief, output_dir)
    files["success_criteria.md"] = _write_success_criteria(approved_brief, output_dir)
    files["assumptions.md"] = _write_assumptions(approved_brief, output_dir)
    return SpecBundle(version=1, root_dir=output_dir, files=files)


def validate_spec_bundle(spec_dir: Path) -> list[str]:
    """Return list of warnings. Empty = clean."""
    warnings = []
    for filename in REQUIRED_FILES:
        path = spec_dir / filename
        if not path.exists():
            warnings.append(f"Missing: {filename}")
        elif path.stat().st_size == 0:
            warnings.append(f"Empty: {filename}")
    return warnings


def _or_placeholder(value: str) -> str:
    return value if value else "(not specified — to be refined)"


def _write_problem(brief: dict, out: Path) -> Path:
    p = out / "problem.md"
    p.write_text(
        f"# Problem Statement\n\n"
        f"## Raw Idea (Original Input — Do Not Interpret)\n{brief['raw_idea']}\n\n"
        f"## Problem\n{_or_placeholder(brief['problem'])}\n\n"
        f"## Target Users\n{_or_placeholder(brief['target_users'])}\n"
    )
    return p


def _write_requirements(brief: dict, out: Path) -> Path:
    p = out / "requirements.md"
    scope = brief.get("scope", {})
    p.write_text(
        f"# Requirements\n\n"
        f"## Problem Alignment\nThis goal addresses the problem described in problem.md.\n\n"
        f"## Goal\n{_or_placeholder(brief['goal'])}\n\n"
        f"---\n\n"
        f"## Scope Definition (Execution Context)\n"
        f"- Type: {scope.get('type', 'unknown')}\n"
        f"- Complexity: {scope.get('complexity_hint', 'unknown')}\n"
    )
    return p


def _write_constraints(brief: dict, out: Path) -> Path:
    p = out / "constraints.md"
    hard = brief.get("constraints", {}).get("hard", [])
    soft = brief.get("constraints", {}).get("soft", [])
    hard_text = "\n".join(f"- [HARD] {c}" for c in hard) if hard else "(none specified)"
    soft_text = "\n".join(f"- [SOFT] {c}" for c in soft) if soft else "(none specified)"
    p.write_text(
        f"# Constraints\n\n"
        f"## Hard Constraints (MUST satisfy)\n{hard_text}\n\n"
        f"## Soft Constraints (SHOULD satisfy, tradeable)\n{soft_text}\n"
    )
    return p


def _write_success_criteria(brief: dict, out: Path) -> Path:
    p = out / "success_criteria.md"
    signals = brief.get("success_signals", [])
    if signals:
        items = "\n".join(
            f"- [ ] {s}\n  - Metric: (to be defined)\n  - Target: (to be defined)"
            for s in signals
        )
    else:
        items = "(no signals defined — verification will use goal as proxy)"
    p.write_text(f"# Success Criteria\n\n{items}\n")
    return p


def _write_assumptions(brief: dict, out: Path) -> Path:
    p = out / "assumptions.md"
    assumptions = brief.get("assumptions", [])
    if assumptions:
        items = "\n".join(f"- {a}" for a in assumptions)
    else:
        items = "(no assumptions recorded)"
    p.write_text(
        f"# Assumptions\n\n{items}\n\n"
        f"> These assumptions have not been validated.\n"
        f"> Debate crew may challenge these. Task execution should flag if an assumption proves false.\n"
    )
    return p
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd .worktrees/minimal-worker-loop && python -m pytest tests/unit/test_spec_bundle.py -v
```

Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/spec_bundle.py tests/unit/test_spec_bundle.py
git commit -m "feat: spec bundle generator with 5-file mapping + validation"
```

---

### Task 4: DB repo extensions (RunRepo.create, TaskRunRepo.create_sync)

**Files:**
- Modify: `src/ai_dev_system/db/repos/runs.py`
- Modify: `src/ai_dev_system/db/repos/task_runs.py`
- Modify: `src/ai_dev_system/storage/paths.py`
- Create: `tests/integration/test_repo_extensions.py`

- [ ] **Step 1: Write failing test — RunRepo.create**

```python
# tests/integration/test_repo_extensions.py
import uuid
from ai_dev_system.db.repos.runs import RunRepo
from ai_dev_system.db.repos.task_runs import TaskRunRepo


def test_run_repo_create(conn, project_id):
    repo = RunRepo(conn)
    run_id = repo.create(project_id=project_id, pipeline_type="spec_pipeline")
    assert run_id
    row = conn.execute("SELECT * FROM runs WHERE run_id = %s", (run_id,)).fetchone()
    assert row["status"] == "RUNNING_PHASE_1A"
    assert row["project_id"] == project_id


def test_task_run_repo_create_sync(conn, project_id):
    run_repo = RunRepo(conn)
    run_id = run_repo.create(project_id=project_id, pipeline_type="spec_pipeline")
    repo = TaskRunRepo(conn)
    task_run = repo.create_sync(run_id=run_id, task_type="normalize_idea")
    assert task_run["task_run_id"]
    assert task_run["run_id"] == run_id
    assert task_run["task_id"] == "normalize_idea"
    assert task_run["attempt_number"] == 1
    row = conn.execute(
        "SELECT * FROM task_runs WHERE task_run_id = %s", (task_run["task_run_id"],)
    ).fetchone()
    assert row["status"] == "RUNNING"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd .worktrees/minimal-worker-loop && python -m pytest tests/integration/test_repo_extensions.py -v
```

Expected: FAIL — `AttributeError: 'RunRepo' object has no attribute 'create'`

- [ ] **Step 3: Implement RunRepo.create**

Add to `src/ai_dev_system/db/repos/runs.py`:

```python
import uuid
import psycopg.types.json

# ... existing code ...

    def create(self, project_id: str, pipeline_type: str) -> str:
        """Create a new run. Returns run_id.
        Initializes current_artifacts with all null keys per schema invariant."""
        run_id = str(uuid.uuid4())
        initial_artifacts = {
            "initial_brief_id": None, "debate_report_id": None,
            "decision_log_id": None, "approved_answers_id": None,
            "approved_brief_id": None, "spec_bundle_id": None,
            "task_graph_gen_id": None, "task_graph_approved_id": None,
        }
        self.conn.execute("""
            INSERT INTO runs (run_id, project_id, status, title, current_artifacts, metadata)
            VALUES (%s, %s, 'RUNNING_PHASE_1A', %s, %s, '{}')
        """, (run_id, project_id, f"Pipeline: {pipeline_type}",
              psycopg.types.json.Jsonb(initial_artifacts)))
        return run_id
```

- [ ] **Step 4: Implement TaskRunRepo.create_sync**

Add to `src/ai_dev_system/db/repos/task_runs.py`:

```python
import uuid

# ... existing code ...

    def create_sync(self, run_id: str, task_type: str) -> dict:
        """Create a task_run for synchronous pipeline. Returns full dict.
        Sets status=RUNNING, started_at=now(). No SKIP LOCKED — single-threaded."""
        task_run_id = str(uuid.uuid4())
        self.conn.execute("""
            INSERT INTO task_runs (
                task_run_id, run_id, task_id, attempt_number, status,
                agent_type, started_at, heartbeat_at,
                input_artifact_ids, resolved_dependencies, promoted_outputs
            ) VALUES (%s, %s, %s, 1, 'RUNNING', 'pipeline', now(), now(), '{}', '{}', '[]')
        """, (task_run_id, run_id, task_type))
        return {
            "task_run_id": task_run_id,
            "run_id": run_id,
            "task_id": task_type,
            "attempt_number": 1,
            "status": "RUNNING",
        }
```

- [ ] **Step 5: Add APPROVED_BRIEF to paths.py**

In `src/ai_dev_system/storage/paths.py`, add to `ARTIFACT_TYPE_TO_KEY`:

```python
"APPROVED_BRIEF":     "approved_brief_id",
```

- [ ] **Step 6: Run test to verify it passes**

```bash
cd .worktrees/minimal-worker-loop && python -m pytest tests/integration/test_repo_extensions.py -v
```

Expected: 2 passed

- [ ] **Step 7: Commit**

```bash
git add src/ai_dev_system/db/repos/runs.py src/ai_dev_system/db/repos/task_runs.py src/ai_dev_system/storage/paths.py tests/integration/test_repo_extensions.py
git commit -m "feat: RunRepo.create + TaskRunRepo.create_sync + APPROVED_BRIEF artifact type"
```

---

### Task 5: Pipeline runner (spec pipeline end-to-end)

**Files:**
- Create: `src/ai_dev_system/pipeline.py`
- Create: `tests/integration/test_pipeline.py`

- [ ] **Step 1: Write failing integration test — full pipeline with StubGateIO**

```python
# tests/integration/test_pipeline.py
import os
import json
from pathlib import Path
from ai_dev_system.pipeline import run_spec_pipeline, PipelineAborted
from ai_dev_system.gate.stub import StubGateIO


def test_spec_pipeline_end_to_end(conn, config, project_id):
    """Full pipeline: normalize → gate 1 (approve) → spec bundle."""
    io = StubGateIO(
        edits={
            "problem": "No knowledge sharing",
            "goal": "Internal forum",
            "constraints": {"hard": ["Must use PostgreSQL"]},
            "scope": {"type": "product", "complexity_hint": "medium"},
        },
        approve=True,
    )
    bundle = run_spec_pipeline(
        raw_idea="Build a forum for sharing knowledge",
        config=config,
        conn=conn,
        project_id=project_id,
        io=io,
    )
    assert bundle.version == 1
    assert (bundle.root_dir / "problem.md").exists()
    assert (bundle.root_dir / "constraints.md").exists()
    # Verify DB state
    runs = conn.execute("SELECT * FROM runs WHERE project_id = %s", (project_id,)).fetchall()
    assert len(runs) == 1
    task_runs = conn.execute(
        "SELECT * FROM task_runs WHERE run_id = %s ORDER BY started_at",
        (runs[0]["run_id"],)
    ).fetchall()
    assert len(task_runs) == 3
    assert all(tr["status"] == "SUCCESS" for tr in task_runs)
    # Verify artifacts
    artifacts = conn.execute(
        "SELECT * FROM artifacts WHERE run_id = %s ORDER BY version",
        (runs[0]["run_id"],)
    ).fetchall()
    assert len(artifacts) == 3
    types = {a["artifact_type"] for a in artifacts}
    assert "INITIAL_BRIEF" in types
    assert "APPROVED_BRIEF" in types
    assert "SPEC_BUNDLE" in types


def test_spec_pipeline_rejected_at_gate(conn, config, project_id):
    io = StubGateIO(approve=False)
    import pytest
    with pytest.raises(PipelineAborted):
        run_spec_pipeline(
            raw_idea="Build something",
            config=config,
            conn=conn,
            project_id=project_id,
            io=io,
        )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd .worktrees/minimal-worker-loop && python -m pytest tests/integration/test_pipeline.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement pipeline.py**

```python
# src/ai_dev_system/pipeline.py
import json
import os
from pathlib import Path

from ai_dev_system.config import Config
from ai_dev_system.normalize import normalize_idea, validate_brief
from ai_dev_system.spec_bundle import generate_spec_bundle, validate_spec_bundle
from ai_dev_system.gate.core import run_gate_1
from ai_dev_system.agents.base import PromotedOutput
from ai_dev_system.db.repos.runs import RunRepo
from ai_dev_system.db.repos.task_runs import TaskRunRepo
from ai_dev_system.db.repos.events import EventRepo
from ai_dev_system.storage.paths import build_temp_path
from ai_dev_system.storage.promote import promote_output


class PipelineAborted(Exception):
    """User rejected at a gate."""


class ValidationError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(f"Validation failed: {errors}")


def run_spec_pipeline(raw_idea: str, config: Config, conn, project_id: str, io) -> "SpecBundle":
    """Full pipeline: normalize → gate 1 → spec bundle.
    Synchronous, blocking. Each step creates DB records.
    """
    run_repo = RunRepo(conn)
    task_run_repo = TaskRunRepo(conn)
    event_repo = EventRepo(conn)

    run_id = run_repo.create(project_id=project_id, pipeline_type="spec_pipeline")

    # Step 1: Normalize
    brief = normalize_idea(raw_idea)
    errors = validate_brief(brief)
    if errors:
        raise ValidationError(errors)

    task_run = task_run_repo.create_sync(run_id, task_type="normalize_idea")
    event_repo.insert(run_id, "TASK_STARTED", "pipeline", task_run["task_run_id"])
    temp_path = _write_json_to_temp(config, task_run, brief)
    promoted = PromotedOutput(name="initial_brief", artifact_type="INITIAL_BRIEF",
                              description="Normalized idea brief")
    promote_output(conn, config, task_run, promoted, temp_path)

    # Step 2: Gate 1
    task_run = task_run_repo.create_sync(run_id, task_type="human_gate")
    event_repo.insert(run_id, "TASK_STARTED", "pipeline", task_run["task_run_id"])

    result = run_gate_1(brief, io)

    if result.status == "rejected":
        task_run_repo.mark_failed(task_run["task_run_id"], "EXECUTION_ERROR", "user_rejected")
        raise PipelineAborted("User rejected brief at Gate 1")

    errors = validate_brief(result.brief)
    if errors:
        task_run_repo.mark_failed(task_run["task_run_id"], "EXECUTION_ERROR", str(errors))
        raise ValidationError(errors)

    temp_path = _write_json_to_temp(config, task_run, result.brief)
    promoted = PromotedOutput(name="approved_brief", artifact_type="APPROVED_BRIEF",
                              description="Human-approved brief")
    promote_output(conn, config, task_run, promoted, temp_path)

    # Step 3: Spec Bundle
    task_run = task_run_repo.create_sync(run_id, task_type="generate_spec")
    event_repo.insert(run_id, "TASK_STARTED", "pipeline", task_run["task_run_id"])

    temp_path = build_temp_path(config.storage_root, run_id,
                                task_run["task_id"], task_run["attempt_number"])
    bundle = generate_spec_bundle(result.brief, Path(temp_path))
    validate_spec_bundle(bundle.root_dir)  # warnings only, don't fail

    promoted = PromotedOutput(name="spec_bundle", artifact_type="SPEC_BUNDLE",
                              description="5-file spec bundle")
    promote_output(conn, config, task_run, promoted, temp_path)

    return bundle


def _write_json_to_temp(config: Config, task_run: dict, data: dict) -> str:
    """Write dict as JSON to temp path. Returns temp_path directory."""
    temp_path = build_temp_path(config.storage_root, task_run["run_id"],
                                task_run["task_id"], task_run["attempt_number"])
    os.makedirs(temp_path, exist_ok=True)
    with open(os.path.join(temp_path, f"{task_run['task_id']}.json"), "w") as f:
        json.dump(data, f, indent=2)
    return temp_path
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd .worktrees/minimal-worker-loop && python -m pytest tests/integration/test_pipeline.py -v
```

Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/pipeline.py tests/integration/test_pipeline.py
git commit -m "feat: spec pipeline runner — normalize → gate 1 → spec bundle (engine-integrated)"
```

---

### Task 6: CLIGateIO (interactive terminal)

**Files:**
- Create: `src/ai_dev_system/gate/cli.py`

- [ ] **Step 1: Implement CLIGateIO**

```python
# src/ai_dev_system/gate/cli.py
import copy


class CLIGateIO:
    """Interactive terminal Gate IO. Presents brief, allows field editing."""

    def present(self, brief: dict) -> None:
        print("\n=== Initial Brief ===")
        print(f"  Raw Idea (immutable): {brief['raw_idea']}")
        print(f"  Problem: {brief['problem'] or '(not specified)'}")
        print(f"  Target Users: {brief['target_users'] or '(not specified)'}")
        print(f"  Goal: {brief['goal'] or '(not specified)'}")
        hard = brief.get("constraints", {}).get("hard", [])
        soft = brief.get("constraints", {}).get("soft", [])
        print(f"  Hard Constraints: {hard or '(none)'}")
        print(f"  Soft Constraints: {soft or '(none)'}")
        scope = brief.get("scope", {})
        print(f"  Scope: type={scope.get('type')}, complexity={scope.get('complexity_hint')}")
        print(f"  Assumptions: {brief.get('assumptions', []) or '(none)'}")
        print(f"  Success Signals: {brief.get('success_signals', []) or '(none)'}")
        print()

    def collect_edit(self, brief: dict) -> dict:
        updated = copy.deepcopy(brief)
        print("Edit fields (press Enter to keep current, type new value to change):")
        for field in ["problem", "target_users", "goal"]:
            current = updated[field] or "(empty)"
            val = input(f"  {field} [{current}]: ").strip()
            if val:
                updated[field] = val
        # Scope
        val = input(f"  scope.type [{updated['scope']['type']}]: ").strip()
        if val:
            updated["scope"]["type"] = val
        val = input(f"  scope.complexity [{updated['scope']['complexity_hint']}]: ").strip()
        if val:
            updated["scope"]["complexity_hint"] = val
        return updated

    def confirm(self, brief: dict) -> bool:
        self.present(brief)
        answer = input("Confirm this brief? (y/n): ").strip().lower()
        return answer in ("y", "yes")
```

- [ ] **Step 2: Commit** (no automated test — this is interactive IO)

```bash
git add src/ai_dev_system/gate/cli.py
git commit -m "feat: CLIGateIO — interactive terminal for Gate 1"
```

---

## Phase B: Task Graph Generator

### Task 7: Skeleton builder

**Files:**
- Create: `src/ai_dev_system/task_graph/__init__.py`
- Create: `src/ai_dev_system/task_graph/skeleton.py`
- Create: `tests/unit/test_skeleton.py`

- [ ] **Step 1: Write failing test — skeleton always has 4 nodes**

```python
# tests/unit/test_skeleton.py
from ai_dev_system.task_graph.skeleton import build_skeleton, CORE_SKELETON


def test_skeleton_has_4_nodes():
    graph = build_skeleton()
    assert len(graph) == 4


def test_skeleton_correct_ids():
    graph = build_skeleton()
    ids = {t["id"] for t in graph}
    assert ids == {"TASK-PARSE", "TASK-DESIGN", "TASK-IMPL", "TASK-VALIDATE"}


def test_skeleton_correct_deps():
    graph = build_skeleton()
    by_id = {t["id"]: t for t in graph}
    assert by_id["TASK-PARSE"]["deps"] == []
    assert by_id["TASK-DESIGN"]["deps"] == ["TASK-PARSE"]
    assert by_id["TASK-IMPL"]["deps"] == ["TASK-DESIGN"]
    assert by_id["TASK-VALIDATE"]["deps"] == ["TASK-IMPL"]


def test_skeleton_all_atomic():
    graph = build_skeleton()
    assert all(t["execution_type"] == "atomic" for t in graph)


def test_skeleton_returns_deep_copy():
    g1 = build_skeleton()
    g2 = build_skeleton()
    g1[0]["title"] = "mutated"
    assert g2[0]["title"] != "mutated"
    assert CORE_SKELETON[0]["title"] != "mutated"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd .worktrees/minimal-worker-loop && python -m pytest tests/unit/test_skeleton.py -v
```

- [ ] **Step 3: Implement skeleton.py**

Implement `CORE_SKELETON` and `build_skeleton()` exactly as defined in the spec (Section 4). All 4 nodes with full field set.

- [ ] **Step 4: Run test to verify it passes**

```bash
cd .worktrees/minimal-worker-loop && python -m pytest tests/unit/test_skeleton.py -v
```

Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/task_graph/ tests/unit/test_skeleton.py
git commit -m "feat: task graph skeleton — invariant 4-phase backbone"
```

---

### Task 8: Graph mutation primitives (add_parallel, add_before, add_after)

**Files:**
- Create: `src/ai_dev_system/task_graph/rules.py` (primitives only, rules come next)
- Create: `tests/unit/test_rules.py`

- [ ] **Step 1: Write failing tests — primitives**

```python
# tests/unit/test_rules.py
import pytest
from ai_dev_system.task_graph.skeleton import build_skeleton
from ai_dev_system.task_graph.rules import add_parallel, add_before, add_after, _find


def test_add_before_inserts_node():
    graph = build_skeleton()
    graph, changed = add_before(graph, "TASK-IMPL", {
        "id": "TASK-DESIGN.SCHEMA", "title": "Schema", "phase": "design_solution",
        "group": "design_phase", "execution_type": "atomic", "type": "design",
        "agent_type": "DBA", "required_inputs": [], "expected_outputs": [],
        "done_definition": "done", "enriched_by": "rule", "created_by_rule": "R1",
    })
    assert changed is True
    by_id = {t["id"]: t for t in graph}
    assert "TASK-DESIGN.SCHEMA" in by_id
    assert by_id["TASK-DESIGN.SCHEMA"]["deps"] == ["TASK-DESIGN"]  # old IMPL deps
    assert by_id["TASK-IMPL"]["deps"] == ["TASK-DESIGN.SCHEMA"]  # rewired


def test_add_parallel_makes_composite():
    graph = build_skeleton()
    graph, changed = add_parallel(graph, "TASK-IMPL", [
        {"id": "TASK-IMPL.A", "title": "A", "phase": "implement", "group": "g",
         "execution_type": "atomic", "type": "coding", "agent_type": "Dev",
         "required_inputs": [], "expected_outputs": [], "done_definition": "d",
         "enriched_by": "rule", "created_by_rule": "R"},
        {"id": "TASK-IMPL.B", "title": "B", "phase": "implement", "group": "g",
         "execution_type": "atomic", "type": "coding", "agent_type": "Dev",
         "required_inputs": [], "expected_outputs": [], "done_definition": "d",
         "enriched_by": "rule", "created_by_rule": "R"},
    ])
    assert changed is True
    by_id = {t["id"]: t for t in graph}
    assert by_id["TASK-IMPL"]["execution_type"] == "composite"
    # Children inherit parent's deps
    assert by_id["TASK-IMPL.A"]["deps"] == ["TASK-DESIGN"]
    assert by_id["TASK-IMPL.B"]["deps"] == ["TASK-DESIGN"]
    # Downstream still depends on composite parent
    assert by_id["TASK-VALIDATE"]["deps"] == ["TASK-IMPL"]


def test_add_parallel_rejects_already_composite():
    graph = build_skeleton()
    graph, _ = add_parallel(graph, "TASK-IMPL", [
        {"id": "TASK-IMPL.A", "title": "A", "phase": "implement", "group": "g",
         "execution_type": "atomic", "type": "coding", "agent_type": "Dev",
         "required_inputs": [], "expected_outputs": [], "done_definition": "d",
         "enriched_by": "rule", "created_by_rule": "R"},
    ])
    with pytest.raises(ValueError, match="already-composite"):
        add_parallel(graph, "TASK-IMPL", [
            {"id": "TASK-IMPL.C", "title": "C", "phase": "implement", "group": "g",
             "execution_type": "atomic", "type": "coding", "agent_type": "Dev",
             "required_inputs": [], "expected_outputs": [], "done_definition": "d",
             "enriched_by": "rule", "created_by_rule": "R"},
        ])


def test_add_after_on_atomic():
    graph = build_skeleton()
    graph, changed = add_after(graph, "TASK-IMPL", {
        "id": "TASK-IMPL.PERF", "title": "Perf", "phase": "implement",
        "group": "g", "execution_type": "atomic", "type": "testing",
        "agent_type": "QA", "required_inputs": [], "expected_outputs": [],
        "done_definition": "d", "enriched_by": "rule", "created_by_rule": "R",
    })
    assert changed is True
    by_id = {t["id"]: t for t in graph}
    assert by_id["TASK-IMPL.PERF"]["deps"] == ["TASK-IMPL"]
    assert by_id["TASK-VALIDATE"]["deps"] == ["TASK-IMPL.PERF"]


def test_add_after_on_composite():
    graph = build_skeleton()
    # First make IMPL composite
    graph, _ = add_parallel(graph, "TASK-IMPL", [
        {"id": "TASK-IMPL.A", "title": "A", "phase": "implement", "group": "g",
         "execution_type": "atomic", "type": "coding", "agent_type": "Dev",
         "required_inputs": [], "expected_outputs": [], "done_definition": "d",
         "enriched_by": "rule", "created_by_rule": "R"},
        {"id": "TASK-IMPL.B", "title": "B", "phase": "implement", "group": "g",
         "execution_type": "atomic", "type": "coding", "agent_type": "Dev",
         "required_inputs": [], "expected_outputs": [], "done_definition": "d",
         "enriched_by": "rule", "created_by_rule": "R"},
    ])
    # add_after on composite should attach to leaf children
    graph, _ = add_after(graph, "TASK-IMPL", {
        "id": "TASK-IMPL.PERF", "title": "Perf", "phase": "implement",
        "group": "g", "execution_type": "atomic", "type": "testing",
        "agent_type": "QA", "required_inputs": [], "expected_outputs": [],
        "done_definition": "d", "enriched_by": "rule", "created_by_rule": "R",
    })
    by_id = {t["id"]: t for t in graph}
    assert set(by_id["TASK-IMPL.PERF"]["deps"]) == {"TASK-IMPL.A", "TASK-IMPL.B"}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd .worktrees/minimal-worker-loop && python -m pytest tests/unit/test_rules.py -v
```

- [ ] **Step 3: Implement primitives in rules.py**

Implement `add_parallel`, `add_before`, `add_after`, `_find` exactly as in the spec (Section 5, Graph Mutation Primitives). All return `(graph, True)`. No-change paths return `(graph, False)`.

- [ ] **Step 4: Run test to verify it passes**

```bash
cd .worktrees/minimal-worker-loop && python -m pytest tests/unit/test_rules.py -v
```

Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/task_graph/rules.py tests/unit/test_rules.py
git commit -m "feat: graph mutation primitives — add_parallel/before/after"
```

---

### Task 9: Rule engine (3 rules + apply_rules)

**Files:**
- Modify: `src/ai_dev_system/task_graph/rules.py` (add rules)
- Append to: `tests/unit/test_rules.py`

- [ ] **Step 1: Write failing tests — rules fire correctly**

```python
# tests/unit/test_rules.py (append)
from ai_dev_system.task_graph.rules import apply_rules


def test_rule_database_fires():
    graph = build_skeleton()
    spec = {"constraints": {"hard": ["Must use PostgreSQL"], "soft": []},
            "scope": {"type": "unknown"}, "success_signals": []}
    graph, applied = apply_rules(graph, spec)
    assert "RULE-DATABASE" in applied
    ids = {t["id"] for t in graph}
    assert "TASK-DESIGN.SCHEMA" in ids


def test_rule_product_split_fires():
    graph = build_skeleton()
    spec = {"constraints": {"hard": [], "soft": []},
            "scope": {"type": "product"}, "success_signals": []}
    graph, applied = apply_rules(graph, spec)
    assert "RULE-PRODUCT-SPLIT" in applied
    by_id = {t["id"]: t for t in graph}
    assert "TASK-IMPL.BACKEND" in by_id
    assert "TASK-IMPL.FRONTEND" in by_id
    assert by_id["TASK-IMPL"]["execution_type"] == "composite"


def test_rule_perf_fires():
    graph = build_skeleton()
    spec = {"constraints": {"hard": [], "soft": []},
            "scope": {"type": "unknown"},
            "success_signals": ["Search latency < 5s"]}
    graph, applied = apply_rules(graph, spec)
    assert "RULE-PERF" in applied
    ids = {t["id"] for t in graph}
    assert "TASK-IMPL.PERF" in ids


def test_no_rules_fire_on_minimal_spec():
    graph = build_skeleton()
    spec = {"constraints": {"hard": [], "soft": []},
            "scope": {"type": "unknown"}, "success_signals": []}
    graph, applied = apply_rules(graph, spec)
    assert applied == []
    assert len(graph) == 4


def test_database_and_product_split_combined():
    """Database rule runs first, then product split inherits correct deps."""
    graph = build_skeleton()
    spec = {"constraints": {"hard": ["database required"], "soft": []},
            "scope": {"type": "product"}, "success_signals": []}
    graph, applied = apply_rules(graph, spec)
    assert "RULE-DATABASE" in applied
    assert "RULE-PRODUCT-SPLIT" in applied
    by_id = {t["id"]: t for t in graph}
    # Children should inherit TASK-DESIGN.SCHEMA dep (from IMPL after database rule)
    assert by_id["TASK-IMPL.BACKEND"]["deps"] == ["TASK-DESIGN.SCHEMA"]
    assert by_id["TASK-IMPL.FRONTEND"]["deps"] == ["TASK-DESIGN.SCHEMA"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd .worktrees/minimal-worker-loop && python -m pytest tests/unit/test_rules.py -v -k "rule_"
```

- [ ] **Step 3: Add rules to rules.py**

Add `rule_product_split`, `rule_database`, `rule_performance`, `RULES`, `apply_rules` exactly as in the spec (Section 5). Rule ordering: DATABASE → PRODUCT-SPLIT → PERF.

- [ ] **Step 4: Run test to verify it passes**

```bash
cd .worktrees/minimal-worker-loop && python -m pytest tests/unit/test_rules.py -v
```

Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/task_graph/rules.py tests/unit/test_rules.py
git commit -m "feat: rule engine — 3 deterministic rules + apply_rules"
```

---

### Task 10: Graph validator

**Files:**
- Create: `src/ai_dev_system/task_graph/validator.py`
- Create: `tests/unit/test_validator.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_validator.py
from ai_dev_system.task_graph.skeleton import build_skeleton
from ai_dev_system.task_graph.rules import add_parallel
from ai_dev_system.task_graph.validator import validate_graph


def test_valid_skeleton():
    assert validate_graph(build_skeleton()) == []


def test_missing_core_node():
    graph = build_skeleton()
    graph = [t for t in graph if t["id"] != "TASK-PARSE"]
    errors = validate_graph(graph)
    assert any("Missing core" in e for e in errors)


def test_unknown_dep():
    graph = build_skeleton()
    graph[1]["deps"] = ["NONEXISTENT"]
    errors = validate_graph(graph)
    assert any("unknown" in e for e in errors)


def test_duplicate_id():
    graph = build_skeleton()
    graph.append(dict(graph[0]))  # duplicate TASK-PARSE
    errors = validate_graph(graph)
    assert any("Duplicate" in e for e in errors)


def test_cycle_detection():
    graph = build_skeleton()
    # Create cycle: PARSE → VALIDATE → PARSE
    graph[0]["deps"] = ["TASK-VALIDATE"]
    errors = validate_graph(graph)
    assert any("cycle" in e.lower() for e in errors)


def test_composite_without_children():
    graph = build_skeleton()
    graph[2]["execution_type"] = "composite"  # TASK-IMPL
    errors = validate_graph(graph)
    assert any("no children" in e.lower() for e in errors)


def test_valid_composite_with_children():
    graph = build_skeleton()
    graph, _ = add_parallel(graph, "TASK-IMPL", [
        {"id": "TASK-IMPL.A", "title": "A", "phase": "implement", "group": "g",
         "execution_type": "atomic", "type": "coding", "agent_type": "Dev",
         "required_inputs": [], "expected_outputs": [], "done_definition": "d",
         "enriched_by": "rule", "created_by_rule": "R"},
    ])
    assert validate_graph(graph) == []
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd .worktrees/minimal-worker-loop && python -m pytest tests/unit/test_validator.py -v
```

- [ ] **Step 3: Implement validator.py**

Implement `validate_graph`, `_has_cycle`, `CORE_IDS`, `REQUIRED_FIELDS` as in the spec (Section 7).

**Important fix**: The required field check must use `field not in task` (key existence), NOT `not task.get(field)` (truthiness). The spec's version would falsely flag `deps: []` as missing because `not []` is `True`.

```python
    # All tasks have required fields — check key existence, not truthiness
    for task in graph:
        for field in REQUIRED_FIELDS:
            if field not in task:
                errors.append(f"{task['id']} missing required field: {field}")
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd .worktrees/minimal-worker-loop && python -m pytest tests/unit/test_validator.py -v
```

Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/task_graph/validator.py tests/unit/test_validator.py
git commit -m "feat: graph validator — cycles, deps, core presence, composite checks"
```

---

### Task 11: LLM enricher (with mock)

**Files:**
- Create: `src/ai_dev_system/task_graph/enricher.py`
- Create: `tests/unit/test_enricher.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_enricher.py
import json
from ai_dev_system.task_graph.skeleton import build_skeleton
from ai_dev_system.task_graph.enricher import enrich_task, enrich_all, ENRICHABLE_FIELDS


class MockLLM:
    def __init__(self, response: str):
        self.response = response
        self.calls = []

    def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        return self.response


def test_enrich_task_fills_content():
    task = build_skeleton()[0]  # TASK-PARSE
    llm = MockLLM(json.dumps({
        "title": "Parse forum spec",
        "objective": "Extract forum requirements",
        "description": "Detailed parsing",
        "done_definition": "All parsed",
        "verification_steps": ["Check A", "Check B"],
    }))
    spec = {"problem.md": "forum problem", "requirements.md": "forum reqs", "constraints.md": "pg"}
    result = enrich_task(task, spec, llm)
    assert result["title"] == "Parse forum spec"
    assert result["llm_enriched"] is True
    assert result["enriched_by"] == "llm"
    assert len(llm.calls) == 1


def test_enrich_task_rejects_structure_fields():
    task = build_skeleton()[0]
    llm = MockLLM(json.dumps({
        "title": "New title",
        "id": "HACKED",  # should be stripped
        "deps": ["HACKED"],  # should be stripped
    }))
    spec = {"problem.md": "x"}
    result = enrich_task(task, spec, llm)
    assert result["id"] == "TASK-PARSE"  # unchanged
    assert result["deps"] == []  # unchanged


def test_enrich_task_fallback_on_error():
    task = build_skeleton()[0]
    original_title = task["title"]
    llm = MockLLM("not valid json {{{{")
    spec = {"problem.md": "x"}
    result = enrich_task(task, spec, llm)
    assert result["title"] == original_title  # unchanged
    assert result["llm_enriched"] is False


def test_enrich_all_skips_composite():
    graph = build_skeleton()
    graph[2]["execution_type"] = "composite"
    llm = MockLLM(json.dumps({"title": "enriched"}))
    spec = {"problem.md": "x"}
    enrich_all(graph, spec, llm)
    assert graph[2]["llm_enriched"] is False  # composite skipped
    assert graph[0]["llm_enriched"] is True  # atomic enriched


def test_enrich_all_noop_without_llm():
    graph = build_skeleton()
    result = enrich_all(graph, {}, None)
    assert all(t["llm_enriched"] is False for t in result)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd .worktrees/minimal-worker-loop && python -m pytest tests/unit/test_enricher.py -v
```

- [ ] **Step 3: Implement enricher.py**

```python
# src/ai_dev_system/task_graph/enricher.py
import json
from typing import Protocol, Optional


class LLMClient(Protocol):
    def complete(self, prompt: str) -> str: ...


ENRICHABLE_FIELDS = {"title", "objective", "description",
                     "done_definition", "verification_steps"}


def enrich_task(task: dict, spec_content: dict[str, str], llm: LLMClient) -> dict:
    """Enrich a single task. Structure fields immutable."""
    prompt = _build_prompt(task, spec_content)
    try:
        response = llm.complete(prompt)
        enrichment = json.loads(response)
        if not isinstance(enrichment, dict):
            return task
    except (json.JSONDecodeError, Exception):
        return task

    for key in list(enrichment.keys()):
        if key not in ENRICHABLE_FIELDS:
            del enrichment[key]

    task.update(enrichment)
    task["llm_enriched"] = True
    task["enriched_by"] = "llm"
    return task


def enrich_all(graph: list[dict], spec_content: dict[str, str],
               llm: Optional[LLMClient] = None) -> list[dict]:
    """Enrich all atomic tasks. Skip composite. No-op if llm is None."""
    if llm is None:
        return graph
    for task in graph:
        if task["execution_type"] == "atomic":
            enrich_task(task, spec_content, llm)
    return graph


def _build_prompt(task: dict, spec_content: dict[str, str]) -> str:
    problem = spec_content.get("problem.md", "")[:800]
    requirements = spec_content.get("requirements.md", "")[:800]
    constraints = spec_content.get("constraints.md", "")[:800]
    return f"""You are enriching a task in a software development execution plan.

## Task Context
- ID: {task['id']}
- Phase: {task['phase']}
- Type: {task['type']}
- Current title: {task['title']}
- Agent: {task['agent_type']}
- Inputs: {task['required_inputs']}
- Outputs: {task['expected_outputs']}

## Project Spec
### Problem
{problem}

### Requirements
{requirements}

### Constraints
{constraints}

## Instructions
Enrich this task with project-specific details. Return ONLY valid JSON:

```json
{{
  "title": "specific title mentioning the actual project",
  "objective": "1-2 sentences",
  "description": "detailed description with project-specific context",
  "done_definition": "measurable completion criteria",
  "verification_steps": ["step 1", "step 2", "step 3"]
}}
```

Rules:
- Be specific to THIS project
- done_definition must be measurable
- verification_steps must be actionable
- Do NOT add or reference tasks, dependencies, or execution structure"""
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd .worktrees/minimal-worker-loop && python -m pytest tests/unit/test_enricher.py -v
```

Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/task_graph/enricher.py tests/unit/test_enricher.py
git commit -m "feat: LLM enricher with guardrails — content only, fallback on error"
```

---

### Task 12: Generator orchestrator + Gate 2

**Files:**
- Create: `src/ai_dev_system/task_graph/generator.py`
- Create: `src/ai_dev_system/gate/gate2.py`
- Create: `src/ai_dev_system/gate/stub_gate2.py`
- Create: `tests/integration/test_generator.py`

- [ ] **Step 1: Write failing integration test — full generator**

```python
# tests/integration/test_generator.py
from ai_dev_system.task_graph.generator import generate_task_graph, GraphValidationError
from ai_dev_system.gate.gate2 import run_gate_2
from ai_dev_system.gate.stub_gate2 import StubGate2IO


def test_generate_minimal_graph():
    spec = {"problem.md": "test", "requirements.md": "test", "constraints.md": "test",
            "success_criteria.md": "test", "assumptions.md": "test"}
    brief = {"constraints": {"hard": [], "soft": []},
             "scope": {"type": "unknown"}, "success_signals": []}
    envelope = generate_task_graph(spec, brief, "artifact-123")
    assert envelope["graph_version"] == 1
    assert len(envelope["tasks"]) == 4
    assert envelope["rules_applied"] == []
    assert envelope["llm_enriched"] is False


def test_generate_with_rules():
    spec = {"problem.md": "forum", "requirements.md": "reqs", "constraints.md": "pg"}
    brief = {"constraints": {"hard": ["Must use PostgreSQL"], "soft": []},
             "scope": {"type": "product"}, "success_signals": []}
    envelope = generate_task_graph(spec, brief, "artifact-123")
    assert "RULE-DATABASE" in envelope["rules_applied"]
    assert "RULE-PRODUCT-SPLIT" in envelope["rules_applied"]
    ids = {t["id"] for t in envelope["tasks"]}
    assert "TASK-DESIGN.SCHEMA" in ids
    assert "TASK-IMPL.BACKEND" in ids
    assert "TASK-IMPL.FRONTEND" in ids


def test_gate2_approve():
    envelope = generate_task_graph({}, {"constraints": {"hard": [], "soft": []},
                                        "scope": {"type": "unknown"}, "success_signals": []},
                                   "a-123")
    io = StubGate2IO(action="approve")
    result = run_gate_2(envelope, io)
    assert result.status == "approved"
    assert result.graph == envelope


def test_gate2_reject():
    envelope = generate_task_graph({}, {"constraints": {"hard": [], "soft": []},
                                        "scope": {"type": "unknown"}, "success_signals": []},
                                   "a-123")
    io = StubGate2IO(action="reject")
    result = run_gate_2(envelope, io)
    assert result.status == "rejected"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd .worktrees/minimal-worker-loop && python -m pytest tests/integration/test_generator.py -v
```

- [ ] **Step 3: Implement generator.py**

```python
# src/ai_dev_system/task_graph/generator.py
from datetime import datetime, timezone

from ai_dev_system.task_graph.skeleton import build_skeleton
from ai_dev_system.task_graph.rules import apply_rules
from ai_dev_system.task_graph.enricher import enrich_all
from ai_dev_system.task_graph.validator import validate_graph


class GraphValidationError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(f"Graph validation failed: {errors}")


def generate_task_graph(
    spec_bundle_content: dict[str, str],
    approved_brief: dict,
    spec_artifact_id: str,
    llm=None,
) -> dict:
    """Full pipeline: skeleton → rules → enrich → validate → envelope."""
    graph = build_skeleton()
    graph, rules_applied = apply_rules(graph, approved_brief)
    graph = enrich_all(graph, spec_bundle_content, llm)

    errors = validate_graph(graph)
    if errors:
        raise GraphValidationError(errors)

    return {
        "graph_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "spec_bundle_version": 1,
        "source_spec_artifact_id": spec_artifact_id,
        "generator_version": "1.0.0",
        "rules_applied": rules_applied,
        "llm_enriched": llm is not None,
        "tasks": graph,
    }
```

- [ ] **Step 4: Implement Gate 2**

```python
# src/ai_dev_system/gate/gate2.py
from dataclasses import dataclass
from typing import Literal, Protocol


class Gate2IO(Protocol):
    def present_graph(self, graph_envelope: dict) -> None: ...
    def collect_edits(self, graph_envelope: dict) -> tuple[Literal["approve", "reject"], dict]: ...


@dataclass
class Gate2Result:
    status: Literal["approved", "rejected"]
    graph: dict


def run_gate_2(graph_envelope: dict, io: Gate2IO) -> Gate2Result:
    io.present_graph(graph_envelope)
    action, edited = io.collect_edits(graph_envelope)
    if action == "approve":
        return Gate2Result(status="approved", graph=edited)
    return Gate2Result(status="rejected", graph=graph_envelope)
```

```python
# src/ai_dev_system/gate/stub_gate2.py


class StubGate2IO:
    """Test double for Gate 2."""

    def __init__(self, action: str = "approve", edits: dict | None = None):
        self.action = action
        self.edits = edits
        self.presented = None

    def present_graph(self, graph_envelope: dict) -> None:
        self.presented = graph_envelope

    def collect_edits(self, graph_envelope: dict) -> tuple[str, dict]:
        if self.edits:
            graph_envelope.update(self.edits)
        return self.action, graph_envelope
```

- [ ] **Step 5: Run test to verify it passes**

```bash
cd .worktrees/minimal-worker-loop && python -m pytest tests/integration/test_generator.py -v
```

Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add src/ai_dev_system/task_graph/generator.py src/ai_dev_system/gate/gate2.py src/ai_dev_system/gate/stub_gate2.py tests/integration/test_generator.py
git commit -m "feat: task graph generator orchestrator + Gate 2"
```

---

### Task 13: Full end-to-end integration test

**Files:**
- Create: `tests/integration/test_pipeline_full.py`

- [ ] **Step 1: Write full pipeline test — raw idea → approved task graph**

```python
# tests/integration/test_pipeline_full.py
from ai_dev_system.pipeline import run_spec_pipeline
from ai_dev_system.gate.stub import StubGateIO
from ai_dev_system.gate.stub_gate2 import StubGate2IO
from ai_dev_system.task_graph.generator import generate_task_graph
from ai_dev_system.gate.gate2 import run_gate_2
from ai_dev_system.task_graph.validator import validate_graph


def test_full_pipeline_idea_to_task_graph(conn, config, project_id):
    """End-to-end: raw idea → spec bundle → task graph → approved graph."""
    # Phase A: Spec Pipeline
    gate1_io = StubGateIO(
        edits={
            "problem": "No internal knowledge sharing",
            "goal": "Forum for developers",
            "target_users": "Internal team (~50)",
            "constraints": {"hard": ["Must use PostgreSQL"], "soft": ["Prefer Python"]},
            "scope": {"type": "product", "complexity_hint": "medium"},
            "success_signals": ["Search results in < 5s"],
        },
        approve=True,
    )
    bundle = run_spec_pipeline(
        raw_idea="Build a forum for sharing knowledge",
        config=config, conn=conn, project_id=project_id, io=gate1_io,
    )

    # Read spec files back
    spec_content = {}
    for filename, path in bundle.files.items():
        spec_content[filename] = path.read_text()

    # Phase B: Task Graph
    brief = gate1_io.edits  # simplified — in real pipeline, read from artifact
    brief["constraints"] = {"hard": ["Must use PostgreSQL"], "soft": ["Prefer Python"]}
    brief["scope"] = {"type": "product", "complexity_hint": "medium"}
    brief["success_signals"] = ["Search results in < 5s"]

    envelope = generate_task_graph(spec_content, brief, "test-artifact-id")
    assert len(envelope["rules_applied"]) >= 2  # DATABASE + PRODUCT-SPLIT
    assert validate_graph(envelope["tasks"]) == []

    # Gate 2: Approve
    gate2_io = StubGate2IO(action="approve")
    result = run_gate_2(envelope, gate2_io)
    assert result.status == "approved"

    # Verify graph structure
    ids = {t["id"] for t in result.graph["tasks"]}
    assert "TASK-PARSE" in ids
    assert "TASK-DESIGN" in ids
    assert "TASK-DESIGN.SCHEMA" in ids
    assert "TASK-IMPL" in ids
    assert "TASK-IMPL.BACKEND" in ids
    assert "TASK-IMPL.FRONTEND" in ids
    assert "TASK-VALIDATE" in ids
```

- [ ] **Step 2: Run test**

```bash
cd .worktrees/minimal-worker-loop && python -m pytest tests/integration/test_pipeline_full.py -v
```

Expected: 1 passed

- [ ] **Step 3: Run full test suite**

```bash
cd .worktrees/minimal-worker-loop && python -m pytest -v
```

Expected: all tests pass

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_pipeline_full.py
git commit -m "test: full end-to-end — raw idea → spec bundle → task graph → approved"
```

---

### Task 14: create_from_graph + read_spec_bundle utilities

**Files:**
- Modify: `src/ai_dev_system/db/repos/task_runs.py`
- Create: `src/ai_dev_system/utils.py`
- Create: `tests/integration/test_create_from_graph.py`

- [ ] **Step 1: Write failing test — create_from_graph creates task_runs from graph nodes**

```python
# tests/integration/test_create_from_graph.py
from ai_dev_system.db.repos.runs import RunRepo
from ai_dev_system.db.repos.task_runs import TaskRunRepo
from ai_dev_system.task_graph.generator import generate_task_graph


def test_create_from_graph(conn, project_id):
    run_repo = RunRepo(conn)
    run_id = run_repo.create(project_id=project_id, pipeline_type="test")
    task_run_repo = TaskRunRepo(conn)

    brief = {"constraints": {"hard": [], "soft": []},
             "scope": {"type": "unknown"}, "success_signals": []}
    envelope = generate_task_graph({}, brief, "art-123")

    # Only create task_runs for atomic tasks
    created = []
    for task in envelope["tasks"]:
        if task["execution_type"] == "atomic":
            tr_id = task_run_repo.create_from_graph(
                run_id=run_id, task=task, task_graph_artifact_id="art-123")
            created.append(tr_id)

    assert len(created) == 4  # all 4 core nodes are atomic
    # Verify DB records
    rows = conn.execute(
        "SELECT * FROM task_runs WHERE run_id = %s AND task_graph_artifact_id = %s",
        (run_id, "art-123")
    ).fetchall()
    assert len(rows) == 4
    assert all(r["status"] == "PENDING" for r in rows)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd .worktrees/minimal-worker-loop && python -m pytest tests/integration/test_create_from_graph.py -v
```

- [ ] **Step 3: Implement create_from_graph**

Add to `src/ai_dev_system/db/repos/task_runs.py`:

```python
    def create_from_graph(self, run_id: str, task: dict, task_graph_artifact_id: str) -> str:
        """Create a PENDING task_run from a graph node. For execution engine."""
        task_run_id = str(uuid.uuid4())
        deps = task.get("deps", [])
        self.conn.execute("""
            INSERT INTO task_runs (
                task_run_id, run_id, task_id, task_graph_artifact_id,
                attempt_number, status, agent_type,
                input_artifact_ids, resolved_dependencies, promoted_outputs
            ) VALUES (%s, %s, %s, %s, 1, 'PENDING', %s, '{}', %s, '[]')
        """, (task_run_id, run_id, task["id"], task_graph_artifact_id,
              task.get("agent_type", "unknown"),
              psycopg.types.json.Jsonb(deps)))
        return task_run_id
```

- [ ] **Step 4: Implement read_spec_bundle utility**

```python
# src/ai_dev_system/utils.py
import os
from ai_dev_system.spec_bundle import REQUIRED_FILES


def read_spec_bundle(content_ref: str) -> dict[str, str]:
    """Read spec bundle files from promoted artifact path.
    Returns {"problem.md": "content", ...}."""
    result = {}
    for filename in REQUIRED_FILES:
        path = os.path.join(content_ref, filename)
        if os.path.exists(path):
            with open(path) as f:
                result[filename] = f.read()
        else:
            result[filename] = ""
    return result
```

- [ ] **Step 5: Run test to verify it passes**

```bash
cd .worktrees/minimal-worker-loop && python -m pytest tests/integration/test_create_from_graph.py -v
```

Expected: 1 passed

- [ ] **Step 6: Run full test suite**

```bash
cd .worktrees/minimal-worker-loop && python -m pytest -v
```

Expected: all tests pass

- [ ] **Step 7: Commit**

```bash
git add src/ai_dev_system/db/repos/task_runs.py src/ai_dev_system/utils.py tests/integration/test_create_from_graph.py
git commit -m "feat: create_from_graph + read_spec_bundle — bridge graph to execution engine"
```
