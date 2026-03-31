# Beads Final Report (Phase 5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Phase 5 of workflow-v2 — real-time per-task Beads progress updates (`BD->>BD`) and a final audit trail report to the user (`BD->>U`).

**Architecture:** Self-tracking via events table. `beads_update_task()` called by `worker_loop()` after each task commits (calls `bd close` + prints progress). `write_beads_final_artifact()` called by `run_phase_b_pipeline()` after execution finishes (prints terminal summary + writes JSON file). `beads_sync()` gains a `BEADS_SYNC_START` event so total task count is always queryable.

**Tech Stack:** Python 3.12, psycopg3, dataclasses, subprocess (`bd` CLI), pytest, unittest.mock

---

## Working Directory

All file paths are relative to the repo root (`e:\Work\ai-development-system`).

---

## File Map

### New files
| File | Responsibility |
|------|----------------|
| `src/ai_dev_system/beads/report.py` | `BeadsFinalReport` dataclass, `_get_total_tasks()`, `print_beads_progress()`, `beads_update_task()`, `write_beads_final_artifact()`, `_print_final_summary()` |
| `docs/schema/migrations/v5-beads-report.sql` | `ALTER TYPE event_type ADD ... 'BEADS_SYNC_START'`; `ALTER TYPE artifact_type ADD ... 'BEADS_FINAL_REPORT'` |
| `tests/unit/beads/__init__.py` | Package marker |
| `tests/unit/beads/test_report.py` | Unit tests for all report.py functions (DB mocked) |
| `tests/integration/test_beads_report.py` | Integration tests (real DB, subprocess mocked) |

### Modified files
| File | Change |
|------|--------|
| `src/ai_dev_system/beads/sync.py` | Add `EventRepo(conn).insert(run_id, "BEADS_SYNC_START", ...)` at top of `beads_sync()` |
| `src/ai_dev_system/engine/worker.py` | (1) `_execute_task()` line 95: add `task_id` to `TASK_COMPLETED` payload; (2) `_promote_for_runner()` line 297-298: add `task_id` to `TASK_COMPLETED` payload; (3) `worker_loop()` after COMMIT: call `beads_update_task()` on success |
| `src/ai_dev_system/debate_pipeline.py` | After `run_execution()` returns (line 192): call `write_beads_final_artifact(run_id, config.storage_root, conn)` |

---

## Task 1: v5 Schema Migration

**Files:**
- Create: `docs/schema/migrations/v5-beads-report.sql`

- [ ] **Step 1: Write the migration file**

```sql
-- v5-beads-report.sql
-- Adds BEADS_SYNC_START event type and BEADS_FINAL_REPORT artifact type.
-- Safe to run after v4-verification.sql.
-- Note: TASK_COMPLETED already exists in the base schema — no-op needed.

ALTER TYPE event_type    ADD VALUE IF NOT EXISTS 'BEADS_SYNC_START';
ALTER TYPE artifact_type ADD VALUE IF NOT EXISTS 'BEADS_FINAL_REPORT';
```

- [ ] **Step 2: Apply the migration**

```bash
psql $DATABASE_URL -f docs/schema/migrations/v5-beads-report.sql
```

Expected: two `ALTER TYPE` lines, no errors.

- [ ] **Step 3: Commit**

```bash
git add docs/schema/migrations/v5-beads-report.sql
git commit -m "feat(schema): add BEADS_SYNC_START event type and BEADS_FINAL_REPORT artifact type (v5)"
```

---

## Task 2: report.py Foundation — Dataclass + _get_total_tasks + print_beads_progress

**Files:**
- Create: `src/ai_dev_system/beads/__init__.py` (already exists — skip if present)
- Create: `tests/unit/beads/__init__.py`
- Create: `src/ai_dev_system/beads/report.py` (partial — dataclass + query helpers)
- Test: `tests/unit/beads/test_report.py` (partial)

- [ ] **Step 1: Create test package marker**

```bash
# Create if not exists
touch tests/unit/beads/__init__.py
```

- [ ] **Step 2: Write failing tests for dataclass and _get_total_tasks**

Create `tests/unit/beads/test_report.py`:

