# Spec Pipeline — Thin Vertical Slice Design

> Date: 2026-03-29
> Status: Draft
> Goal: First closable loop — user inputs raw idea, system outputs structured spec bundle

---

## 1. Overview

Build a minimal, working pipeline:

```
raw idea → normalize → human confirm (Gate 1) → spec bundle
```

Three components, synchronous execution, integrated with existing execution engine (run → task_run → artifact → event). No worker loop, no AI, no debate — just structure and human confirmation.

### Design Principles

- **Thin slice**: each component does the minimum to close the loop
- **Engine-integrated from day 1**: every step creates run/task_run/artifact records — no standalone file-only mode
- **IO abstraction**: Gate 1 logic decoupled from presentation (CLI today, API/MCP later)
- **Strict contracts**: schema is an intermediate representation (IR) for downstream AI reasoning — no ambiguity, no redundancy, clear semantic boundaries

---

## 2. Artifact Types

The existing `ARTIFACT_TYPE_TO_KEY` in `storage/paths.py` already contains `INITIAL_BRIEF` and `SPEC_BUNDLE`. Add the missing type:

```python
# In storage/paths.py — add to ARTIFACT_TYPE_TO_KEY:
"APPROVED_BRIEF": "approved_brief_id",
```

Artifact type strings are **UPPERCASE** throughout the system (matching DB enum and `ARTIFACT_TYPE_TO_KEY` keys). All code in this spec uses uppercase: `"INITIAL_BRIEF"`, `"APPROVED_BRIEF"`, `"SPEC_BUNDLE"`.

The `runs.current_artifacts` JSONB column must also have the `approved_brief_id` key added (via migration or seed).

---

## 3. Component A: Normalize

**Purpose**: Convert raw text into a structured brief. No AI — just create the skeleton for human to fill.

**Input**: `raw_text: str`
**Output**: `initial_brief.json` (promoted as artifact)

### Contract: `initial_brief.json`

```json
{
  "id": "uuid",
  "version": 1,
  "raw_idea": "string — required, non-empty",

  "problem": "",
  "target_users": "",
  "goal": "",

  "constraints": {
    "hard": [],
    "soft": []
  },

  "assumptions": [],

  "scope": {
    "type": "unknown",
    "complexity_hint": "unknown"
  },

  "success_signals": []
}
```

### Validation Rules

| Field | Rule |
|-------|------|
| `id` | UUID, required |
| `version` | int >= 1, required |
| `raw_idea` | non-empty string, required (only MUST-have field) |
| `problem`, `target_users`, `goal` | string, may be empty |
| `constraints.hard`, `constraints.soft` | list[str], may be empty |
| `assumptions` | list[str], may be empty |
| `scope.type` | one of: `product`, `feature`, `experiment`, `unknown` |
| `scope.complexity_hint` | one of: `low`, `medium`, `high`, `unknown` |
| `success_signals` | list[str], may be empty |
| No extra keys | strict schema enforcement |

### Implementation

```python
# src/ai_dev_system/normalize.py

from uuid import uuid4

SCOPE_TYPES = {"product", "feature", "experiment", "unknown"}
COMPLEXITY_HINTS = {"low", "medium", "high", "unknown"}

def normalize_idea(raw_text: str) -> dict:
    """Parse raw text into structured brief skeleton."""
    if not raw_text.strip():
        raise ValueError("raw_idea must be non-empty")
    return {
        "id": str(uuid4()),
        "version": 1,
        "raw_idea": raw_text.strip(),
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
    allowed = {"id", "version", "raw_idea", "problem", "target_users", "goal",
               "constraints", "assumptions", "scope", "success_signals"}
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

---

## 4. Component C: Gate 1 — Human Confirmation

**Purpose**: Present brief to human, let them edit, confirm. Decoupled from IO.

**Input**: `initial_brief.json` content
**Output**: `approved_brief.json` (same schema, human-edited)

### GateIO Protocol

```python
# src/ai_dev_system/gate/interface.py
from typing import Protocol

class GateIO(Protocol):
    def present(self, brief: dict) -> None:
        """Display the brief to the user."""
        ...

    def collect_edit(self, brief: dict) -> dict:
        """Let user edit fields. Returns updated brief."""
        ...

    def confirm(self, brief: dict) -> bool:
        """Show final brief, ask for confirmation. Returns True if approved."""
        ...
```

### Gate Core Logic

```python
# src/ai_dev_system/gate/core.py
from dataclasses import dataclass
from typing import Literal

@dataclass
class GateResult:
    status: Literal["approved", "rejected"]
    brief: dict

