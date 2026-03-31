# Verification Phase 4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Phase 4 end-to-end: after execution completes (run.status=COMPLETED from engine), collect task evidence, LLM-judge acceptance criteria, present a verification report at Gate 3, and loop remediation up to 3 times before escalating human.

**Architecture:** Phase V-A (Python) collects evidence from completed task_runs and calls an LLM judge per acceptance criterion, then promotes a VERIFICATION_REPORT artifact and pauses at PAUSED_AT_GATE_3. The Gate 3 skill (`/review-verification`) presents the report to the human, collects skip/abort/fix decisions, and calls `finalize_gate3()` which either transitions to COMPLETED, spawns a RemediationGraph for re-execution, or escalates to PAUSED_AT_GATE_3B after 3 attempts.

> **Interface note:** `run_phase_v_pipeline()` and `run_verification()` both accept `conn` as an explicit parameter (5 args total). The spec shows 4 args without `conn` — this plan deviates intentionally for testability and consistency with `finalize_gate1()`. The conn is always `autocommit=False` (from `conn_factory()` in `run_phase_b_pipeline` or the skill's direct `psycopg.connect`). This satisfies `promote_output`'s requirement to be called inside an open transaction.

**Tech Stack:** Python 3.11+, psycopg3, PostgreSQL enum mutations via SQL migration, existing `promote_output` / `task_run_repo.create_sync` promotion pattern, `typing.Protocol` for LLM stub, `@dataclass` for data contracts.

---

## File Map

### New files

| File | Responsibility |
|------|---------------|
| `docs/schema/migrations/v4-verification.sql` | New run_status, artifact_type, event_type enum values |
| `src/ai_dev_system/verification/__init__.py` | Package marker |
| `src/ai_dev_system/verification/report.py` | `CriterionResult`, `VerificationReport`, `TaskSummaryEntry` dataclasses |
| `src/ai_dev_system/verification/judge.py` | `VerificationLLMClient` Protocol + `StubVerificationLLMClient` |
| `src/ai_dev_system/verification/collector.py` | `collect_evidence()` — query task_runs + read outputs |
| `src/ai_dev_system/verification/pipeline.py` | `run_verification()` + `run_phase_v_pipeline()` |
| `src/ai_dev_system/gate/gate3_bridge.py` | `Gate3Decision`, `Gate3Result`, `finalize_gate3()` |
| `skills/review-verification.md` | Gate 3 skill invoked via `/review-verification` |
| `tests/unit/verification/__init__.py` | Package marker |
| `tests/unit/verification/test_report.py` | Dataclass construction tests |
| `tests/unit/verification/test_judge.py` | Stub LLM tests |
| `tests/unit/verification/test_collector.py` | Evidence collection with mock DB |
| `tests/unit/verification/test_pipeline.py` | `run_verification()` end-to-end with stub LLM + mock conn |
| `tests/integration/test_verification_pipeline.py` | Phase V-A: collect → judge → VERIFICATION_REPORT artifact |
| `tests/integration/test_finalize_gate3.py` | Gate 3: all-pass, has-fail, skip, abort paths |
| `tests/integration/test_remediation_loop.py` | Attempt counter increments; attempt ≥ 3 → PAUSED_AT_GATE_3B |
| `tests/integration/test_verification_allpass.py` | All-pass fast path: empty decisions → COMPLETED |
| `tests/unit/test_debate_pipeline_phase_v.py` | Unit test: Phase V wiring in run_phase_b_pipeline |

### Modified files

| File | Change |
|------|--------|
| `src/ai_dev_system/debate_pipeline.py` | `run_phase_b_pipeline()`: after `run_execution()` returns COMPLETED, transition → RUNNING_PHASE_V, call `run_phase_v_pipeline()` |

### NOT modified

- `src/ai_dev_system/storage/paths.py` — `VERIFICATION_REPORT` intentionally absent from `ARTIFACT_TYPE_TO_KEY`. Confirmed safe: `promote_output` line `artifact_key = ARTIFACT_TYPE_TO_KEY.get(artifact_type)` uses `.get()` (not `[]`), so a missing key returns `None` and the `if artifact_key is not None:` guard skips the `current_artifacts` update without raising `KeyError`. Direct query pattern per spec.

---

## Task 1: DB Migration

**Files:**
- Create: `docs/schema/migrations/v4-verification.sql`

- [ ] **Step 1: Write migration file**

```sql
-- v4-verification.sql
-- Adds Phase 4 verification statuses, artifact type, and event types.
-- Safe to run after control-layer-schema.sql, v2-execution-runner.sql, v3-debate-engine.sql

-- New run_status values (SUCCESS = Phase 3 terminal; COMPLETED = Phase 4 terminal)
ALTER TYPE run_status ADD VALUE IF NOT EXISTS 'RUNNING_PHASE_V';
ALTER TYPE run_status ADD VALUE IF NOT EXISTS 'PAUSED_AT_GATE_3';
ALTER TYPE run_status ADD VALUE IF NOT EXISTS 'PAUSED_AT_GATE_3B';
-- Note: 'COMPLETED' and 'SUCCESS' already exist in control-layer-schema.sql

-- New artifact type
ALTER TYPE artifact_type ADD VALUE IF NOT EXISTS 'VERIFICATION_REPORT';

-- New event types
ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'VERIFICATION_STARTED';
ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'VERIFICATION_COMPLETED';
ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'REMEDIATION_CREATED';
```

- [ ] **Step 2: Apply migration to DB**

```bash
psql $DATABASE_URL -f docs/schema/migrations/v4-verification.sql
```

Expected: commands complete without error. `IF NOT EXISTS` makes it idempotent.

- [ ] **Step 3: Verify enum values exist**

```bash
psql $DATABASE_URL -c "SELECT unnest(enum_range(NULL::run_status));"
```

Expected: list includes `RUNNING_PHASE_V`, `PAUSED_AT_GATE_3`, `PAUSED_AT_GATE_3B`.

- [ ] **Step 4: Commit**

```bash
git add docs/schema/migrations/v4-verification.sql
git commit -m "feat: add v4-verification.sql migration for Phase 4 enum values"
```

---

## Task 2: Data Model

**Files:**
- Create: `src/ai_dev_system/verification/__init__.py`
- Create: `src/ai_dev_system/verification/report.py`
- Create: `tests/unit/verification/__init__.py`
- Create: `tests/unit/verification/test_report.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/verification/test_report.py
from ai_dev_system.verification.report import (
    CriterionResult, VerificationReport, TaskSummaryEntry
)


def test_criterion_result_construction():
    cr = CriterionResult(
        criterion_id="AC-1",
        criterion_text="User can create tasks",
        verdict="PASS",
        confidence=0.95,
        evidence=["task-1 output: created task OK"],
        reasoning="Task creation confirmed in output",
        related_task_ids=["TASK-1"],
    )
    assert cr.criterion_id == "AC-1"
    assert cr.verdict == "PASS"
    assert cr.confidence == 0.95


def test_task_summary_entry_construction():
    entry = TaskSummaryEntry(
        task_id="TASK-1",
        done_definition_met=True,
        output_artifact_id="some-uuid",
        verification_step_results=["pytest: 5 passed"],
    )
    assert entry.done_definition_met is True
    assert entry.output_artifact_id == "some-uuid"


def test_verification_report_overall_all_pass():
    cr_pass = CriterionResult(
        criterion_id="AC-1", criterion_text="x", verdict="PASS",
        confidence=1.0, evidence=[], reasoning="ok", related_task_ids=[],
    )
    report = VerificationReport(
        run_id="run-1", attempt=1,
        criteria=[cr_pass],
        overall="ALL_PASS",
        task_summary={},
        generated_at="2026-03-31T00:00:00+00:00",
    )
    assert report.overall == "ALL_PASS"
    assert report.attempt == 1


def test_verification_report_overall_has_fail():
    cr_fail = CriterionResult(
        criterion_id="AC-2", criterion_text="y", verdict="FAIL",
        confidence=0.9, evidence=[], reasoning="nope", related_task_ids=[],
    )
    report = VerificationReport(
        run_id="run-1", attempt=2,
        criteria=[cr_fail],
        overall="HAS_FAIL",
        task_summary={},
        generated_at="2026-03-31T00:00:00+00:00",
    )
    assert report.overall == "HAS_FAIL"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/verification/test_report.py -v
```

Expected: `ImportError` — `ai_dev_system.verification.report` does not exist yet.

- [ ] **Step 3: Create package markers**

```python
# src/ai_dev_system/verification/__init__.py
# (empty)
```

```python
# tests/unit/verification/__init__.py
# (empty)
```

- [ ] **Step 4: Implement report.py**

```python
# src/ai_dev_system/verification/report.py
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class TaskSummaryEntry:
    task_id: str
    done_definition_met: bool
    output_artifact_id: str | None
    verification_step_results: list[str] = field(default_factory=list)


@dataclass
class CriterionResult:
    criterion_id: str
    criterion_text: str
    verdict: Literal["PASS", "FAIL", "SKIP"]
    confidence: float                          # 0.0–1.0
    evidence: list[str]                        # task output excerpts used to judge
    reasoning: str                             # LLM explanation
    related_task_ids: list[str] = field(default_factory=list)


@dataclass
class VerificationReport:
    run_id: str
    attempt: int                               # 1-based; counted from VERIFICATION_REPORT artifacts
    criteria: list[CriterionResult]
    overall: Literal["ALL_PASS", "HAS_FAIL"]
    task_summary: dict[str, TaskSummaryEntry]  # task_id → TaskSummaryEntry
    generated_at: str                          # ISO 8601 UTC timestamp
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/unit/verification/test_report.py -v
```

Expected: 4 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ai_dev_system/verification/__init__.py src/ai_dev_system/verification/report.py tests/unit/verification/__init__.py tests/unit/verification/test_report.py
git commit -m "feat: add verification data model (CriterionResult, VerificationReport, TaskSummaryEntry)"
```

---

## Task 3: LLM Judge Protocol + Stub

**Files:**
- Create: `src/ai_dev_system/verification/judge.py`
- Create: `tests/unit/verification/test_judge.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/verification/test_judge.py
from ai_dev_system.verification.judge import StubVerificationLLMClient


def test_stub_returns_configured_verdict():
    stub = StubVerificationLLMClient(verdicts={
        "AC-1": ("PASS", 0.95, "looks good"),
        "AC-2": ("FAIL", 0.88, "coverage is 71%"),
    })
    verdict, conf, reasoning = stub.judge_criterion("AC-1", "User can create tasks", ["output..."])
    assert verdict == "PASS"
    assert conf == 0.95
    assert "looks good" in reasoning


def test_stub_returns_configured_fail():
    stub = StubVerificationLLMClient(verdicts={
        "AC-2": ("FAIL", 0.88, "coverage is 71%"),
    })
    verdict, conf, reasoning = stub.judge_criterion("AC-2", "Coverage ≥ 80%", ["pytest-cov: 71%"])
    assert verdict == "FAIL"
    assert conf == 0.88


def test_stub_defaults_to_pass_for_unknown_criterion():
    stub = StubVerificationLLMClient(verdicts={})
    verdict, conf, reasoning = stub.judge_criterion("AC-99", "Unknown criterion", [])
    assert verdict == "PASS"
    assert conf == 1.0


def test_stub_protocol_compliance():
    """Verify stub satisfies the Protocol interface at runtime."""
    from ai_dev_system.verification.judge import VerificationLLMClient
    from typing import runtime_checkable, Protocol
    stub = StubVerificationLLMClient(verdicts={})
    # Protocol compliance: just verify method exists and is callable
    assert callable(stub.judge_criterion)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/verification/test_judge.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Implement judge.py**

```python
# src/ai_dev_system/verification/judge.py
from typing import Literal, Protocol


class VerificationLLMClient(Protocol):
    def judge_criterion(
        self,
        criterion_id: str,
        criterion_text: str,
        evidence: list[str],
    ) -> tuple[Literal["PASS", "FAIL"], float, str]:
        """Returns: (verdict, confidence, reasoning)"""
        ...


class StubVerificationLLMClient:
    """Returns configurable verdicts per criterion_id — deterministic for tests."""

    def __init__(self, verdicts: dict[str, tuple[str, float, str]]):
        # verdicts: {"AC-1": ("PASS", 0.95, "looks good"), ...}
        self.verdicts = verdicts

    def judge_criterion(
        self,
        criterion_id: str,
        criterion_text: str,
        evidence: list[str],
    ) -> tuple[str, float, str]:
        return self.verdicts.get(criterion_id, ("PASS", 1.0, "stub default"))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/verification/test_judge.py -v
```

Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/verification/judge.py tests/unit/verification/test_judge.py
git commit -m "feat: add VerificationLLMClient protocol and StubVerificationLLMClient"
```

---

## Task 4: Evidence Collector

**Files:**
- Create: `src/ai_dev_system/verification/collector.py`
- Create: `tests/unit/verification/test_collector.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/verification/test_collector.py
import os
import json
import uuid
from unittest.mock import MagicMock
from ai_dev_system.verification.collector import collect_evidence
from ai_dev_system.verification.report import TaskSummaryEntry


def _make_conn(task_runs: list[dict], artifacts: dict[str, dict]) -> MagicMock:
    """Build a mock conn that responds to the two queries in collect_evidence."""
    conn = MagicMock()

    def execute_side_effect(query, params=None):
        cursor = MagicMock()
        q = query.strip().lower()
        if "from task_runs" in q:
            cursor.fetchall.return_value = task_runs
        elif "from artifacts" in q:
            artifact_id = params[0] if params else None
            row = artifacts.get(str(artifact_id))
            cursor.fetchone.return_value = row
        return cursor

    conn.execute.side_effect = execute_side_effect
    return conn


def test_collect_evidence_empty_run():
    conn = _make_conn(task_runs=[], artifacts={})
    summaries, evidence = collect_evidence("run-1", conn)
    assert summaries == {}
    assert evidence == []


def test_collect_evidence_success_task_no_artifact(tmp_path):
    task_id = "TASK-1"
    task_run = {
        "task_id": task_id,
        "status": "SUCCESS",
        "output_artifact_id": None,
    }
    conn = _make_conn(task_runs=[task_run], artifacts={})
    summaries, evidence = collect_evidence("run-1", conn)
    assert task_id in summaries
    entry = summaries[task_id]
    assert entry.done_definition_met is True
    assert entry.output_artifact_id is None
    assert entry.verification_step_results == []


def test_collect_evidence_reads_output_file(tmp_path):
    artifact_id = str(uuid.uuid4())
    task_id = "TASK-2"

    # Write a fake output file
    artifact_dir = tmp_path / "artifact"
    artifact_dir.mkdir()
    output_file = artifact_dir / "output.txt"
    output_file.write_text("task output: all tests passed")

    task_run = {
        "task_id": task_id,
        "status": "SUCCESS",
        "output_artifact_id": artifact_id,
    }
    artifact_row = {"content_ref": str(artifact_dir)}
    conn = _make_conn(
        task_runs=[task_run],
        artifacts={artifact_id: artifact_row},
    )
    summaries, evidence = collect_evidence("run-1", conn)
    assert task_id in summaries
    assert summaries[task_id].output_artifact_id == artifact_id
    # Evidence should contain text from the output file
    assert any("all tests passed" in e for e in evidence)


def test_collect_evidence_multiple_tasks(tmp_path):
    id1 = str(uuid.uuid4())
    id2 = str(uuid.uuid4())

    dir1 = tmp_path / "a1"; dir1.mkdir()
    (dir1 / "out.txt").write_text("task1 result")
    dir2 = tmp_path / "a2"; dir2.mkdir()
    (dir2 / "out.txt").write_text("task2 result")

    task_runs = [
        {"task_id": "T1", "status": "SUCCESS", "output_artifact_id": id1},
        {"task_id": "T2", "status": "SUCCESS", "output_artifact_id": id2},
    ]
    artifacts = {
        id1: {"content_ref": str(dir1)},
        id2: {"content_ref": str(dir2)},
    }
    conn = _make_conn(task_runs=task_runs, artifacts=artifacts)
    summaries, evidence = collect_evidence("run-1", conn)
    assert len(summaries) == 2
    assert len(evidence) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/verification/test_collector.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Implement collector.py**

```python
# src/ai_dev_system/verification/collector.py
import os

from ai_dev_system.verification.report import TaskSummaryEntry


def collect_evidence(
    run_id: str,
    conn,
) -> tuple[dict[str, TaskSummaryEntry], list[str]]:
    """
    Gather per-task summaries and evidence text from completed task_runs.

    Returns:
        task_summary: dict[task_id → TaskSummaryEntry] for all SUCCESS task_runs
        evidence: list of text excerpts (one per task that has output), for LLM judge
    """
    rows = conn.execute(
        """
        SELECT task_id, status, output_artifact_id
        FROM task_runs
        WHERE run_id = %s AND status = 'SUCCESS'
        """,
        (run_id,),
    ).fetchall()

    task_summary: dict[str, TaskSummaryEntry] = {}
    evidence: list[str] = []

    for row in rows:
        task_id = row["task_id"]
        output_artifact_id = row["output_artifact_id"]

        output_text: list[str] = []
        if output_artifact_id:
            artifact_row = conn.execute(
                "SELECT content_ref FROM artifacts WHERE artifact_id = %s",
                (output_artifact_id,),
            ).fetchone()
            if artifact_row:
                content_ref = artifact_row["content_ref"]
                output_text = _read_text_files(content_ref)

        task_summary[task_id] = TaskSummaryEntry(
            task_id=task_id,
            done_definition_met=True,  # SUCCESS status means agent confirmed done_definition
            output_artifact_id=str(output_artifact_id) if output_artifact_id else None,
            verification_step_results=output_text,
        )
        if output_text:
            combined = f"[{task_id}]\n" + "\n".join(output_text)
            evidence.append(combined)

    return task_summary, evidence


def _read_text_files(directory: str) -> list[str]:
    """Read all .txt and .log files in a directory. Returns list of file contents."""
    lines = []
    if not os.path.isdir(directory):
        return lines
    for fname in sorted(os.listdir(directory)):
        if fname.endswith((".txt", ".log", ".json")) and not fname.startswith("_"):
            fpath = os.path.join(directory, fname)
            try:
                with open(fpath, encoding="utf-8", errors="replace") as f:
                    content = f.read(4096)  # cap at 4KB per file to stay within LLM context
                lines.append(content)
            except OSError:
                pass
    return lines
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/verification/test_collector.py -v
```

Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/verification/collector.py tests/unit/verification/test_collector.py
git commit -m "feat: add collect_evidence() to gather task outputs for LLM judge"
```

---

## Task 5: Verification Pipeline

**Files:**
- Create: `src/ai_dev_system/verification/pipeline.py`
- Create: `tests/unit/verification/test_pipeline.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/verification/test_pipeline.py
import json
import os
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch, call
from ai_dev_system.verification.pipeline import run_verification, run_phase_v_pipeline
from ai_dev_system.verification.judge import StubVerificationLLMClient
from ai_dev_system.verification.report import VerificationReport


def _make_spec_artifact(tmp_path: Path, criteria_text: str) -> tuple[str, MagicMock]:
    """Write acceptance-criteria.md and return (artifact_id, mock_conn_that_finds_it)."""
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    (spec_dir / "acceptance-criteria.md").write_text(criteria_text)

    artifact_id = str(uuid.uuid4())
    conn = MagicMock()

    def execute_side_effect(query, params=None):
        cursor = MagicMock()
        q = query.strip().lower()
        if "from artifacts" in q and "artifact_id" in q:
            cursor.fetchone.return_value = {"content_ref": str(spec_dir)}
        elif "from task_runs" in q:
            cursor.fetchall.return_value = []
        elif "count" in q and "verification_report" in q:
            cursor.fetchone.return_value = {"count": 0}
        elif "from runs" in q:
            cursor.fetchone.return_value = {"status": "RUNNING_PHASE_V"}
        else:
            cursor.fetchone.return_value = None
            cursor.fetchall.return_value = []
        return cursor

    conn.execute.side_effect = execute_side_effect
    return artifact_id, conn


def test_run_verification_returns_report(tmp_path):
    criteria_text = "# Acceptance Criteria\n\nAC-1: User can create tasks\n"
    spec_id, conn = _make_spec_artifact(tmp_path, criteria_text)
    stub_llm = StubVerificationLLMClient(verdicts={"AC-1": ("PASS", 0.95, "confirmed")})

    config = MagicMock()
    config.storage_root = str(tmp_path / "storage")
    os.makedirs(config.storage_root, exist_ok=True)

    with patch("ai_dev_system.verification.pipeline.promote_output") as mock_promote, \
         patch("ai_dev_system.verification.pipeline.task_run_repo_create") as mock_create:
        mock_create.return_value = {
            "task_run_id": str(uuid.uuid4()),
            "run_id": "run-1",
            "task_id": "verification",
            "attempt_number": 1,
        }
        mock_promote.return_value = str(uuid.uuid4())

        report = run_verification("run-1", spec_id, config, conn, stub_llm)

    assert isinstance(report, VerificationReport)
    assert report.run_id == "run-1"
    assert report.attempt == 1  # count was 0 → attempt 1
    assert len(report.criteria) == 1
    assert report.criteria[0].criterion_id == "AC-1"
    assert report.criteria[0].verdict == "PASS"
    assert report.overall == "ALL_PASS"


def test_run_verification_overall_has_fail(tmp_path):
    criteria_text = "# Acceptance Criteria\n\nAC-1: Coverage ≥ 80%\n"
    spec_id, conn = _make_spec_artifact(tmp_path, criteria_text)
    stub_llm = StubVerificationLLMClient(verdicts={"AC-1": ("FAIL", 0.99, "only 71%")})

    config = MagicMock()
    config.storage_root = str(tmp_path / "storage")
    os.makedirs(config.storage_root, exist_ok=True)

    with patch("ai_dev_system.verification.pipeline.promote_output") as mock_promote, \
         patch("ai_dev_system.verification.pipeline.task_run_repo_create") as mock_create:
        mock_create.return_value = {
            "task_run_id": str(uuid.uuid4()),
            "run_id": "run-1",
            "task_id": "verification",
            "attempt_number": 1,
        }
        mock_promote.return_value = str(uuid.uuid4())

        report = run_verification("run-1", spec_id, config, conn, stub_llm)

    assert report.overall == "HAS_FAIL"
    assert report.criteria[0].verdict == "FAIL"


def test_run_phase_v_pipeline_transitions_to_paused(tmp_path):
    criteria_text = "# Acceptance Criteria\n\nAC-1: All good\n"
    spec_id, conn = _make_spec_artifact(tmp_path, criteria_text)
    stub_llm = StubVerificationLLMClient(verdicts={})

    config = MagicMock()
    config.storage_root = str(tmp_path / "storage")
    os.makedirs(config.storage_root, exist_ok=True)

    status_updates = []
    original_side_effect = conn.execute.side_effect

    def tracking_execute(query, params=None):
        if "update runs set status" in query.lower():
            status_updates.append(params[0] if params else None)
            cursor = MagicMock()
            return cursor
        return original_side_effect(query, params)

    conn.execute.side_effect = tracking_execute

    with patch("ai_dev_system.verification.pipeline.promote_output") as mock_promote, \
         patch("ai_dev_system.verification.pipeline.task_run_repo_create") as mock_create:
        mock_create.return_value = {
            "task_run_id": str(uuid.uuid4()),
            "run_id": "run-1",
            "task_id": "verification",
            "attempt_number": 1,
        }
        mock_promote.return_value = str(uuid.uuid4())

        run_phase_v_pipeline("run-1", spec_id, config, conn, stub_llm)

    assert "PAUSED_AT_GATE_3" in status_updates
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/verification/test_pipeline.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Implement pipeline.py**

```python
# src/ai_dev_system/verification/pipeline.py
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from ai_dev_system.agents.base import PromotedOutput
from ai_dev_system.config import Config
from ai_dev_system.db.repos.events import EventRepo
from ai_dev_system.db.repos.task_runs import TaskRunRepo
from ai_dev_system.storage.paths import build_temp_path
from ai_dev_system.storage.promote import promote_output
from ai_dev_system.verification.collector import collect_evidence
from ai_dev_system.verification.judge import VerificationLLMClient
from ai_dev_system.verification.report import CriterionResult, VerificationReport


def task_run_repo_create(run_id: str, task_type: str, conn) -> dict:
    """Thin wrapper so unit tests can patch it without patching the whole class."""
    return TaskRunRepo(conn).create_sync(run_id, task_type)


def run_phase_v_pipeline(
    run_id: str,
    spec_artifact_id: str,
    config: Config,
    conn,
    llm: VerificationLLMClient,
) -> VerificationReport:
    """
    Phase V-A standalone entry point.

    Precondition:  run.status = RUNNING_PHASE_V (caller must set this before calling)
    Postcondition: run.status = PAUSED_AT_GATE_3

    Returns VerificationReport (also written as VERIFICATION_REPORT artifact).
    """
    report = run_verification(run_id, spec_artifact_id, config, conn, llm)

    conn.execute(
        "UPDATE runs SET status = %s, last_activity_at = now() WHERE run_id = %s",
        ("PAUSED_AT_GATE_3", run_id),
    )
    EventRepo(conn).insert(run_id, "VERIFICATION_COMPLETED", "system")

    return report


def run_verification(
    run_id: str,
    spec_artifact_id: str,
    config: Config,
    conn,
    llm: VerificationLLMClient,
) -> VerificationReport:
    """
    Internal: collect evidence → LLM judge per criterion → persist VERIFICATION_REPORT artifact.

    VERIFICATION_REPORT is NOT tracked in runs.current_artifacts.
    Access pattern: direct artifact table query (run_id + artifact_type='VERIFICATION_REPORT').
    """
    event_repo = EventRepo(conn)
    event_repo.insert(run_id, "VERIFICATION_STARTED", "system")

    # --- Attempt counter: count existing VERIFICATION_REPORT artifacts ---
    count_row = conn.execute(
        """
        SELECT COUNT(*) AS count FROM artifacts
        WHERE run_id = %s AND artifact_type = 'VERIFICATION_REPORT'
          AND status IN ('ACTIVE', 'SUPERSEDED')
        """,
        (run_id,),
    ).fetchone()
    attempt = (count_row["count"] if count_row else 0) + 1

    # --- Collect evidence from completed task_runs ---
    task_summary, evidence = collect_evidence(run_id, conn)

    # --- Load acceptance criteria from SPEC_BUNDLE artifact ---
    spec_row = conn.execute(
        "SELECT content_ref FROM artifacts WHERE artifact_id = %s",
        (spec_artifact_id,),
    ).fetchone()
    criteria_list = _parse_acceptance_criteria(spec_row["content_ref"])

    # --- LLM judge each criterion ---
    criterion_results: list[CriterionResult] = []
    for cid, ctext in criteria_list:
        verdict, confidence, reasoning = llm.judge_criterion(cid, ctext, evidence)
        criterion_results.append(CriterionResult(
            criterion_id=cid,
            criterion_text=ctext,
            verdict=verdict,
            confidence=confidence,
            evidence=evidence[:3],  # include up to 3 excerpts in the result
            reasoning=reasoning,
            related_task_ids=list(task_summary.keys()),
        ))

    overall = "ALL_PASS" if all(c.verdict != "FAIL" for c in criterion_results) else "HAS_FAIL"

    report = VerificationReport(
        run_id=run_id,
        attempt=attempt,
        criteria=criterion_results,
        overall=overall,
        task_summary=task_summary,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )

    # --- Persist as VERIFICATION_REPORT artifact ---
    task_run = task_run_repo_create(run_id, "verification_report", conn)
    task_run["input_artifact_ids"] = [spec_artifact_id]

    temp_path = build_temp_path(
        config.storage_root, run_id, task_run["task_id"], task_run["attempt_number"]
    )
    os.makedirs(temp_path, exist_ok=True)

    report_dict = _report_to_dict(report)
    with open(os.path.join(temp_path, "verification_report.json"), "w", encoding="utf-8") as f:
        json.dump(report_dict, f, indent=2, ensure_ascii=False)

    promote_output(
        conn, config, task_run,
        PromotedOutput("verification_report", "VERIFICATION_REPORT", "Phase 4 verification report"),
        temp_path,
    )

    return report


def _parse_acceptance_criteria(spec_bundle_path: str) -> list[tuple[str, str]]:
    """
    Parse acceptance-criteria.md from spec bundle directory.

    Returns list of (criterion_id, criterion_text) tuples.
    Looks for lines matching patterns like:
      - "AC-1: some text"
      - "**AC-1**: some text"
      - "## AC-1 — some text"
    """
    criteria_file = os.path.join(spec_bundle_path, "acceptance-criteria.md")
    if not os.path.exists(criteria_file):
        return []

    with open(criteria_file, encoding="utf-8") as f:
        content = f.read()

    results = []
    # Match lines like: AC-1: text, AC-1 — text, **AC-1**: text
    pattern = re.compile(
        r"(?:^|\n)\s*(?:\*\*)?(?P<id>AC-\d+)(?:\*\*)?[\s:—\-]+(?P<text>[^\n]+)",
        re.MULTILINE,
    )
    for m in pattern.finditer(content):
        results.append((m.group("id").strip(), m.group("text").strip()))

    return results


def _report_to_dict(report: VerificationReport) -> dict:
    return {
        "run_id": report.run_id,
        "attempt": report.attempt,
        "overall": report.overall,
        "generated_at": report.generated_at,
        "criteria": [
            {
                "criterion_id": c.criterion_id,
                "criterion_text": c.criterion_text,
                "verdict": c.verdict,
                "confidence": c.confidence,
                "evidence": c.evidence,
                "reasoning": c.reasoning,
                "related_task_ids": c.related_task_ids,
            }
            for c in report.criteria
        ],
        "task_summary": {
            k: {
                "task_id": v.task_id,
                "done_definition_met": v.done_definition_met,
                "output_artifact_id": v.output_artifact_id,
                "verification_step_results": v.verification_step_results,
            }
            for k, v in report.task_summary.items()
        },
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/verification/test_pipeline.py -v
```

Expected: 3 tests PASS.

- [ ] **Step 5: Run all unit tests to check no regressions**

```bash
pytest tests/unit/ -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/ai_dev_system/verification/pipeline.py tests/unit/verification/test_pipeline.py
git commit -m "feat: add run_verification() and run_phase_v_pipeline() orchestration"
```

---

## Task 6: Gate 3 Bridge

**Files:**
- Create: `src/ai_dev_system/gate/gate3_bridge.py`
- Create: `tests/integration/test_finalize_gate3.py`
- Create: `tests/integration/test_verification_allpass.py`

- [ ] **Step 1: Write the failing integration tests**

```python
# tests/integration/test_finalize_gate3.py
"""
Integration tests for finalize_gate3().
These tests require DATABASE_URL and a live DB with v4-verification.sql applied.
"""
import json
import uuid
from pathlib import Path
import pytest
from ai_dev_system.gate.gate3_bridge import finalize_gate3, Gate3Decision, Gate3Result
from ai_dev_system.db.repos.runs import RunRepo


# ─── Helpers ────────────────────────────────────────────────────────────────

def _seed_run_at_phase_v(conn, project_id: str) -> str:
    """Insert a run in PAUSED_AT_GATE_3 status — the state finalize_gate3 is called from."""
    run_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO runs (run_id, project_id, status, title, current_artifacts, metadata)
        VALUES (%s, %s, 'PAUSED_AT_GATE_3', 'Gate3 Test Run', '{}', '{}')
    """, (run_id, project_id))
    return run_id


def _seed_verification_report(conn, run_id: str, tmp_path: Path, criteria_verdicts: dict) -> str:
    """Insert a VERIFICATION_REPORT artifact with given verdicts. Returns artifact_id."""
    artifact_id = str(uuid.uuid4())
    artifact_dir = tmp_path / f"vr_{artifact_id[:8]}"
    artifact_dir.mkdir()

    criteria = [
        {
            "criterion_id": cid,
            "criterion_text": f"Text for {cid}",
            "verdict": verdict,
            "confidence": 0.9,
            "evidence": [],
            "reasoning": "test",
            "related_task_ids": [],
        }
        for cid, verdict in criteria_verdicts.items()
    ]
    report = {
        "run_id": run_id,
        "attempt": 1,
        "overall": "ALL_PASS" if all(v == "PASS" for v in criteria_verdicts.values()) else "HAS_FAIL",
        "generated_at": "2026-03-31T00:00:00+00:00",
        "criteria": criteria,
        "task_summary": {},
    }
    (artifact_dir / "verification_report.json").write_text(json.dumps(report))
    (artifact_dir / "_complete.marker").write_text("{}")

    conn.execute("""
        INSERT INTO artifacts (
            artifact_id, run_id, artifact_type, version, status, created_by,
            input_artifact_ids, content_ref, content_checksum, content_size
        ) VALUES (%s, %s, 'VERIFICATION_REPORT', 1, 'ACTIVE', 'system',
                  '{}', %s, 'test-checksum', 0)
    """, (artifact_id, run_id, str(artifact_dir)))
    return artifact_id


# ─── Tests ──────────────────────────────────────────────────────────────────

def test_finalize_gate3_all_pass_completes_run(conn, config, project_id, tmp_path):
    run_id = _seed_run_at_phase_v(conn, project_id)
    _seed_verification_report(conn, run_id, tmp_path, {"AC-1": "PASS", "AC-2": "PASS"})

    result = finalize_gate3(run_id, decisions=[], storage_root=config.storage_root, conn=conn)

    assert result.run_id == run_id
    assert result.has_remediation is False
    assert result.aborted is False

    row = conn.execute("SELECT status FROM runs WHERE run_id = %s", (run_id,)).fetchone()
    assert row["status"] == "COMPLETED"


def test_finalize_gate3_abort_sets_aborted(conn, config, project_id, tmp_path):
    run_id = _seed_run_at_phase_v(conn, project_id)
    _seed_verification_report(conn, run_id, tmp_path, {"AC-1": "FAIL"})

    result = finalize_gate3(
        run_id,
        decisions=[Gate3Decision(criterion_id="AC-1", action="ABORT")],
        storage_root=config.storage_root,
        conn=conn,
    )

    assert result.aborted is True
    row = conn.execute("SELECT status FROM runs WHERE run_id = %s", (run_id,)).fetchone()
    assert row["status"] == "ABORTED"


def test_finalize_gate3_all_skipped_completes(conn, config, project_id, tmp_path):
    run_id = _seed_run_at_phase_v(conn, project_id)
    _seed_verification_report(conn, run_id, tmp_path, {"AC-1": "FAIL"})

    result = finalize_gate3(
        run_id,
        decisions=[Gate3Decision(criterion_id="AC-1", action="SKIP")],
        storage_root=config.storage_root,
        conn=conn,
    )

    assert result.has_remediation is False
    assert result.aborted is False
    row = conn.execute("SELECT status FROM runs WHERE run_id = %s", (run_id,)).fetchone()
    assert row["status"] == "COMPLETED"


def test_finalize_gate3_fail_triggers_remediation(conn, config, project_id, tmp_path):
    run_id = _seed_run_at_phase_v(conn, project_id)
    _seed_verification_report(conn, run_id, tmp_path, {"AC-1": "PASS", "AC-2": "FAIL"})

    # No decisions for AC-2 → it stays FAIL → should generate remediation
    result = finalize_gate3(run_id, decisions=[], storage_root=config.storage_root, conn=conn)

    assert result.has_remediation is True
    assert result.remediation_graph is not None
    row = conn.execute("SELECT status FROM runs WHERE run_id = %s", (run_id,)).fetchone()
    assert row["status"] == "RUNNING_PHASE_V"


def test_finalize_gate3_attempt3_pauses_at_gate3b(conn, config, project_id, tmp_path):
    run_id = _seed_run_at_phase_v(conn, project_id)

    # Seed 3 VERIFICATION_REPORT artifacts to simulate 3 attempts already done
    for i in range(1, 4):
        artifact_id = str(uuid.uuid4())
        artifact_dir = tmp_path / f"vr_{i}"
        artifact_dir.mkdir()
        (artifact_dir / "verification_report.json").write_text(json.dumps({
            "run_id": run_id, "attempt": i, "overall": "HAS_FAIL",
            "generated_at": "2026-03-31T00:00:00+00:00",
            "criteria": [{"criterion_id": "AC-1", "criterion_text": "x",
                           "verdict": "FAIL", "confidence": 0.9,
                           "evidence": [], "reasoning": "x", "related_task_ids": []}],
            "task_summary": {},
        }))
        (artifact_dir / "_complete.marker").write_text("{}")
        status = "ACTIVE" if i == 3 else "SUPERSEDED"
        conn.execute("""
            INSERT INTO artifacts (
                artifact_id, run_id, artifact_type, version, status, created_by,
                input_artifact_ids, content_ref, content_checksum, content_size
            ) VALUES (%s, %s, 'VERIFICATION_REPORT', %s, %s, 'system',
                      '{}', %s, 'chk', 0)
        """, (artifact_id, run_id, i, status, str(artifact_dir)))

    # attempt count = 3 → should escalate to PAUSED_AT_GATE_3B
    result = finalize_gate3(run_id, decisions=[], storage_root=config.storage_root, conn=conn)

    row = conn.execute("SELECT status FROM runs WHERE run_id = %s", (run_id,)).fetchone()
    assert row["status"] == "PAUSED_AT_GATE_3B"
    assert result.has_remediation is False
```

```python
# tests/integration/test_verification_allpass.py
"""All-pass fast path: finalize_gate3 with empty decisions on an all-PASS report → COMPLETED."""
import json
import uuid
from pathlib import Path
import pytest
from ai_dev_system.gate.gate3_bridge import finalize_gate3


def test_allpass_empty_decisions_completes(conn, config, project_id, tmp_path):
    run_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO runs (run_id, project_id, status, title, current_artifacts, metadata)
        VALUES (%s, %s, 'RUNNING_PHASE_V', 'AllPass Test', '{}', '{}')
    """, (run_id, project_id))

    # All criteria PASS
    artifact_id = str(uuid.uuid4())
    artifact_dir = tmp_path / "allpass"
    artifact_dir.mkdir()
    report = {
        "run_id": run_id, "attempt": 1, "overall": "ALL_PASS",
        "generated_at": "2026-03-31T00:00:00+00:00",
        "criteria": [
            {"criterion_id": "AC-1", "criterion_text": "x", "verdict": "PASS",
             "confidence": 1.0, "evidence": [], "reasoning": "ok", "related_task_ids": []},
        ],
        "task_summary": {},
    }
    (artifact_dir / "verification_report.json").write_text(json.dumps(report))
    (artifact_dir / "_complete.marker").write_text("{}")
    conn.execute("""
        INSERT INTO artifacts (artifact_id, run_id, artifact_type, version, status, created_by,
                               input_artifact_ids, content_ref, content_checksum, content_size)
        VALUES (%s, %s, 'VERIFICATION_REPORT', 1, 'ACTIVE', 'system', '{}', %s, 'x', 0)
    """, (artifact_id, run_id, str(artifact_dir)))

    result = finalize_gate3(run_id, decisions=[], storage_root=config.storage_root, conn=conn)

    assert result.has_remediation is False
    assert result.aborted is False
    row = conn.execute("SELECT status FROM runs WHERE run_id = %s", (run_id,)).fetchone()
    assert row["status"] == "COMPLETED"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/integration/test_finalize_gate3.py tests/integration/test_verification_allpass.py -v
```

Expected: `ImportError` — `gate3_bridge` does not exist.

- [ ] **Step 3: Implement gate3_bridge.py**

```python
# src/ai_dev_system/gate/gate3_bridge.py
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ai_dev_system.db.repos.events import EventRepo
from ai_dev_system.db.repos.runs import RunRepo


@dataclass
class Gate3Decision:
    criterion_id: str
    action: Literal["SKIP", "ABORT"]   # only FAIL criteria need a decision; PASS is implicit


@dataclass
class Gate3Result:
    run_id: str
    has_remediation: bool
    remediation_graph: dict | None   # TaskGraph JSON when fail criteria → remediation
    aborted: bool


def finalize_gate3(
    run_id: str,
    decisions: list[Gate3Decision],
    storage_root: str,
    conn,
) -> Gate3Result:
    """
    Apply Gate 3 decisions to the current VERIFICATION_REPORT.

    Decision logic:
      - PASS criteria (not in decisions list) → accepted automatically
      - SKIP decision → criterion skipped, not counted as fail
      - ABORT decision → run.status = ABORTED immediately
      - Remaining FAIL criteria (not skipped, not aborted):
          - attempt < 3 → generate RemediationGraph → run.status = RUNNING_PHASE_V
          - attempt ≥ 3 → run.status = PAUSED_AT_GATE_3B (soft limit)
      - All pass/skip → run.status = COMPLETED
    """
    run_repo = RunRepo(conn)
    event_repo = EventRepo(conn)

    # --- Load current VERIFICATION_REPORT ---
    artifact_row = conn.execute(
        """
        SELECT content_ref FROM artifacts
        WHERE run_id = %s AND artifact_type = 'VERIFICATION_REPORT' AND status = 'ACTIVE'
        ORDER BY version DESC LIMIT 1
        """,
        (run_id,),
    ).fetchone()
    if not artifact_row:
        raise ValueError(f"No active VERIFICATION_REPORT found for run {run_id}")

    report_path = os.path.join(artifact_row["content_ref"], "verification_report.json")
    with open(report_path, encoding="utf-8") as f:
        report = json.load(f)

    # --- Attempt counter (before adding the one we just read) ---
    count_row = conn.execute(
        """
        SELECT COUNT(*) AS count FROM artifacts
        WHERE run_id = %s AND artifact_type = 'VERIFICATION_REPORT'
          AND status IN ('ACTIVE', 'SUPERSEDED')
        """,
        (run_id,),
    ).fetchone()
    attempt_count = count_row["count"] if count_row else 1

    # --- Build decision index ---
    decision_map = {d.criterion_id: d.action for d in decisions}

    # --- Check for ABORT ---
    for d in decisions:
        if d.action == "ABORT":
            run_repo.update_status(run_id, "ABORTED")
            event_repo.insert(run_id, "VERIFICATION_COMPLETED", "system",
                              payload={"outcome": "ABORTED"})
            return Gate3Result(run_id=run_id, has_remediation=False,
                               remediation_graph=None, aborted=True)

    # --- Classify criteria ---
    fail_criteria = [
        c for c in report["criteria"]
        if c["verdict"] == "FAIL" and decision_map.get(c["criterion_id"]) != "SKIP"
    ]

    if not fail_criteria:
        # All pass or all skipped → COMPLETED
        run_repo.update_status(run_id, "COMPLETED")
        event_repo.insert(run_id, "VERIFICATION_COMPLETED", "system",
                          payload={"outcome": "COMPLETED"})
        return Gate3Result(run_id=run_id, has_remediation=False,
                           remediation_graph=None, aborted=False)

    # --- Remaining fails: check attempt limit ---
    if attempt_count >= 3:
        run_repo.update_status(run_id, "PAUSED_AT_GATE_3B")
        event_repo.insert(run_id, "VERIFICATION_COMPLETED", "system",
                          payload={"outcome": "PAUSED_AT_GATE_3B", "attempt": attempt_count})
        return Gate3Result(run_id=run_id, has_remediation=False,
                           remediation_graph=None, aborted=False)

    # --- Generate remediation graph ---
    remediation_graph = _generate_remediation_graph(fail_criteria)
    event_repo.insert(run_id, "REMEDIATION_CREATED", "system",
                      payload={"fail_count": len(fail_criteria)})

    run_repo.update_status(run_id, "RUNNING_PHASE_V")

    return Gate3Result(run_id=run_id, has_remediation=True,
                       remediation_graph=remediation_graph, aborted=False)


def _generate_remediation_graph(fail_criteria: list[dict]) -> dict:
    """
    Minimal remediation graph: one task per failing criterion.
    In production, an LLM would generate this. For v1, stubs are sufficient.
    """
    tasks = []
    for i, c in enumerate(fail_criteria, start=1):
        tasks.append({
            "id": f"REMEDIATE-{c['criterion_id']}",
            "execution_type": "atomic",
            "phase": "remediation",
            "type": "fix",
            "agent_type": "Implementer",
            "objective": f"Fix criterion {c['criterion_id']}: {c['criterion_text']}",
            "description": f"Reasoning from last attempt: {c.get('reasoning', '')}",
            "done_definition": f"Criterion {c['criterion_id']} must now pass verification",
            "verification_steps": [],
            "deps": [f"REMEDIATE-{fail_criteria[i-2]['criterion_id']}"] if i > 1 else [],
            "required_inputs": [],
            "expected_outputs": [],
        })
    return {
        "graph_version": 1,
        "remediation": True,
        "tasks": tasks,
    }
```

> **Note on `EventRepo.insert`:** signature is `insert(run_id, event_type, actor, task_run_id=None, payload=None)`. Use `payload=` as shown above.

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/integration/test_finalize_gate3.py tests/integration/test_verification_allpass.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/gate/gate3_bridge.py tests/integration/test_finalize_gate3.py tests/integration/test_verification_allpass.py
git commit -m "feat: add finalize_gate3() with pass/skip/abort/remediation/attempt-limit paths"
```

---

## Task 7: Integration Tests — Full Pipeline + Remediation Loop

**Files:**
- Create: `tests/integration/test_verification_pipeline.py`
- Create: `tests/integration/test_remediation_loop.py`

- [ ] **Step 1: Write the failing integration tests**

```python
# tests/integration/test_verification_pipeline.py
"""
Integration test: run_phase_v_pipeline() end-to-end with a real DB.
Requires DATABASE_URL + v4-verification.sql applied.
"""
import json
import uuid
from pathlib import Path
import pytest
from ai_dev_system.verification.pipeline import run_phase_v_pipeline
from ai_dev_system.verification.judge import StubVerificationLLMClient


def _seed_run_phase_v(conn, project_id: str) -> str:
    run_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO runs (run_id, project_id, status, title, current_artifacts, metadata)
        VALUES (%s, %s, 'RUNNING_PHASE_V', 'Phase V Test', '{}', '{}')
    """, (run_id, project_id))
    return run_id


def _seed_spec_bundle(conn, run_id: str, tmp_path: Path, ac_content: str) -> str:
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    (spec_dir / "acceptance-criteria.md").write_text(ac_content)
    (spec_dir / "_complete.marker").write_text("{}")

    artifact_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO artifacts (artifact_id, run_id, artifact_type, version, status, created_by,
                               input_artifact_ids, content_ref, content_checksum, content_size)
        VALUES (%s, %s, 'SPEC_BUNDLE', 1, 'ACTIVE', 'system', '{}', %s, 'spec-chk', 0)
    """, (artifact_id, run_id, str(spec_dir)))
    return artifact_id