```python
import json
import pytest
from unittest.mock import MagicMock
from ai_dev_system.beads.report import BeadsFinalReport, _get_total_tasks, print_beads_progress


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_conn(events: list[dict]) -> MagicMock:
    """Stub conn that returns `events` for any execute().fetchall() / fetchone()."""
    conn = MagicMock()
    # fetchall returns event rows
    conn.execute.return_value.fetchall.return_value = events
    # fetchone used by _get_total_tasks — set a sane default; override per test
    conn.execute.return_value.fetchone.return_value = None
    return conn


def _make_sync_start_conn(total: int) -> MagicMock:
    conn = MagicMock()
    row = {"payload": {"total_tasks": total}}
    conn.execute.return_value.fetchone.return_value = row
    return conn


# ── BeadsFinalReport ───────────────────────────────────────────────────────────

def test_beads_final_report_fields():
    r = BeadsFinalReport(
        run_id="r1",
        total_tasks=3,
        completed_tasks=2,
        sync_warnings=[{"task_id": "T1", "stderr": "err"}],
        task_timeline=[{"task_id": "T1", "completed_at": "2026-03-31T10:00:00+00:00"}],
        generated_at="2026-03-31T10:01:00+00:00",
    )
    assert r.run_id == "r1"
    assert r.total_tasks == 3
    assert r.completed_tasks == 2
    assert len(r.sync_warnings) == 1
    assert len(r.task_timeline) == 1


# ── _get_total_tasks ──────────────────────────────────────────────────────────

def test_get_total_tasks_reads_from_sync_start_event():
    conn = _make_sync_start_conn(total=6)
    assert _get_total_tasks("r1", conn) == 6


def test_get_total_tasks_missing_event_returns_zero(caplog):
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = None
    result = _get_total_tasks("r1", conn)
    assert result == 0
    assert "BEADS_SYNC_START" in caplog.text


# ── print_beads_progress ───────────────────────────────────────────────────────

def test_print_progress_no_warnings(capsys):
    events = [
        {"event_type": "TASK_COMPLETED", "payload": {"task_id": "T1"}, "occurred_at": None},
        {"event_type": "TASK_COMPLETED", "payload": {"task_id": "T2"}, "occurred_at": None},
    ]
    conn = _make_conn(events)
    # _get_total_tasks also called — give it a fetchone via side_effect
    conn.execute.return_value.fetchone.return_value = {"payload": {"total_tasks": 5}}

    print_beads_progress("r1", conn)

    out = capsys.readouterr().out
    assert "[2/5 tasks done]" in out
    assert "open=3" in out
    assert "closed=2" in out
    assert "warning" not in out.lower()


def test_print_progress_with_warnings(capsys):
    events = [
        {"event_type": "TASK_COMPLETED", "payload": {"task_id": "T1"}, "occurred_at": None},
        {"event_type": "BEADS_SYNC_WARNING", "payload": {"task_id": "T1", "stderr": "err"},
         "occurred_at": None},
    ]
    conn = _make_conn(events)
    conn.execute.return_value.fetchone.return_value = {"payload": {"total_tasks": 3}}

    print_beads_progress("r1", conn)

    out = capsys.readouterr().out
    assert "⚠️" in out
    assert "1 sync warning(s)" in out


def test_print_progress_zero_completed(capsys):
    conn = _make_conn([])
    conn.execute.return_value.fetchone.return_value = {"payload": {"total_tasks": 5}}

    print_beads_progress("r1", conn)

    out = capsys.readouterr().out
    assert "[0/5 tasks done]" in out
    assert "open=5" in out
    assert "closed=0" in out
```

- [ ] **Step 3: Run to confirm tests fail**

```bash
pytest tests/unit/beads/test_report.py -v
```

Expected: `ModuleNotFoundError: No module named 'ai_dev_system.beads.report'`

- [ ] **Step 4: Implement dataclass + _get_total_tasks + print_beads_progress**

Create `src/ai_dev_system/beads/report.py`:

```python
# src/ai_dev_system/beads/report.py
import json
import logging
import subprocess
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from ai_dev_system.db.repos.events import EventRepo

logger = logging.getLogger(__name__)


@dataclass
class BeadsFinalReport:
    run_id: str
    total_tasks: int
    completed_tasks: int
    sync_warnings: list[dict]   # [{"task_id": str, "stderr": str}]
    task_timeline: list[dict]   # [{"task_id": str, "completed_at": str}]
    generated_at: str           # ISO UTC


def _get_total_tasks(run_id: str, conn) -> int:
    """Read total task count from BEADS_SYNC_START event payload."""
    row = conn.execute("""
        SELECT payload FROM events
        WHERE run_id = %s AND event_type = 'BEADS_SYNC_START'
        ORDER BY occurred_at LIMIT 1
    """, (run_id,)).fetchone()
    if row is None:
        logger.warning("beads: BEADS_SYNC_START event not found for run %s", run_id)
        return 0
    return row["payload"]["total_tasks"]


def print_beads_progress(run_id: str, conn) -> None:
    """Print real-time progress line to terminal. Reads from events table only."""
    rows = conn.execute("""
        SELECT event_type, payload, occurred_at
        FROM events
        WHERE run_id = %s
          AND event_type IN ('TASK_COMPLETED', 'BEADS_SYNC_WARNING')
        ORDER BY occurred_at
    """, (run_id,)).fetchall()

    completed = [r for r in rows if r["event_type"] == "TASK_COMPLETED"]
    warnings  = [r for r in rows if r["event_type"] == "BEADS_SYNC_WARNING"]
    total = _get_total_tasks(run_id, conn)

    warning_str = f"  ⚠️  {len(warnings)} sync warning(s)" if warnings else ""
    open_count = max(0, total - len(completed))
    print(
        f"[{len(completed)}/{total} tasks done]"
        f"  open={open_count}"
        f"  closed={len(completed)}"
        f"{warning_str}"
    )
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
pytest tests/unit/beads/test_report.py -v -k "not update_task and not write_final and not warning_on_close and not bd_not_found"
```

Expected: all targeted tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/ai_dev_system/beads/report.py tests/unit/beads/__init__.py tests/unit/beads/test_report.py
git commit -m "feat(beads): add BeadsFinalReport dataclass, _get_total_tasks, print_beads_progress"
```

---

## Task 3: beads_update_task

**Files:**
- Modify: `src/ai_dev_system/beads/report.py` (add function)
- Modify: `tests/unit/beads/test_report.py` (add tests)

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/beads/test_report.py`:

```python
from unittest.mock import patch
from ai_dev_system.beads.report import beads_update_task


def test_beads_update_task_calls_bd_close():
    """beads_update_task calls bd close <task_id>."""
    with patch("subprocess.run") as mock_run, \
         patch("ai_dev_system.beads.report.print_beads_progress"):
        mock_run.return_value = MagicMock(returncode=0, stderr=b"")
        conn = _make_conn([])
        conn.execute.return_value.fetchone.return_value = {"payload": {"total_tasks": 3}}
        beads_update_task("r1", "TASK-1", conn)
        mock_run.assert_called_once_with(
            ["bd", "close", "TASK-1"], capture_output=True
        )


def test_beads_update_task_logs_warning_on_close_fail(caplog):
    """bd close returncode=1 → warning logged, no exception raised."""
    with patch("subprocess.run") as mock_run, \
         patch("ai_dev_system.beads.report.print_beads_progress"):
        mock_run.return_value = MagicMock(returncode=1, stderr=b"task not found")
        conn = _make_conn([])
        conn.execute.return_value.fetchone.return_value = {"payload": {"total_tasks": 3}}
        beads_update_task("r1", "TASK-1", conn)  # must not raise
    assert "task not found" in caplog.text


def test_beads_update_task_skips_bd_not_found(caplog):
    """bd not in PATH → warning logged, no exception raised."""
    with patch("subprocess.run", side_effect=FileNotFoundError("bd not found")), \
         patch("ai_dev_system.beads.report.print_beads_progress"):
        conn = _make_conn([])
        conn.execute.return_value.fetchone.return_value = {"payload": {"total_tasks": 3}}
        beads_update_task("r1", "TASK-1", conn)  # must not raise
    assert "not in PATH" in caplog.text


def test_beads_update_task_does_not_insert_event():
    """beads_update_task must NOT insert a TASK_COMPLETED event (worker already does)."""
    with patch("subprocess.run") as mock_run, \
         patch("ai_dev_system.beads.report.print_beads_progress"):
        mock_run.return_value = MagicMock(returncode=0, stderr=b"")
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {"payload": {"total_tasks": 3}}
        beads_update_task("r1", "TASK-1", conn)
        # EventRepo.insert is only called via conn.execute — verify no INSERT INTO events
        insert_calls = [
            str(call) for call in conn.execute.call_args_list
            if "INSERT INTO events" in str(call)
        ]
        assert insert_calls == [], "beads_update_task must not insert events"
```

