# Design Spec: Beads Final Report (Phase 5)

**Date:** 2026-03-31
**Status:** Approved
**Scope:** Phase 5 of workflow-v2 — `BD->>BD: Cap nhat status + audit trail` + `BD->>U: Audit trail + thong ke`

---

## Overview

Implements the final phase of workflow-v2 where Beads provides real-time progress updates during execution and a final audit trail report to the user. Completes the pipeline loop:

```
beads_sync() → run_execution() → [per-task: BD->>BD] → [end: BD->>U]
```

**Core decision:** Self-tracking via events table (DB-only). No dependency on `bd status` JSON format — progress is computed from `TASK_COMPLETED` events written to the existing `events` table after each task.

---

## Architecture

### Position in pipeline (per workflow-v2 + data-flow-v2)

```
Phase B: ... → beads_sync() → run_execution()
                                     │
                          ┌──────────▼─────────────┐
                          │  per-task (BD->>BD):    │
                          │  beads_update_task()    │
                          │  ├─ bd close <task_id>  │
                          │  ├─ insert TASK_COMPLETED│
                          │  └─ print_beads_progress │
                          └──────────┬─────────────┘
                                     │ (after all tasks)
                          ┌──────────▼─────────────┐
                          │  final (BD->>U):        │
                          │  write_beads_final_     │
                          │  artifact()             │
                          │  + terminal summary     │
                          └─────────────────────────┘
```

### New files

| File | Responsibility |
|------|----------------|
| `src/ai_dev_system/beads/report.py` | `beads_update_task()`, `print_beads_progress()`, `write_beads_final_artifact()`, `BeadsFinalReport` dataclass |
| `docs/schema/migrations/v4-beads-report.sql` | Add `TASK_COMPLETED`, `BEADS_SYNC_START` event types |
| `tests/unit/beads/test_report.py` | Unit tests (subprocess mocked, DB stub) |
| `tests/integration/test_beads_report.py` | Integration tests (real DB, subprocess mocked) |

### Modified files

| File | Change |
|------|--------|
| `src/ai_dev_system/beads/sync.py` | Insert `BEADS_SYNC_START` event at start of `beads_sync()` |
| `src/ai_dev_system/engine/worker.py` | Call `beads_update_task()` after each task succeeds; call `write_beads_final_artifact()` after all tasks |

---

## Schema Migration (v4)

```sql
-- v4-beads-report.sql
ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'TASK_COMPLETED';
ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'BEADS_SYNC_START';
```

---

## Component Details

### Data Contract

```python
@dataclass
class BeadsFinalReport:
    run_id: str
    total_tasks: int
    completed_tasks: int
    sync_warnings: list[dict]   # [{"task_id": str, "stderr": str}]
    task_timeline: list[dict]   # [{"task_id": str, "completed_at": str}]
    generated_at: str           # ISO UTC
```

---

### beads_update_task(run_id, task_id, conn) — per-task (BD->>BD)

Called by `worker.py` after each task completes successfully.

```python
def beads_update_task(run_id: str, task_id: str, conn) -> None:
    # 1. Mark task closed in Beads (non-blocking, warn-only on failure)
    try:
        result = subprocess.run(
            ["bd", "close", task_id],
            capture_output=True
        )
        if result.returncode != 0:
            logger.warning("beads: close failed for %s: %s", task_id, result.stderr)
    except FileNotFoundError:
        logger.warning("beads: bd not in PATH, skipping close for %s", task_id)

    # 2. Record completion in events table
    EventRepo(conn).insert(run_id, "TASK_COMPLETED", "system",
                           payload={"task_id": task_id})

    # 3. Print real-time progress to terminal
    print_beads_progress(run_id, conn)
```

**Error handling:**
- `bd close` non-zero exit → log warning, continue (non-blocking)
- `bd` not in PATH (`FileNotFoundError`) → log warning, continue
- Never raises — execution must not fail due to reporting

---

### print_beads_progress(run_id, conn) — terminal output

Computes progress from events table only (no subprocess call):

```python
def print_beads_progress(run_id: str, conn) -> None:
    rows = conn.execute("""
        SELECT event_type, payload, created_at
        FROM events
        WHERE run_id = %s
          AND event_type IN ('TASK_COMPLETED', 'BEADS_SYNC_WARNING')
        ORDER BY created_at
    """, (run_id,)).fetchall()

    completed = [r for r in rows if r["event_type"] == "TASK_COMPLETED"]
    warnings  = [r for r in rows if r["event_type"] == "BEADS_SYNC_WARNING"]
    total = _get_total_tasks(run_id, conn)

    warning_str = f"  ⚠️  {len(warnings)} sync warning(s)" if warnings else ""
    print(
        f"[{len(completed)}/{total} tasks done]"
        f"  open={total - len(completed)}"
        f"  closed={len(completed)}"
        f"{warning_str}"
    )
```

**Example terminal output:**
```
[1/6 tasks done]  open=5  closed=1
[2/6 tasks done]  open=4  closed=2
[3/6 tasks done]  open=3  closed=3  ⚠️  1 sync warning(s)
[4/6 tasks done]  open=2  closed=4  ⚠️  1 sync warning(s)
[5/6 tasks done]  open=1  closed=5  ⚠️  1 sync warning(s)
[6/6 tasks done]  open=0  closed=6  ⚠️  1 sync warning(s)
```