def test_run_phase_v_pipeline_creates_artifact(conn, config, project_id, tmp_path):
    run_id = _seed_run_phase_v(conn, project_id)
    spec_id = _seed_spec_bundle(conn, run_id, tmp_path,
                                "# Acceptance Criteria\n\nAC-1: User can login\n")

    stub = StubVerificationLLMClient(verdicts={"AC-1": ("PASS", 0.98, "login works")})
    report = run_phase_v_pipeline(run_id, spec_id, config, conn, stub)

    # run.status should be PAUSED_AT_GATE_3
    row = conn.execute("SELECT status FROM runs WHERE run_id = %s", (run_id,)).fetchone()
    assert row["status"] == "PAUSED_AT_GATE_3"

    # VERIFICATION_REPORT artifact should exist
    art = conn.execute(
        "SELECT content_ref FROM artifacts WHERE run_id = %s AND artifact_type = 'VERIFICATION_REPORT' AND status = 'ACTIVE'",
        (run_id,),
    ).fetchone()
    assert art is not None

    # Report file should be readable and correct
    report_file = Path(art["content_ref"]) / "verification_report.json"
    assert report_file.exists()
    data = json.loads(report_file.read_text())
    assert data["run_id"] == run_id
    assert data["attempt"] == 1
    assert data["overall"] == "ALL_PASS"
    assert data["criteria"][0]["criterion_id"] == "AC-1"