def run_gate_1(brief: dict, io: GateIO) -> GateResult:
    io.present(brief)
    updated = io.collect_edit(brief)
    if io.confirm(updated):
        return GateResult(status="approved", brief=updated)
    return GateResult(status="rejected", brief=brief)
```

### Flow

```
PRESENT          →  Show brief fields in readable format
                    raw_idea shown as "Original Input (immutable)"
COLLECT_EDIT     →  User edits fields (or says "ok" to skip)
CONFIRM          →  Show updated brief, ask "Confirm? (y/n)"
                    "y" → approved
                    "n" → rejected (pipeline aborts)
```

### Implementations

**CLIGateIO** — for interactive terminal use:

```python
# src/ai_dev_system/gate/cli.py

class CLIGateIO:
    def present(self, brief: dict) -> None:
        print("=== Initial Brief ===")
        print(f"Raw Idea (immutable): {brief['raw_idea']}")
        print(f"Problem: {brief['problem'] or '(not specified)'}")
        print(f"Target Users: {brief['target_users'] or '(not specified)'}")
        print(f"Goal: {brief['goal'] or '(not specified)'}")
        # ... etc

    def collect_edit(self, brief: dict) -> dict:
        updated = dict(brief)  # shallow copy
        # For each editable field, prompt user
        # Accept: new value, or Enter to keep current
        # raw_idea is NOT editable (immutable input)
        ...
        return updated

    def confirm(self, brief: dict) -> bool:
        # Show summary, ask y/n
        ...
```

**StubGateIO** — for tests:

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
        updated = copy.deepcopy(brief)  # deep copy to avoid corrupting nested dicts
        for key, value in self.edits.items():
            if isinstance(value, dict) and isinstance(updated.get(key), dict):
                updated[key].update(value)  # merge nested dicts
            else:
                updated[key] = value
        return updated

    def confirm(self, brief: dict) -> bool:
        return self.approve
```

---

## 5. Component D: Spec Bundle Generator

**Purpose**: Map approved brief fields into 5 structured markdown files. No AI — deterministic mapping.

**Input**: `approved_brief.json` content
**Output**: `SpecBundle` (5 files written to artifact directory)

### SpecBundle

```python
# src/ai_dev_system/spec_bundle.py
from dataclasses import dataclass
from pathlib import Path

@dataclass
class SpecBundle:
    version: int
    root_dir: Path            # directory containing all spec files
    files: dict[str, Path]    # filename → absolute path
```

### File Mapping

| File | Source fields | Consumer |
|------|-------------|----------|
| `problem.md` | `raw_idea`, `problem`, `target_users` | Debate crew (context), Task graph (scope) |
| `requirements.md` | `goal`, `scope` | Task graph generator (task derivation) |
| `constraints.md` | `constraints.hard`, `constraints.soft` | Task graph (filter), Debate (boundary) |
| `success_criteria.md` | `success_signals` | Verification (Phase 4) |
| `assumptions.md` | `assumptions` | Debate (challenge), Risk |

**Rule**: each field maps to exactly one file. No duplication.

### Templates

**`problem.md`**

```markdown
# Problem Statement

## Raw Idea (Original Input — Do Not Interpret)
{raw_idea}

## Problem
{problem | "(not specified — to be refined)"}

## Target Users
{target_users | "(not specified — to be refined)"}
```

**`requirements.md`**

```markdown
# Requirements

## Problem Alignment
This goal addresses the problem described in problem.md.

## Goal
{goal | "(not specified — to be refined)"}

---

## Scope Definition (Execution Context)
- Type: {scope.type}
- Complexity: {scope.complexity_hint}
```

**`constraints.md`**

```markdown
# Constraints

## Hard Constraints (MUST satisfy)
{for each constraints.hard:}
- [HARD] {item}
{if empty: "(none specified)"}

## Soft Constraints (SHOULD satisfy, tradeable)
{for each constraints.soft:}
- [SOFT] {item}
{if empty: "(none specified)"}
```

**`success_criteria.md`**

```markdown
# Success Criteria

{for each success_signals:}
- [ ] {signal}
  - Metric: (to be defined)
  - Target: (to be defined)

{if empty: "(no signals defined — verification will use goal as proxy)"}
```

**`assumptions.md`**

```markdown
# Assumptions

{for each assumptions:}
- {item}

{if empty: "(no assumptions recorded)"}

> These assumptions have not been validated.
> Debate crew may challenge these. Task execution should flag if an assumption proves false.
```

### Edge Cases

| Case | Behavior |
|------|----------|
| Field is empty string | File created, section shows `(not specified — to be refined)` |
| List field is empty | File created, section shows placeholder text |
| `scope.type = "unknown"` | Written as-is — task graph generator should infer |
| All fields empty except `raw_idea` | All 5 files created with placeholders — valid but minimal |

