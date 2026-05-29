# src/ai_dev_system/engine/escalation.py
"""Escalation resolution (SQLite).

PG → SQLite:
- `ANY(resolved_dependencies)` → Python iteration over parsed JSON.
- SAVEPOINT semantics unchanged (SQLite supports nested savepoints).
"""
import json
import logging
import sqlite3

from ai_dev_system.db.repos.escalations import EscalationRepo
from ai_dev_system.db.repos.events import EventRepo
from ai_dev_system.db.repos.task_runs import TaskRunRepo

logger = logging.getLogger(__name__)


def _parse_deps(raw) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        return json.loads(raw) if raw.strip() else []
    return list(raw)


def resolve_escalation(
    conn: sqlite3.Connection,
    escalation_id: str,
    resolution: str,   # 'retry' | 'skip' | 'abort'
    run_id: str,
) -> None:
    """Human resolves a stuck run. Idempotent — second call on resolved esc is a no-op.

    Uses SAVEPOINT so callers may or may not already have an open transaction.
    """
    esc_repo = EscalationRepo(conn)
    repo = TaskRunRepo(conn)
    event_repo = EventRepo(conn)

    conn.execute("SAVEPOINT resolve_escalation")
    try:
        esc = esc_repo.get_and_lock(escalation_id)
        if esc is None or esc["status"] != "OPEN":
            conn.execute("RELEASE SAVEPOINT resolve_escalation")
            return  # Already resolved — idempotent

        esc_repo.mark_resolved(escalation_id, resolution)
        event_repo.insert(run_id, "HUMAN_DECISION_RECORDED", "human",
                          task_run_id=esc["task_run_id"],
                          payload={"resolution": resolution,
                                   "escalation_id": str(escalation_id)})

        task = repo.get_by_id(esc["task_run_id"])

        if resolution == "retry":
            repo.create_retry(run_id, task, retry_delay_s=0, reset_retry_count=True)
            _unblock_downstream_bfs(conn, run_id, task["task_id"])
            conn.execute("""
                UPDATE runs SET status = 'RUNNING_EXECUTION'
                WHERE run_id = ? AND status = 'PAUSED_FOR_DECISION'
            """, (run_id,))

        elif resolution == "skip":
            conn.execute("""
                UPDATE task_runs SET status = 'SKIPPED'
                WHERE task_run_id = ? AND status = 'FAILED_FINAL'
            """, (str(esc["task_run_id"]),))
            _unblock_downstream_bfs(conn, run_id, task["task_id"])
            conn.execute("""
                UPDATE runs SET status = 'RUNNING_EXECUTION'
                WHERE run_id = ? AND status = 'PAUSED_FOR_DECISION'
            """, (run_id,))

        elif resolution == "abort":
            conn.execute("""
                UPDATE task_runs SET status = 'ABORTED'
                WHERE run_id = ?
                  AND status NOT IN ('SUCCESS', 'FAILED_FINAL', 'SKIPPED', 'ABORTED')
            """, (run_id,))
            conn.execute("""
                UPDATE runs SET status = 'FAILED', completed_at = CURRENT_TIMESTAMP
                WHERE run_id = ?
            """, (run_id,))
            event_repo.insert(run_id, "RUN_ABORTED", "human",
                              payload={"reason": "human_abort_on_escalation"})

        conn.execute("RELEASE SAVEPOINT resolve_escalation")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT resolve_escalation")
        conn.execute("RELEASE SAVEPOINT resolve_escalation")
        raise


def _unblock_downstream_bfs(
    conn: sqlite3.Connection,
    run_id: str,
    unblocked_task_id: str,
) -> None:
    """BFS: BLOCKED_BY_FAILURE → PENDING for tasks downstream of unblocked_task_id.

    Only unblocks if the task has NO remaining FAILED_FINAL dependencies.
    mark_ready_tasks() then evaluates which PENDING tasks are actually READY.
    """
    visited: set[str] = set()
    queue = [unblocked_task_id]

    while queue:
        current = queue.pop(0)

        # All blocked task_runs in this run — filter by "current ∈ resolved_deps" in Python.
        blocked = conn.execute(
            """
            SELECT task_run_id, task_id, resolved_dependencies
            FROM task_runs
            WHERE run_id = ? AND status = 'BLOCKED_BY_FAILURE'
            """,
            (run_id,),
        ).fetchall()

        for dep in blocked:
            deps_list = _parse_deps(dep["resolved_dependencies"])
            if current not in deps_list:
                continue
            if dep["task_id"] in visited:
                continue
            visited.add(dep["task_id"])

            # Check this task has no remaining FAILED_FINAL deps before unblocking
            if not deps_list:
                has_failed_dep = False
            else:
                placeholders = ",".join("?" for _ in deps_list)
                row = conn.execute(
                    f"""
                    SELECT 1 FROM task_runs
                    WHERE run_id = ?
                      AND task_id IN ({placeholders})
                      AND status = 'FAILED_FINAL'
                    LIMIT 1
                    """,
                    (run_id, *deps_list),
                ).fetchone()
                has_failed_dep = row is not None

            if has_failed_dep:
                continue

            rows_updated = conn.execute(
                """
                UPDATE task_runs SET status = 'PENDING', error_detail = NULL
                WHERE task_run_id = ? AND status = 'BLOCKED_BY_FAILURE'
                """,
                (dep["task_run_id"],),
            ).rowcount

            if rows_updated > 0:
                queue.append(dep["task_id"])