def test_run_phase_v_pipeline_has_fail(conn, config, project_id, tmp_path):
    run_id = _seed_run_phase_v(conn, project_id)
    spec_id = _seed_spec_bundle(conn, run_id, tmp_path,
                                "# Acceptance Criteria\n\nAC-1: Coverage >= 80%\n")

    stub = StubVerificationLLMClient(verdicts={"AC-1": ("FAIL", 0.99, "only 71%")})
    report = run_phase_v_pipeline(run_id, spec_id, config, conn, stub)

    assert report.overall == "HAS_FAIL"
    row = conn.execute("SELECT status FROM runs WHERE run_id = %s", (run_id,)).fetchone()
    assert row["status"] == "PAUSED_AT_GATE_3"
```

```python
# tests/integration/test_remediation_loop.py
"""
Integration: attempt counter increments correctly; attempt ≥ 3 → PAUSED_AT_GATE_3B.
"""
import json
import uuid
from pathlib import Path
import pytest
from ai_dev_system.gate.gate3_bridge import finalize_gate3, Gate3Decision


def _insert_vr_artifact(conn, run_id: str, tmp_path: Path, attempt: int, status: str) -> str:
    artifact_id = str(uuid.uuid4())
    d = tmp_path / f"vr_{attempt}"
    d.mkdir()
    report = {
        "run_id": run_id, "attempt": attempt, "overall": "HAS_FAIL",
        "generated_at": "2026-03-31T00:00:00+00:00",
        "criteria": [
            {"criterion_id": "AC-1", "criterion_text": "x", "verdict": "FAIL",
             "confidence": 0.9, "evidence": [], "reasoning": "x", "related_task_ids": []}
        ],
        "task_summary": {},
    }
    (d / "verification_report.json").write_text(json.dumps(report))
    (d / "_complete.marker").write_text("{}")
    conn.execute("""
        INSERT INTO artifacts (artifact_id, run_id, artifact_type, version, status, created_by,
                               input_artifact_ids, content_ref, content_checksum, content_size)
        VALUES (%s, %s, 'VERIFICATION_REPORT', %s, %s, 'system', '{}', %s, 'chk', 0)
    """, (artifact_id, run_id, attempt, status, str(d)))
    return artifact_id