---

### _get_total_tasks(run_id, conn) — internal helper

Reads `total_tasks` from the `BEADS_SYNC_START` event payload:

```python
def _get_total_tasks(run_id: str, conn) -> int:
    row = conn.execute("""
        SELECT payload FROM events
        WHERE run_id = %s AND event_type = 'BEADS_SYNC_START'
        ORDER BY created_at LIMIT 1
    """, (run_id,)).fetchone()
    return row["payload"]["total_tasks"] if row else 0
```

---

### Modification to beads_sync() — BEADS_SYNC_START event

Add at start of `beads_sync()` so `total_tasks` is available for progress tracking:

```python
def beads_sync(run_id: str, graph: dict, conn) -> None:
    # NEW: record total task count for progress tracking
    EventRepo(conn).insert(run_id, "BEADS_SYNC_START", "system",
                           payload={"total_tasks": len(graph["tasks"])})

    # existing logic unchanged below...
    event_repo = EventRepo(conn)
    tasks = topological_sort(graph["tasks"])
    ...
```

---

### write_beads_final_artifact(run_id, conn) — final report (BD->>U)

Called once after all tasks complete. Writes `BEADS_FINAL_REPORT` artifact + prints terminal summary.

```python
def write_beads_final_artifact(run_id: str, storage_root: str, conn) -> str:
    rows = conn.execute("""
        SELECT event_type, payload, created_at
        FROM events
        WHERE run_id = %s
          AND event_type IN ('TASK_COMPLETED', 'BEADS_SYNC_WARNING')
        ORDER BY created_at
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
             "completed_at": r["created_at"].isoformat()}
            for r in completed_rows
        ],
        generated_at=datetime.utcnow().isoformat() + "Z",
    )

    # Write JSON artifact
    artifact_path = Path(storage_root) / run_id / "beads_final_report.json"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(json.dumps(asdict(report), indent=2))

    # Print terminal summary (BD->>U)
    _print_final_summary(report)

    return str(artifact_path)


def _print_final_summary(report: BeadsFinalReport) -> None:
    warning_line = (
        f"  Warnings  : {len(report.sync_warnings)} sync warning(s)\n"
        if report.sync_warnings else
        f"  Warnings  : none\n"
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

**Example terminal output (BD->>U):**
```
══════════════════════════════════════
  Beads Audit Trail + Thống kê
══════════════════════════════════════
  Completed : 6/6 tasks
  Warnings  : none
  Timeline  :
    TASK-1       done  10:01
    TASK-2       done  10:15
    TASK-3       done  10:34
    TASK-4       done  10:52
    TASK-5       done  11:08
    TASK-6       done  11:21
══════════════════════════════════════
```

---

## New Artifact Type

Added to `artifact_type` enum (requires v4 migration for artifact type if not already covered):

| Type | Key in current_artifacts | Phase |
|------|--------------------------|-------|
| `BEADS_FINAL_REPORT` | `beads_final_report_id` | Phase 5 |

---

## Testing Strategy

### Unit tests — `tests/unit/beads/test_report.py`

| Test | Verifies |
|------|----------|
| `test_print_progress_no_warnings` | `[2/5 tasks done]  open=3  closed=2` — correct format, no warning suffix |
| `test_print_progress_with_warnings` | Line ends with `⚠️  1 sync warning(s)` |
| `test_print_progress_zero_completed` | `[0/5 tasks done]` — handles empty state |
| `test_write_final_report_structure` | `BeadsFinalReport` has correct fields, JSON written to correct path |
| `test_beads_update_task_logs_warning_on_close_fail` | `bd close` returncode=1 → warning logged, no exception |
| `test_beads_update_task_skips_bd_not_found` | `FileNotFoundError` → warning logged, no exception |

### Integration tests — `tests/integration/test_beads_report.py`

| Test | Verifies |
|------|----------|
| `test_full_flow_3_tasks` | 3× `beads_update_task()` → 3 `TASK_COMPLETED` events → final report has `completed_tasks=3` |
| `test_sync_warning_surfaces_in_final_report` | Pre-insert `BEADS_SYNC_WARNING` → appears in `sync_warnings` list |
| `test_final_artifact_written_to_db` | `write_beads_final_artifact()` → file exists at expected path |
| `test_get_total_tasks_from_sync_start_event` | `_get_total_tasks()` reads correctly from `BEADS_SYNC_START` payload |

### Stub pattern (consistent with test_beads_sync.py)

```python
with patch("subprocess.run") as mock_run:
    mock_run.return_value = MagicMock(returncode=0, stderr=b"")
    beads_update_task("r1", "TASK-1", db_conn)
```

---

## Error Handling Summary

| Failure | Behavior |
|---------|----------|
| `bd close` non-zero exit | Log warning, continue execution |
| `bd` not in PATH | Log warning, continue execution |
| `BEADS_SYNC_START` event missing | `_get_total_tasks()` returns 0, progress shows `[N/0]` |
| Artifact write fails | Propagate exception (storage failure = real error) |

---

## Out of Scope

- ETA estimation
- Real-time streaming to UI
- Beads error recovery (best-effort sync only, per existing spec)
- `bd audit record` integration (separate concern)