### Generator Function

```python
# src/ai_dev_system/spec_bundle.py

REQUIRED_FILES = ["problem.md", "requirements.md", "constraints.md",
                  "success_criteria.md", "assumptions.md"]

def generate_spec_bundle(approved_brief: dict, output_dir: Path) -> SpecBundle:
    """Write 5 spec files from approved brief into output_dir.

    output_dir is a temp directory (caller manages promotion).
    Returns SpecBundle with version=1 and file map.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    files = {}
    files["problem.md"] = _write_problem(approved_brief, output_dir)
    files["requirements.md"] = _write_requirements(approved_brief, output_dir)
    files["constraints.md"] = _write_constraints(approved_brief, output_dir)
    files["success_criteria.md"] = _write_success_criteria(approved_brief, output_dir)
    files["assumptions.md"] = _write_assumptions(approved_brief, output_dir)
    return SpecBundle(version=1, root_dir=output_dir, files=files)

# _write_problem, _write_requirements, etc. each render the templates
# defined above and write to output_dir / filename. Implementation is
# straightforward string formatting — no template engine needed.
```

### SpecBundle

(Same dataclass as defined in Section 5 above.)

### Validation

```python
def validate_spec_bundle(spec_dir: Path) -> list[str]:
    """Return list of warnings (not errors). Empty = clean."""
    warnings = []
    for filename in REQUIRED_FILES:
        path = spec_dir / filename
        if not path.exists():
            warnings.append(f"Missing: {filename}")
        elif path.stat().st_size == 0:
            warnings.append(f"Empty: {filename}")
    return warnings
```

---

## 6. Pipeline Runner — Synchronous, Engine-Integrated

**Purpose**: Orchestrate A → C → D as a single synchronous run. Every step creates DB records.

### New Repo Methods Required

The existing `RunRepo` and `TaskRunRepo` only have methods for the worker loop. The pipeline needs:

```python
# Add to RunRepo:
def create(self, pipeline_type: str) -> str:
    """INSERT into runs, return run_id. Sets status='RUNNING_PHASE_1A',
    current_artifacts='{}'."""

# Add to TaskRunRepo:
def create_sync(self, run_id: str, task_type: str) -> dict:
    """INSERT into task_runs with status='RUNNING', started_at=now().
    Returns full task_run dict (task_run_id, run_id, task_id, attempt_number=1, etc).
    Unlike worker pickup, no SKIP LOCKED — this is synchronous single-threaded."""
```

These are thin INSERT wrappers. They follow the same patterns as existing repo methods (instance-based, `self.conn`).

### Exceptions

```python
# src/ai_dev_system/pipeline.py

class PipelineAborted(Exception):
    """User rejected at a gate."""

class ValidationError(Exception):
    """Brief failed schema validation."""
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(f"Validation failed: {errors}")
```

### Pipeline (written against actual APIs)