def test_attempt_counter_increments(conn, config, project_id, tmp_path):
    run_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO runs (run_id, project_id, status, title, current_artifacts, metadata)
        VALUES (%s, %s, 'PAUSED_AT_GATE_3', 'Loop Test', '{}', '{}')
    """, (run_id, project_id))

    # Attempt 1: 1 VERIFICATION_REPORT → attempt count = 1 → <3 → remediation
    _insert_vr_artifact(conn, run_id, tmp_path, attempt=1, status="ACTIVE")

    result = finalize_gate3(run_id, decisions=[], storage_root=config.storage_root, conn=conn)

    assert result.has_remediation is True
    row = conn.execute("SELECT status FROM runs WHERE run_id = %s", (run_id,)).fetchone()
    assert row["status"] == "RUNNING_PHASE_V"


def test_attempt_3_triggers_paused_at_gate3b(conn, config, project_id, tmp_path):
    run_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO runs (run_id, project_id, status, title, current_artifacts, metadata)
        VALUES (%s, %s, 'PAUSED_AT_GATE_3', 'Loop Test 3x', '{}', '{}')
    """, (run_id, project_id))

    # 3 VERIFICATION_REPORT artifacts (attempts 1, 2, 3)
    _insert_vr_artifact(conn, run_id, tmp_path, 1, "SUPERSEDED")
    _insert_vr_artifact(conn, run_id, tmp_path, 2, "SUPERSEDED")
    _insert_vr_artifact(conn, run_id, tmp_path, 3, "ACTIVE")

    result = finalize_gate3(run_id, decisions=[], storage_root=config.storage_root, conn=conn)

    assert result.has_remediation is False
    assert result.aborted is False
    row = conn.execute("SELECT status FROM runs WHERE run_id = %s", (run_id,)).fetchone()
    assert row["status"] == "PAUSED_AT_GATE_3B"