- [ ] **Step 2: Run to confirm tests fail**

```bash
pytest tests/unit/beads/test_report.py::test_beads_update_task_calls_bd_close -v
```

Expected: `ImportError: cannot import name 'beads_update_task'`

- [ ] **Step 3: Implement beads_update_task**

Append to `src/ai_dev_system/beads/report.py`:

```python
def beads_update_task(run_id: str, task_id: str, conn) -> None:
    """Call bd close <task_id> and print progress. Called after task transaction commits.
    Does NOT insert TASK_COMPLETED — worker.py already does that.
    Never raises — execution must not fail due to reporting.
    """
    try:
        result = subprocess.run(["bd", "close", task_id], capture_output=True)
        if result.returncode != 0:
            logger.warning("beads: close failed for %s: %s",
                           task_id, result.stderr.decode(errors="replace"))
    except FileNotFoundError:
        logger.warning("beads: bd not in PATH, skipping close for %s", task_id)

    print_beads_progress(run_id, conn)
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/unit/beads/test_report.py -v -k "update_task"
```

Expected: all 4 `test_beads_update_task_*` tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/beads/report.py tests/unit/beads/test_report.py
git commit -m "feat(beads): add beads_update_task (bd close + progress print)"
```

---

## Task 4: write_beads_final_artifact

**Files:**
- Modify: `src/ai_dev_system/beads/report.py` (add function + `_print_final_summary`)
- Modify: `tests/unit/beads/test_report.py` (add tests)

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/beads/test_report.py`:

```python
import os
from datetime import timezone
from ai_dev_system.beads.report import write_beads_final_artifact


def _make_completed_event(task_id: str):
    from datetime import datetime, timezone
    return {
        "event_type": "TASK_COMPLETED",
        "payload": {"task_id": task_id},
        "occurred_at": datetime(2026, 3, 31, 10, 0, 0, tzinfo=timezone.utc),
    }


def _make_warning_event(task_id: str):
    return {
        "event_type": "BEADS_SYNC_WARNING",
        "payload": {"task_id": task_id, "stderr": "some error"},
        "occurred_at": None,
    }


def test_write_final_report_file_written(tmp_path):
    """write_beads_final_artifact writes JSON to <storage_root>/<run_id>/beads_final_report.json."""
    events = [_make_completed_event("T1"), _make_completed_event("T2")]
    conn = _make_conn(events)
    conn.execute.return_value.fetchone.return_value = {"payload": {"total_tasks": 2}}

    path = write_beads_final_artifact("run-abc", str(tmp_path), conn)

    assert os.path.exists(path)
    assert path.endswith("beads_final_report.json")
    data = json.loads(open(path).read())
    assert data["run_id"] == "run-abc"
    assert data["completed_tasks"] == 2
    assert data["total_tasks"] == 2


def test_write_final_report_total_tasks_from_sync_start(tmp_path):
    """total_tasks comes from BEADS_SYNC_START event, not hardcoded."""
    events = [_make_completed_event("T1")]
    conn = _make_conn(events)
    conn.execute.return_value.fetchone.return_value = {"payload": {"total_tasks": 5}}

    write_beads_final_artifact("run-abc", str(tmp_path), conn)

    data = json.loads(open(tmp_path / "run-abc" / "beads_final_report.json").read())
    assert data["total_tasks"] == 5


def test_write_final_report_includes_sync_warnings(tmp_path):
    """sync_warnings from BEADS_SYNC_WARNING events appear in report."""
    events = [_make_completed_event("T1"), _make_warning_event("T2")]
    conn = _make_conn(events)
    conn.execute.return_value.fetchone.return_value = {"payload": {"total_tasks": 2}}

    write_beads_final_artifact("run-abc", str(tmp_path), conn)

    data = json.loads(open(tmp_path / "run-abc" / "beads_final_report.json").read())
    assert len(data["sync_warnings"]) == 1
    assert data["sync_warnings"][0]["task_id"] == "T2"


def test_write_final_report_timeline_uses_occurred_at(tmp_path):
    """task_timeline entries have correct task_id and completed_at from occurred_at."""
    events = [_make_completed_event("T1")]
    conn = _make_conn(events)
    conn.execute.return_value.fetchone.return_value = {"payload": {"total_tasks": 1}}

    write_beads_final_artifact("run-abc", str(tmp_path), conn)

    data = json.loads(open(tmp_path / "run-abc" / "beads_final_report.json").read())
    assert data["task_timeline"][0]["task_id"] == "T1"
    assert "10:00" in data["task_timeline"][0]["completed_at"]


def test_write_final_report_prints_summary(tmp_path, capsys):
    """Terminal summary (BD->>U) is printed."""
    events = [_make_completed_event("T1")]
    conn = _make_conn(events)
    conn.execute.return_value.fetchone.return_value = {"payload": {"total_tasks": 1}}

    write_beads_final_artifact("run-abc", str(tmp_path), conn)

    out = capsys.readouterr().out
    assert "Beads Audit Trail" in out
    assert "1/1 tasks" in out
```

