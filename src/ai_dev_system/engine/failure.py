# src/ai_dev_system/engine/failure.py
"""Failure propagation (SQLite).

PG `? = ANY(resolved_dependencies)` → Python JSON parse + membership check.
"""
import json
import logging
import sqlite3

from ai_dev_system.config import Config
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


def propagate_failure(
    conn: sqlite3.Connection,
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

        # All non-terminal task_runs in this run; filter by dep membership in Python
        candidates = conn.execute(
            """
            SELECT task_run_id, task_id, status, resolved_dependencies
            FROM task_runs
            WHERE run_id = ?
              AND status NOT IN (
                  'SUCCESS', 'SKIPPED', 'FAILED_FINAL',
                  'FAILED_RETRYABLE', 'ABORTED'
              )
            """,
            (run_id,),
        ).fetchall()

        for dep in candidates:
            deps_list = _parse_deps(dep["resolved_dependencies"])
            if current_id not in deps_list:
                continue
            if dep["task_id"] in visited:
                continue
            visited.add(dep["task_id"])

            conn.execute(
                """
                UPDATE task_runs
                SET status = 'BLOCKED_BY_FAILURE',
                    error_detail = ?
                WHERE task_run_id = ?
                  AND status IN ('PENDING', 'READY')
                """,
                (f"dependency_failed:{failed_task_id}", dep["task_run_id"]),
            )

            queue.append(dep["task_id"])

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
    conn: sqlite3.Connection,
    config: Config,
    task: dict,
    error: str,
    worker_id: str,
    run_id: str,
    error_type: str,
) -> None:
    """Mark task FAILED_RETRYABLE (with retry) or FAILED_FINAL (propagate)."""
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