```python
# src/ai_dev_system/pipeline.py

def run_spec_pipeline(raw_idea: str, config: Config, io: GateIO) -> SpecBundle:
    """Full pipeline: normalize → gate 1 → spec bundle.

    Synchronous, blocking. No worker loop.
    Each step creates run/task_run/artifact records.
    Transaction boundary: one transaction per step (not one for entire pipeline,
    since Gate 1 blocks for human input).
    """
    conn = get_connection(config.database_url)
    run_repo = RunRepo(conn)
    task_run_repo = TaskRunRepo(conn)
    event_repo = EventRepo(conn)

    # Create run
    with conn.transaction():
        run_id = run_repo.create(pipeline_type="spec_pipeline")

    # Step 1: Normalize
    brief = normalize_idea(raw_idea)
    errors = validate_brief(brief)
    if errors:
        raise ValidationError(errors)

    with conn.transaction():
        task_run = task_run_repo.create_sync(run_id, task_type="normalize_idea")
        event_repo.insert(run_id, "TASK_STARTED", "pipeline", task_run["task_run_id"])
        # Write brief as JSON to temp path, then promote
        temp_path = _write_json_to_temp(config, task_run, brief)
        promoted = PromotedOutput(
            name="initial_brief",
            artifact_type="INITIAL_BRIEF",
            description="Normalized idea brief",
        )
        promote_output(conn, config, task_run, promoted, temp_path)
        # promote_output calls mark_success internally (step 7e)

    # Step 2: Gate 1 (human interaction — outside transaction)
    with conn.transaction():
        task_run = task_run_repo.create_sync(run_id, task_type="human_gate")
        event_repo.insert(run_id, "TASK_STARTED", "pipeline", task_run["task_run_id"])

    result = run_gate_1(brief, io)  # blocks for human input

    if result.status == "rejected":
        with conn.transaction():
            task_run_repo.mark_failed(
                task_run["task_run_id"], "EXECUTION_ERROR", "user_rejected")
        raise PipelineAborted("User rejected brief at Gate 1")

    errors = validate_brief(result.brief)
    if errors:
        with conn.transaction():
            task_run_repo.mark_failed(
                task_run["task_run_id"], "EXECUTION_ERROR", f"validation: {errors}")
        raise ValidationError(errors)

    with conn.transaction():
        temp_path = _write_json_to_temp(config, task_run, result.brief)
        promoted = PromotedOutput(
            name="approved_brief",
            artifact_type="APPROVED_BRIEF",
            description="Human-approved brief",
        )
        promote_output(conn, config, task_run, promoted, temp_path)

    # Step 3: Spec Bundle
    with conn.transaction():
        task_run = task_run_repo.create_sync(run_id, task_type="generate_spec")
        event_repo.insert(run_id, "TASK_STARTED", "pipeline", task_run["task_run_id"])

    # Generate spec files into temp dir
    temp_path = build_temp_path(
        config.storage_root, run_id,
        task_run["task_id"], task_run["attempt_number"])
    bundle = generate_spec_bundle(result.brief, Path(temp_path))
    warnings = validate_spec_bundle(bundle.root_dir)
    # Log warnings but don't fail

    with conn.transaction():
        promoted = PromotedOutput(
            name="spec_bundle",
            artifact_type="SPEC_BUNDLE",
            description="5-file spec bundle",
        )
        promote_output(conn, config, task_run, promoted, temp_path)

    return bundle


def _write_json_to_temp(config: Config, task_run: dict, data: dict) -> str:
    """Write dict as JSON file to temp path. Returns temp_path."""
    temp_path = build_temp_path(
        config.storage_root, task_run["run_id"],
        task_run["task_id"], task_run["attempt_number"])
    os.makedirs(temp_path, exist_ok=True)
    with open(os.path.join(temp_path, f"{task_run['task_id']}.json"), "w") as f:
        json.dump(data, f, indent=2)
    return temp_path
```

### Transaction Boundaries

Each step runs in its own transaction. This is deliberate:

- Gate 1 blocks for human input — holding a transaction open would be dangerous
- If step 2 fails, step 1's artifacts are already committed and traceable
- Matches the existing engine's two-transaction model (pickup vs promote)

### Why synchronous?

- No task graph yet — steps are linear
- No need for queue — 3 steps, one user
- Gate 1 is interactive (blocking by nature)
- Avoids over-engineering

### Why engine-integrated?

- Audit trail from day 1 (every step has events)
- Artifacts traceable (run_id → task_run → artifact)
- When task graph / retry logic arrives later, pipeline is already in the right shape
- No "retrofit into engine" migration

---

## 7. File Structure

```
src/ai_dev_system/
    normalize.py              # normalize_idea(), validate_brief()
    spec_bundle.py            # generate_spec_bundle(), validate_spec_bundle(), SpecBundle
    pipeline.py               # run_spec_pipeline(), PipelineAborted, ValidationError
    gate/
        __init__.py
        interface.py          # GateIO protocol
        core.py               # run_gate_1(), GateResult
        cli.py                # CLIGateIO
        stub.py               # StubGateIO (test double)

tests/
    unit/
        test_normalize.py     # normalize + validate_brief
        test_spec_bundle.py   # generate + validate
        test_gate_core.py     # run_gate_1 with StubGateIO
    integration/
        test_pipeline.py      # full pipeline with StubGateIO + real DB
```

---

## 8. What This Does NOT Include

Explicitly out of scope for this slice:

- **Debate Crew** — no AI debate, no multi-agent
- **Task Graph Generator** — spec bundle is the terminal output
- **Worker Loop** — pipeline is synchronous
- **Dead Worker Recovery / Retry** — no failure handling beyond abort
- **Real Agent Integration** — no AI agents
- **Rule Registry** — no rule matching
- **Verification (Phase 4)** — no automated quality checks
- **Beads audit** — events in DB are the audit trail for now

---

## 9. Success Criteria for This Slice

The slice is complete when:

1. `run_spec_pipeline("Build a forum for sharing knowledge", config, CLIGateIO())` runs end-to-end
2. DB has: 1 run, 3 task_runs (all SUCCESS), 3 artifacts (INITIAL_BRIEF, APPROVED_BRIEF, SPEC_BUNDLE)
3. Filesystem has: `initial_brief.json`, `approved_brief.json`, 5 spec markdown files with `_complete.marker`
4. Integration test proves the same with `StubGateIO`
5. Unit tests cover: normalize validation, brief edge cases, gate logic, spec bundle generation