def test_attempt_2_still_triggers_remediation(conn, config, project_id, tmp_path):
    run_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO runs (run_id, project_id, status, title, current_artifacts, metadata)
        VALUES (%s, %s, 'PAUSED_AT_GATE_3', 'Loop Test 2x', '{}', '{}')
    """, (run_id, project_id))

    # 2 VERIFICATION_REPORT artifacts → attempt count = 2 → still < 3
    _insert_vr_artifact(conn, run_id, tmp_path, 1, "SUPERSEDED")
    _insert_vr_artifact(conn, run_id, tmp_path, 2, "ACTIVE")

    result = finalize_gate3(run_id, decisions=[], storage_root=config.storage_root, conn=conn)

    assert result.has_remediation is True
    row = conn.execute("SELECT status FROM runs WHERE run_id = %s", (run_id,)).fetchone()
    assert row["status"] == "RUNNING_PHASE_V"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/integration/test_verification_pipeline.py tests/integration/test_remediation_loop.py -v
```

Expected: failures due to missing DB enum values (needs migration applied) or import issues.

- [ ] **Step 3: Run tests to verify they pass**

```bash
pytest tests/integration/test_verification_pipeline.py tests/integration/test_remediation_loop.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 4: Run all integration tests to check no regressions**

```bash
pytest tests/integration/ -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_verification_pipeline.py tests/integration/test_remediation_loop.py
git commit -m "test: add integration tests for Phase V-A pipeline and remediation loop"
```

---

## Task 8: Wire debate_pipeline.py

**Files:**
- Modify: `src/ai_dev_system/debate_pipeline.py`

- [ ] **Step 1: Read the existing file** (already done in planning — lines 189-198)

The change is: after `run_execution()` returns COMPLETED, transition to `RUNNING_PHASE_V` and call `run_phase_v_pipeline()`. If agent is None (existing code path), skip Phase V (tests that don't pass an agent should not break).

- [ ] **Step 2: Write the failing test**

```python
# tests/unit/test_debate_pipeline_phase_v.py
"""Unit test: run_phase_b_pipeline() calls run_phase_v_pipeline() after COMPLETED execution."""
import uuid
from unittest.mock import MagicMock, patch, call
from ai_dev_system.debate_pipeline import run_phase_b_pipeline
from ai_dev_system.engine.runner import ExecutionResult


def _make_phase_b_conn(run_id: str, tmp_path) -> MagicMock:
    """Mock conn that satisfies all queries in run_phase_b_pipeline."""
    import json, os
    # Set up fake spec bundle dir and approved_answers
    spec_dir = tmp_path / "spec"; spec_dir.mkdir()
    aa_dir = tmp_path / "aa"; aa_dir.mkdir()
    (aa_dir / "approved_answers.json").write_text(json.dumps({"Q1": "yes"}))

    conn = MagicMock()
    def execute(query, params=None):
        cursor = MagicMock()
        q = query.strip().lower()
        if "select status" in q and "current_artifacts" in q:
            cursor.fetchone.return_value = {
                "status": "RUNNING_PHASE_1D",
                "current_artifacts": {"approved_answers_id": "aa-id"},
            }
        elif "from artifacts" in q:
            art_id = (params or [""])[0]
            if art_id == "aa-id":
                cursor.fetchone.return_value = {"content_ref": str(aa_dir)}
            else:
                cursor.fetchone.return_value = {"content_ref": str(spec_dir)}
        else:
            cursor.fetchone.return_value = None
            cursor.fetchall.return_value = []
        return cursor
    conn.execute.side_effect = execute
    return conn


def test_phase_v_pipeline_called_after_completed_execution(tmp_path):
    run_id = str(uuid.uuid4())
    conn = _make_phase_b_conn(run_id, tmp_path)
    stub_agent = MagicMock()
    stub_llm = MagicMock()

    with patch("ai_dev_system.debate_pipeline.finalize_spec") as mock_spec, \
         patch("ai_dev_system.debate_pipeline.generate_task_graph") as mock_tg, \
         patch("ai_dev_system.debate_pipeline.run_gate_2") as mock_g2, \
         patch("ai_dev_system.debate_pipeline.beads_sync"), \
         patch("ai_dev_system.debate_pipeline.promote_output") as mock_promote, \
         patch("ai_dev_system.debate_pipeline.run_execution") as mock_exec, \
         patch("ai_dev_system.verification.pipeline.run_phase_v_pipeline") as mock_phase_v:

        mock_spec.return_value = MagicMock(files=["acceptance-criteria.md"])
        mock_tg.return_value = {"graph_version": 1, "tasks": []}
        g2_result = MagicMock(); g2_result.status = "approved"; g2_result.graph = {}
        mock_g2.return_value = g2_result
        mock_promote.return_value = str(uuid.uuid4())
        mock_exec.return_value = ExecutionResult(run_id=run_id, status="COMPLETED")
        mock_phase_v.return_value = MagicMock()

        config = MagicMock()
        config.storage_root = str(tmp_path / "storage")
        import os; os.makedirs(config.storage_root, exist_ok=True)

        run_phase_b_pipeline(
            run_id=run_id,
            config=config,
            conn_factory=lambda: conn,
            gate2_io=MagicMock(),
            llm_client=stub_llm,
            agent=stub_agent,
        )

    # run_phase_v_pipeline must have been called once
    mock_phase_v.assert_called_once()
    call_args = mock_phase_v.call_args
    assert call_args[0][0] == run_id          # first arg is run_id


def test_phase_v_pipeline_not_called_without_agent(tmp_path):
    """If agent=None, Phase V must not be triggered (backward compat)."""
    run_id = str(uuid.uuid4())
    conn = _make_phase_b_conn(run_id, tmp_path)

    with patch("ai_dev_system.debate_pipeline.finalize_spec") as mock_spec, \
         patch("ai_dev_system.debate_pipeline.generate_task_graph") as mock_tg, \
         patch("ai_dev_system.debate_pipeline.run_gate_2") as mock_g2, \
         patch("ai_dev_system.debate_pipeline.beads_sync"), \
         patch("ai_dev_system.debate_pipeline.promote_output") as mock_promote, \
         patch("ai_dev_system.verification.pipeline.run_phase_v_pipeline") as mock_phase_v:

        mock_spec.return_value = MagicMock(files=["acceptance-criteria.md"])
        mock_tg.return_value = {"graph_version": 1, "tasks": []}
        g2_result = MagicMock(); g2_result.status = "approved"; g2_result.graph = {}
        mock_g2.return_value = g2_result
        mock_promote.return_value = str(uuid.uuid4())

        config = MagicMock()
        config.storage_root = str(tmp_path / "storage")
        import os; os.makedirs(config.storage_root, exist_ok=True)

        run_phase_b_pipeline(
            run_id=run_id,
            config=config,
            conn_factory=lambda: conn,
            gate2_io=MagicMock(),
            llm_client=MagicMock(),
            agent=None,  # No agent
        )

    mock_phase_v.assert_not_called()
```

Add this file to the File Map as: `tests/unit/test_debate_pipeline_phase_v.py` — unit test for Phase V wiring.

- [ ] **Step 2b: Run tests to verify they fail**

```bash
pytest tests/unit/test_debate_pipeline_phase_v.py -v
```

Expected: `AssertionError: Expected 'run_phase_v_pipeline' to have been called once. Called 0 times.` — confirms the wiring doesn't exist yet.

- [ ] **Step 3: Implement the wiring in debate_pipeline.py**

In `run_phase_b_pipeline`, locate the section after `run_execution` (lines 189-198). Change:

```python
    # Step 5: Execution (only if agent provided)
    execution_result = None
    if agent is not None:
        execution_result = run_execution(run_id, graph_artifact_id, config, agent)

    return PhaseBResult(
        run_id=run_id,
        graph_artifact_id=graph_artifact_id,
        execution_result=execution_result,
    )
```

To:

```python
    # Step 5: Execution (only if agent provided)
    execution_result = None
    if agent is not None:
        execution_result = run_execution(run_id, graph_artifact_id, config, agent)

        # Step 6: Phase V — Verification (only if execution succeeded)
        # Note: run_execution()'s terminal states are {"COMPLETED","FAILED","ABORTED","PAUSED_FOR_DECISION"}.
        # "SUCCESS" is not a run_status value used by the engine — COMPLETED is the success terminal.
        if execution_result.status == "COMPLETED":
            conn.execute(
                "UPDATE runs SET status = 'RUNNING_PHASE_V', last_activity_at = now() "
                "WHERE run_id = %s AND status = 'COMPLETED'",
                (run_id,),
            )
            if llm_client is not None:
                from ai_dev_system.verification.pipeline import run_phase_v_pipeline
                run_phase_v_pipeline(run_id, spec_artifact_id, config, conn, llm_client)

    return PhaseBResult(
        run_id=run_id,
        graph_artifact_id=graph_artifact_id,
        execution_result=execution_result,
    )
```

> **Note on `conn`:** In `run_phase_b_pipeline`, `conn = conn_factory()` is called at the top of the function. This conn is reused throughout Phase B. The `run_phase_v_pipeline` call uses this same conn. This is consistent with the existing pattern.

> **Note on `SUCCESS` status:** The execution engine (runner.py `_wait_for_terminal_state`) currently does NOT include `SUCCESS` in its terminal states. Check `engine/loop.py` or `engine/worker.py` to see if `SUCCESS` is set when all tasks complete. If the terminal state for a fully-completed execution is `COMPLETED` rather than `SUCCESS`, adjust the condition accordingly. Run `grep -r "SUCCESS" src/ai_dev_system/engine/` to confirm.

- [ ] **Step 4: Run existing debate_pipeline tests**

```bash
pytest tests/integration/test_debate_pipeline.py tests/integration/test_spec_pipeline_phase_b.py -v
```

Expected: all existing tests still pass (the Phase V path only triggers when agent is not None AND execution returns SUCCESS).

- [ ] **Step 5: Run full test suite**

```bash
pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/ai_dev_system/debate_pipeline.py
git commit -m "feat: wire run_phase_v_pipeline() into run_phase_b_pipeline() after successful execution"
```

---

## Task 9: Gate 3 Skill

**Files:**
- Create: `skills/review-verification.md`

- [ ] **Step 1: Write the skill file**

```markdown
# Review Verification

**Invoked via:** `/review-verification <run_id>`

You are the Gate 3 review skill. Your job: read the VERIFICATION_REPORT for a run, present the full report to the human, collect skip/abort/fix decisions for any FAIL criteria, then call `finalize_gate3()` to advance or close the pipeline.

---

## Setup

On invocation, receive `run_id` from the argument. Query the DB for the active VERIFICATION_REPORT:

\```python
import json
import psycopg
import psycopg.rows
from ai_dev_system.db.connection import get_connection

conn = get_connection()
art = conn.execute(
    """
    SELECT content_ref FROM artifacts
    WHERE run_id = %s AND artifact_type = 'VERIFICATION_REPORT' AND status = 'ACTIVE'
    ORDER BY version DESC LIMIT 1
    """,
    (run_id,),
).fetchone()

with open(art["content_ref"] + "/verification_report.json", encoding="utf-8") as f:
    report = json.load(f)

fail_criteria = [c for c in report["criteria"] if c["verdict"] == "FAIL"]
pass_criteria = [c for c in report["criteria"] if c["verdict"] == "PASS"]
attempt = report["attempt"]
\```

---

## State Machine

### PRESENT

Render the full report in a single message. Show FAIL criteria with full detail, PASS criteria summarized.

**Format:**
\```
✅ Verification Report — Attempt {attempt}/3

✅ PASS ({N}): AC-1, AC-3, AC-5

❌ FAIL ({M}):

❌ AC-4: "User can search posts by tag"
   Reasoning: Search API returns 200 but does not filter by tag
   Evidence: task-6 output — GET /posts?tag=python returns all posts
   Confidence: 0.92

❌ AC-6: "Coverage ≥ 80%"
   Reasoning: pytest-cov report = 71%
   Evidence: task-6 verification_steps output
   Confidence: 0.99

→ Confirm to create remediation tasks, or "skip AC-4" / "abort".
\```

**All-pass fast path:** If no FAIL criteria → skip COLLECT_FAILS entirely:
\```
✅ All {N} criteria PASS. Confirm completion? (yes/abort)
\```

Transition to **COLLECT_FAILS** (or CONFIRM_PASS if no fails).

---

### COLLECT_FAILS

Collect one decision per FAIL criterion. Accept batched input in a single message.

| User says | Parsed as |
|-----------|-----------|
| `"ok create remediation"` / `"fix all"` | All FAILs → remediation (no SKIP) |
| `"skip AC-4, fix AC-6"` | AC-4 → SKIP, AC-6 → remediation |
| `"abort"` | run → ABORTED |
| `"show evidence AC-4"` | Display full evidence for AC-4, stay in COLLECT_FAILS |
| Ambiguous | Ask clarifying question, do not record until confirmed |

After parsing, confirm what was recorded and list remaining undecided criteria.

Transition to **CONFIRM** when all FAIL criteria have a decision.

---

### CONFIRM

Show summary before calling finalize_gate3():

\```
📝 Confirm:
  AC-4 → ⏭️  Skip
  AC-6 → 🔧 Create remediation task

Continue? (remediation will re-run the execution loop)
\```

- `"ok"` / `"confirm"` / `"yes"` → call `finalize_gate3()` (see below)
- `"edit"` / `"change"` → return to COLLECT_FAILS

### CONFIRM_PASS (all-pass fast path)

- `"yes"` / `"confirm"` → call `finalize_gate3(decisions=[])` → run transitions to COMPLETED
- `"abort"` → call `finalize_gate3(decisions=[Gate3Decision("*", "ABORT")])` ... actually prompt: "All criteria passed — are you sure you want to abort? (yes to abort, no to complete)"

---

### PAUSED_AT_GATE_3B (attempt ≥ 3, still failing)

When `finalize_gate3()` transitions to PAUSED_AT_GATE_3B, or if the run is already in this state when the skill is invoked:

\```
⚠️ Already attempted 3 times. Still failing: AC-6

Options:
  A. Continue — add 1 more attempt
  B. Skip failing criteria — mark as skip, complete the run
  C. Abort — stop this run entirely
\```

- A → call `finalize_gate3(decisions=[])` after resetting attempt counter (or human manually sets run.status = RUNNING_PHASE_V and invokes `/review-verification` again)
- B → call `finalize_gate3(decisions=[Gate3Decision(cid, "SKIP") for each fail])`
- C → call `finalize_gate3(decisions=[Gate3Decision(fail_criteria[0].criterion_id, "ABORT")])`

Max 3 is a **soft limit** — option A lets the human extend.

---

## Calling finalize_gate3()

After CONFIRM:

\```python
from ai_dev_system.gate.gate3_bridge import finalize_gate3, Gate3Decision
from ai_dev_system.config import Config

config = Config.from_env()
decisions = [
    Gate3Decision(criterion_id=cid, action=action)
    for cid, action in user_decisions.items()
    # only include FAIL criteria that were SKIP or ABORT
    # pass criteria are implicit — do not include them
]

result = finalize_gate3(run_id, decisions, config.storage_root, conn)

if result.aborted:
    print(f"Run {run_id} → ABORTED")
elif result.has_remediation:
    print(f"Run {run_id} → RUNNING_PHASE_V (remediation queued, attempt {attempt+1})")
    print("Re-run execution with the remediation graph, then invoke /review-verification again.")
else:
    print(f"Run {run_id} → COMPLETED ✅")
\```

---

## Principles

- Present full picture immediately — no criterion-by-criterion interruption (consistent with Gate 1)
- CONFIRM is mandatory before remediation — remediation cannot be undone
- `Gate3Decision` only contains FAIL decisions — PASS is implicit
- Attempt ≥ 3 is a soft limit — human can extend by choosing option A
```

- [ ] **Step 2: Verify skill is loadable**

Open a Claude Code session and run `/review-verification --help` (or just invoke it with a dummy run_id). Verify the skill file is read and the setup block runs without import errors in a Python environment with the package installed.

- [ ] **Step 3: Commit**

```bash
git add skills/review-verification.md
git commit -m "feat: add /review-verification Gate 3 skill"
```

---

## Task 10: Final Verification

- [ ] **Step 1: Run full test suite**

```bash
pytest tests/ -v --tb=short
```

Expected: all tests pass, including 88 pre-existing tests + new tests.

- [ ] **Step 2: Verify migration is idempotent**

```bash
psql $DATABASE_URL -f docs/schema/migrations/v4-verification.sql
```

Expected: no errors (IF NOT EXISTS guards).

- [ ] **Step 3: Check no import cycles**

```bash
python -c "from ai_dev_system.verification.pipeline import run_phase_v_pipeline; print('OK')"
python -c "from ai_dev_system.gate.gate3_bridge import finalize_gate3; print('OK')"
```

Expected: both print `OK`.

- [ ] **Step 4: Final commit if anything was missed**

```bash
git status
# If any files are untracked or modified, add and commit them
```

---

## Implementation Notes

1. **`EventRepo.insert` signature** — confirmed `payload=` (not `extra=`). All calls in `gate3_bridge.py` use `payload=` as shown.

2. **Terminal state is `COMPLETED` not `SUCCESS`** — `runner.py` terminal states: `{"COMPLETED","FAILED","ABORTED","PAUSED_FOR_DECISION"}`. The wiring in `debate_pipeline.py` checks `execution_result.status == "COMPLETED"`. No engine changes needed.

3. **`promote_output` + autocommit** — `debate_pipeline.py` creates `conn = conn_factory()` where `conn_factory` in tests is `lambda: db_conn` (`autocommit=False`). In production, callers must use `autocommit=False`. Any `psycopg.connect(..., autocommit=False)` conn satisfies `promote_output`'s transaction requirement — `FOR UPDATE` works in any open transaction, and an implicit transaction begins with the first statement on an `autocommit=False` conn.

4. **Acceptance criteria parsing** — `_parse_acceptance_criteria` uses a regex that matches `AC-1:`, `**AC-1**:`, `## AC-1 —` etc. If `acceptance-criteria.md` uses a different format, adjust the regex in `pipeline.py`. Test with actual spec bundle files.