- [ ] **Step 2: Run to confirm tests fail**

```bash
pytest tests/unit/beads/test_report.py -v -k "write_final"
```

Expected: `ImportError: cannot import name 'write_beads_final_artifact'`

- [ ] **Step 3: Implement write_beads_final_artifact + _print_final_summary**

Append to `src/ai_dev_system/beads/report.py`:

```python
def write_beads_final_artifact(run_id: str, storage_root: str, conn) -> str:
    """Write Beads final report JSON + print terminal summary (BD->>U).
    Called once in run_phase_b_pipeline() after run_execution() returns.
    Returns path to written file.
    """
    rows = conn.execute("""
        SELECT event_type, payload, occurred_at
        FROM events
        WHERE run_id = %s
          AND event_type IN ('TASK_COMPLETED', 'BEADS_SYNC_WARNING')
        ORDER BY occurred_at
    """, (run_id,)).fetchall()

    completed_rows = [r for r in rows if r["event_type"] == "TASK_COMPLETED"]
    warning_rows   = [r for r in rows if r["event_type"] == "BEADS_SYNC_WARNING"]
    total = _get_total_tasks(run_id, conn)

    report = BeadsFinalReport(
        run_id=run_id,
        total_tasks=total,
        completed_tasks=len(completed_rows),
        sync_warnings=[r["payload"] for r in warning_rows],
        task_timeline=[
            {"task_id": r["payload"]["task_id"],
             "completed_at": r["occurred_at"].isoformat()}
            for r in completed_rows
        ],
        generated_at=datetime.now(timezone.utc).isoformat(),
    )

    artifact_path = Path(storage_root) / run_id / "beads_final_report.json"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(json.dumps(asdict(report), indent=2))

    _print_final_summary(report)
    return str(artifact_path)


def _print_final_summary(report: BeadsFinalReport) -> None:
    """Print BD->>U terminal summary."""
    warning_line = (
        f"  Warnings  : {len(report.sync_warnings)} sync warning(s)\n"
        if report.sync_warnings else
        "  Warnings  : none\n"
    )
    timeline = "\n".join(
        f"    {t['task_id']:<12} done  {t['completed_at'][11:16]}"
        for t in report.task_timeline
    )
    print(
        f"\n{'═' * 38}\n"
        f"  Beads Audit Trail + Thống kê\n"
        f"{'═' * 38}\n"
        f"  Completed : {report.completed_tasks}/{report.total_tasks} tasks\n"
        f"{warning_line}"
        f"  Timeline  :\n{timeline}\n"
        f"{'═' * 38}\n"
    )
```

