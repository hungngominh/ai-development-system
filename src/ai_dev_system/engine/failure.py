# src/ai_dev_system/engine/failure.py
import logging

import psycopg

from ai_dev_system.config import Config
from ai_dev_system.db.repos.escalations import EscalationRepo
from ai_dev_system.db.repos.events import EventRepo
from ai_dev_system.db.repos.task_runs import TaskRunRepo

logger = logging.getLogger(__name__)


def propagate_failure(
    conn: psycopg.Connection,
    run_id: str,
    failed_task_id: str,
    failed_task_run_id: str,
) -> None:
    """BFS: mark all downstream tasks BLOCKED_BY_FAILURE.
    Skips terminal states (SUCCESS, SKIPPED, FAILED_*, ABORTED).
    Raises an escalation (deduplicated by UNIQUE constraint).
    Must be called inside an open transaction.
    """
    event_repo = EventRepo(conn)
    esc_repo = EscalationRepo(conn)

    visited: set[str] = set()
    queue = [failed_task_id]

    while queue:
        current_id = queue.pop(0)

        dependents = conn.execute("""
            SELECT task_run_id, task_id, status
            FROM task_runs
            WHERE run_id = %s
              AND %s = ANY(resolved_dependencies)
              AND status NOT IN (
                  'SUCCESS', 'SKIPPED', 'FAILED_FINAL',
                  'FAILED_RETRYABLE', 'ABORTED'
              )
        """, (run_id, current_id)).fetchall()

        for dep in dependents:
            if dep["task_id"] in visited:
                continue
            visited.add(dep["task_id"])

            conn.execute("""
                UPDATE task_runs
                SET status = 'BLOCKED_BY_FAILURE',
                    error_detail = %s
                WHERE task_run_id = %s
                  AND status IN ('PENDING', 'READY')
            """, (f"dependency_failed:{failed_task_id}", dep["task_run_id"]))

            queue.append(dep["task_id"])

    # Raise escalation — UNIQUE constraint deduplicates concurrent calls
    esc_repo.upsert_open(
        run_id=run_id,
        task_run_id=failed_task_run_id,
        reason="TASK_FAILURE",
        options=["retry", "skip", "abort"],
    )
    event_repo.insert(run_id, "ESCALATION_RAISED", "system",
                      task_run_id=failed_task_run_id,
                      payload={"failed_task_id": failed_task_id})


def _handle_failure(
    conn: psycopg.Connection,
    config: Config,
    task: dict,
    error: str,
    worker_id: str,
    run_id: str,
    error_type: str,
) -> None:
    """Mark task FAILED_RETRYABLE (with retry) or FAILED_FINAL (propagate).
    Called inside its own transaction (worker.py opens/commits).
    """
    repo = TaskRunRepo(conn)
    event_repo = EventRepo(conn)

    retry_cfg = config.retry_policy.get(error_type, config.retry_policy["UNKNOWN"])
    can_retry = task.get("retry_count", 0) < retry_cfg["max_retries"]

    if can_retry:
        repo.mark_failed_retryable(task["task_run_id"], error_type, error)
        repo.create_retry(
            run_id, task,
            retry_delay_s=retry_cfg.get("retry_delay_s", 0),
            reset_retry_count=False,
        )
        event_repo.insert(run_id, "TASK_RETRYING", f"worker:{worker_id}",
                          task_run_id=task["task_run_id"],
                          payload={"error_type": error_type, "error": error})
        logger.info("Task %s attempt %d failed (%s), retrying",
                    task["task_id"], task.get("attempt_number", 1), error_type)
    else:
        repo.mark_failed_final(task["task_run_id"], error_type, error)
        event_repo.insert(run_id, "TASK_FAILED", f"worker:{worker_id}",
                          task_run_id=task["task_run_id"],
                          payload={"error_type": error_type, "error": error})
        propagate_failure(conn, run_id,
                          failed_task_id=task["task_id"],
                          failed_task_run_id=task["task_run_id"])
        logger.warning("Task %s exhausted retries → FAILED_FINAL", task["task_id"])
