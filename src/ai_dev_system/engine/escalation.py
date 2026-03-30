# src/ai_dev_system/engine/escalation.py
import logging

import psycopg

from ai_dev_system.db.repos.escalations import EscalationRepo
from ai_dev_system.db.repos.events import EventRepo
from ai_dev_system.db.repos.task_runs import TaskRunRepo

logger = logging.getLogger(__name__)


def resolve_escalation(
    conn: psycopg.Connection,
    escalation_id: str,
    resolution: str,   # 'retry' | 'skip' | 'abort'
    run_id: str,
) -> None:
    """Human resolves a stuck run. Idempotent — second call on resolved esc is a no-op.

    IMPORTANT: This function manages its own transaction internally using SAVEPOINT
    so it can be called whether or not the caller has an active transaction.
    The conn fixture in tests uses autocommit=False with an open transaction per test.
    We use SAVEPOINT to nest within the test's transaction.
    """
    esc_repo = EscalationRepo(conn)
    repo = TaskRunRepo(conn)
    event_repo = EventRepo(conn)

    # Use SAVEPOINT for nested transaction support (works within test fixtures too)
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
                WHERE run_id = %s AND status = 'PAUSED_FOR_DECISION'
            """, (run_id,))

        elif resolution == "skip":
            conn.execute("""
                UPDATE task_runs SET status = 'SKIPPED'
                WHERE task_run_id = %s AND status = 'FAILED_FINAL'
            """, (str(esc["task_run_id"]),))
            _unblock_downstream_bfs(conn, run_id, task["task_id"])
            conn.execute("""
                UPDATE runs SET status = 'RUNNING_EXECUTION'
                WHERE run_id = %s AND status = 'PAUSED_FOR_DECISION'
            """, (run_id,))

        elif resolution == "abort":
            conn.execute("""
                UPDATE task_runs SET status = 'ABORTED'
                WHERE run_id = %s
                  AND status NOT IN ('SUCCESS', 'FAILED_FINAL', 'SKIPPED', 'ABORTED')
            """, (run_id,))
            conn.execute("""
                UPDATE runs SET status = 'FAILED', completed_at = now()
                WHERE run_id = %s
            """, (run_id,))
            event_repo.insert(run_id, "RUN_ABORTED", "human",
                              payload={"reason": "human_abort_on_escalation"})

        conn.execute("RELEASE SAVEPOINT resolve_escalation")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT resolve_escalation")
        conn.execute("RELEASE SAVEPOINT resolve_escalation")
        raise


def _unblock_downstream_bfs(
    conn: psycopg.Connection,
    run_id: str,
    unblocked_task_id: str,
) -> None:
    """BFS: BLOCKED_BY_FAILURE → PENDING for tasks downstream of unblocked_task_id.
    Only unblocks if the task has NO OTHER FAILED_FINAL dependencies.
    mark_ready_tasks() then evaluates which PENDING tasks are actually READY.
    """
    visited: set[str] = set()
    queue = [unblocked_task_id]

    while queue:
        current = queue.pop(0)
        blocked = conn.execute("""
            SELECT task_run_id, task_id
            FROM task_runs
            WHERE run_id = %s
              AND %s = ANY(resolved_dependencies)
              AND status = 'BLOCKED_BY_FAILURE'
        """, (run_id, current)).fetchall()

        for dep in blocked:
            if dep["task_id"] in visited:
                continue
            visited.add(dep["task_id"])

            # Only unblock if this task has no remaining FAILED_FINAL deps
            rows_updated = conn.execute("""
                UPDATE task_runs SET status = 'PENDING', error_detail = NULL
                WHERE task_run_id = %s
                  AND status = 'BLOCKED_BY_FAILURE'
                  AND NOT EXISTS (
                      SELECT 1 FROM task_runs other_dep
                      WHERE other_dep.run_id = %s
                        AND other_dep.task_id = ANY(
                            (SELECT resolved_dependencies FROM task_runs
                             WHERE task_run_id = %s)::text[]
                        )
                        AND other_dep.status = 'FAILED_FINAL'
                  )
            """, (dep["task_run_id"], run_id, dep["task_run_id"])).rowcount

            if rows_updated > 0:
                queue.append(dep["task_id"])