- [ ] **Step 4: Run all unit tests**

```bash
pytest tests/unit/beads/test_report.py -v
```

Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/beads/report.py tests/unit/beads/test_report.py
git commit -m "feat(beads): add write_beads_final_artifact and _print_final_summary"
```

---

## Task 5: Modify beads_sync() — BEADS_SYNC_START event

**Files:**
- Modify: `src/ai_dev_system/beads/sync.py`
- Modify: `tests/integration/test_beads_sync.py` (add one test)

- [ ] **Step 1: Write failing test**

Append to `tests/integration/test_beads_sync.py`:

```python
def test_beads_sync_inserts_sync_start_event(conn, seed_run):
    """beads_sync() inserts a BEADS_SYNC_START event with total_tasks count."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr=b"")
        beads_sync(seed_run, SAMPLE_GRAPH, conn)

    row = conn.execute("""
        SELECT payload FROM events
        WHERE run_id = %s AND event_type = 'BEADS_SYNC_START'
    """, (seed_run,)).fetchone()
    assert row is not None
    assert row["payload"]["total_tasks"] == 3
```

- [ ] **Step 2: Run to confirm test fails**

```bash
pytest tests/integration/test_beads_sync.py::test_beads_sync_inserts_sync_start_event -v
```

Expected: FAIL — no BEADS_SYNC_START row found

- [ ] **Step 3: Modify beads_sync()**

In `src/ai_dev_system/beads/sync.py`, add the `BEADS_SYNC_START` insert at the top of `beads_sync()`:

```python
def beads_sync(run_id: str, graph: dict, conn) -> None:
    """Sync task graph to Beads (bd CLI). Non-blocking: errors are logged, never raised."""
    # Record total task count for Phase 5 progress tracking
    if conn is not None:
        try:
            EventRepo(conn).insert(run_id, "BEADS_SYNC_START", "system",
                                   payload={"total_tasks": len(graph.get("tasks", []))})
        except Exception as e:
            logger.warning("beads_sync: failed to insert BEADS_SYNC_START: %s", e)

    tasks = _topological_sort(graph.get("tasks", []))
    # ... rest unchanged
```

- [ ] **Step 4: Run test to confirm it passes**

```bash
pytest tests/integration/test_beads_sync.py -v
```

Expected: all tests PASS (including the new one)

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/beads/sync.py tests/integration/test_beads_sync.py
git commit -m "feat(beads): insert BEADS_SYNC_START event in beads_sync() for progress tracking"
```

---

## Task 6: Modify worker.py — task_id payload + beads_update_task call

**Files:**
- Modify: `src/ai_dev_system/engine/worker.py`
- Test: existing worker tests (run to confirm no regression)

- [ ] **Step 1: Update TASK_COMPLETED payload in _execute_task() (line ~95)**

In `src/ai_dev_system/engine/worker.py`, find the line:
```python
event_repo.insert(run_id, "TASK_COMPLETED", f"worker:{worker_id}", task["task_run_id"], {})
```

Change `{}` to `{"task_id": task["task_id"]}`:
```python
event_repo.insert(run_id, "TASK_COMPLETED", f"worker:{worker_id}",
                  task["task_run_id"], {"task_id": task["task_id"]})
```

- [ ] **Step 2: Update TASK_COMPLETED payload in _promote_for_runner() (line ~297)**

Find:
```python
EventRepo(conn).insert(run_id, "TASK_COMPLETED", f"worker:{worker_id}",
                       task_run_id=task["task_run_id"])
```

Change to:
```python
EventRepo(conn).insert(run_id, "TASK_COMPLETED", f"worker:{worker_id}",
                       task_run_id=task["task_run_id"],
                       payload={"task_id": task["task_id"]})
```

- [ ] **Step 3: Add beads_update_task() call in worker_loop() after COMMIT**

In `worker_loop()`, find the promote block:
```python
            try:
                conn.execute("BEGIN")
                if not result.success:
                    _handle_failure(...)
                else:
                    _promote_for_runner(conn, config, task, result, worker_id, run_id)
                conn.execute("COMMIT")
            except Exception:
                ...
```

Add `beads_update_task` call **after** the `COMMIT` and **outside** the try/except, so it only runs on success and cannot roll back the transaction:

```python
            try:
                conn.execute("BEGIN")
                if not result.success:
                    _handle_failure(conn, config, task, result.error or "unknown",
                                    worker_id, run_id, error_type="EXECUTION_ERROR")
                else:
                    _promote_for_runner(conn, config, task, result, worker_id, run_id)
                conn.execute("COMMIT")
            except Exception:
                logger.exception("Promote/failure error for task %s", task["task_id"])
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
            else:
                # After-commit Beads update — runs only when no exception was raised
                if result.success:
                    try:
                        from ai_dev_system.beads.report import beads_update_task
                        beads_update_task(run_id, task["task_id"], conn)
                    except Exception:
                        logger.exception("beads_update_task failed for %s", task["task_id"])
```

Note: The `else` clause of a `try/except` runs only when no exception was raised — this is the safest way to call post-commit code.

- [ ] **Step 4: Run existing worker tests**

```bash
pytest tests/ -k "worker" -v
```

Expected: all existing worker tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/engine/worker.py
git commit -m "feat(worker): add task_id to TASK_COMPLETED payload; call beads_update_task post-commit"
```

---

## Task 7: Wire write_beads_final_artifact in debate_pipeline.py

**Files:**
- Modify: `src/ai_dev_system/debate_pipeline.py`

- [ ] **Step 1: Add write_beads_final_artifact call after run_execution()**

In `src/ai_dev_system/debate_pipeline.py`, find (around line 192):

```python
    if agent is not None:
        execution_result = run_execution(run_id, graph_artifact_id, config, agent)

        # Step 6: Phase V — Verification (only if execution succeeded)
```

Add the Phase 5 call between `run_execution()` and the Phase V block:

```python
    if agent is not None:
        execution_result = run_execution(run_id, graph_artifact_id, config, agent)

        # Step 5: Phase 5 — Beads final report (BD->>U)
        try:
            from ai_dev_system.beads.report import write_beads_final_artifact
            write_beads_final_artifact(run_id, config.storage_root, conn)
        except Exception:
            logger.exception("write_beads_final_artifact failed for run %s", run_id)

        # Step 6: Phase V — Verification (only if execution succeeded)
        if execution_result.status == "COMPLETED":
```

- [ ] **Step 2: Run existing debate pipeline tests**

```bash
pytest tests/ -k "phase_b or debate_pipeline" -v
```

Expected: all existing tests PASS (the new call is wrapped in try/except so test stubs without beads_sync_start event won't break)

- [ ] **Step 3: Commit**

```bash
git add src/ai_dev_system/debate_pipeline.py
git commit -m "feat(pipeline): call write_beads_final_artifact after run_execution (Phase 5 BD->>U)"
```

---

## Task 8: Integration Tests

**Files:**
- Create: `tests/integration/test_beads_report.py`

- [ ] **Step 1: Confirm v5 migration is applied**

```bash
psql $DATABASE_URL -c "SELECT 'BEADS_SYNC_START'::event_type;"
```

Expected: one row returned. If you get `ERROR: invalid input value for enum event_type`, run Task 1 Step 2 first.

- [ ] **Step 2: Write integration tests**

Create `tests/integration/test_beads_report.py`:

```python
# tests/integration/test_beads_report.py
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from ai_dev_system.beads.report import (
    beads_update_task,
    write_beads_final_artifact,
    _get_total_tasks,
)
from ai_dev_system.db.repos.events import EventRepo


def _insert_event(conn, run_id: str, event_type: str, payload: dict):
    EventRepo(conn).insert(run_id, event_type, "test", payload=payload)


def _insert_sync_start(conn, run_id: str, total: int):
    _insert_event(conn, run_id, "BEADS_SYNC_START", {"total_tasks": total})


def _insert_task_completed(conn, run_id: str, task_id: str):
    _insert_event(conn, run_id, "TASK_COMPLETED", {"task_id": task_id})


# ── _get_total_tasks (integration) ────────────────────────────────────────────

def test_get_total_tasks_from_sync_start_event(conn, seed_run):
    _insert_sync_start(conn, seed_run, total=4)
    assert _get_total_tasks(seed_run, conn) == 4


# ── beads_update_task (integration) ──────────────────────────────────────────

def test_beads_update_task_prints_progress(conn, seed_run, capsys):
    """After 1 of 3 tasks: prints [1/3 tasks done]."""
    _insert_sync_start(conn, seed_run, total=3)
    _insert_task_completed(conn, seed_run, "T1")

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr=b"")
        beads_update_task(seed_run, "T1", conn)

    out = capsys.readouterr().out
    assert "[1/3 tasks done]" in out


def test_beads_update_task_does_not_double_insert_events(conn, seed_run):
    """beads_update_task must not insert TASK_COMPLETED events (worker already does)."""
    _insert_sync_start(conn, seed_run, total=1)
    _insert_task_completed(conn, seed_run, "T1")  # simulates worker insert

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr=b"")
        beads_update_task(seed_run, "T1", conn)

    rows = conn.execute("""
        SELECT COUNT(*) as cnt FROM events
        WHERE run_id = %s AND event_type = 'TASK_COMPLETED'
    """, (seed_run,)).fetchone()
    assert rows["cnt"] == 1  # exactly 1, not 2


# ── write_beads_final_artifact (integration) ──────────────────────────────────

def test_full_flow_3_tasks(conn, seed_run, tmp_path):
    """3 TASK_COMPLETED events → final report has completed_tasks=3."""
    _insert_sync_start(conn, seed_run, total=3)
    _insert_task_completed(conn, seed_run, "T1")
    _insert_task_completed(conn, seed_run, "T2")
    _insert_task_completed(conn, seed_run, "T3")

    path = write_beads_final_artifact(seed_run, str(tmp_path), conn)

    data = json.loads(Path(path).read_text())
    assert data["completed_tasks"] == 3
    assert data["total_tasks"] == 3
    assert len(data["task_timeline"]) == 3


def test_sync_warning_surfaces_in_final_report(conn, seed_run, tmp_path):
    """Pre-existing BEADS_SYNC_WARNING appears in sync_warnings list."""
    _insert_sync_start(conn, seed_run, total=2)
    _insert_event(conn, seed_run, "BEADS_SYNC_WARNING",
                  {"task_id": "T1", "stderr": "bd create failed"})
    _insert_task_completed(conn, seed_run, "T2")

    path = write_beads_final_artifact(seed_run, str(tmp_path), conn)

    data = json.loads(Path(path).read_text())
    assert len(data["sync_warnings"]) == 1
    assert data["sync_warnings"][0]["task_id"] == "T1"


def test_final_artifact_file_written(conn, seed_run, tmp_path):
    """write_beads_final_artifact writes file to <storage_root>/<run_id>/beads_final_report.json."""
    _insert_sync_start(conn, seed_run, total=1)
    _insert_task_completed(conn, seed_run, "T1")

    path = write_beads_final_artifact(seed_run, str(tmp_path), conn)

    expected = tmp_path / seed_run / "beads_final_report.json"
    assert expected.exists()
    assert str(expected) == path


def test_task_timeline_uses_task_id_from_payload(conn, seed_run, tmp_path):
    """task_timeline entries carry correct task_id from TASK_COMPLETED payload."""
    _insert_sync_start(conn, seed_run, total=2)
    _insert_task_completed(conn, seed_run, "TASK-BUILD")
    _insert_task_completed(conn, seed_run, "TASK-TEST")

    path = write_beads_final_artifact(seed_run, str(tmp_path), conn)

    data = json.loads(Path(path).read_text())
    task_ids = [t["task_id"] for t in data["task_timeline"]]
    assert "TASK-BUILD" in task_ids
    assert "TASK-TEST" in task_ids
```

- [ ] **Step 3: Run integration tests**

```bash
pytest tests/integration/test_beads_report.py -v
```

Expected: all tests PASS

- [ ] **Step 4: Run full test suite to confirm no regressions**

```bash
pytest tests/ -v --tb=short
```

Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_beads_report.py
git commit -m "test(beads): add integration tests for beads final report"
```
