# src/ai_dev_system/engine/background.py
import logging
import threading
from typing import Optional

import psycopg

from ai_dev_system.config import Config
from ai_dev_system.db.repos.events import EventRepo
from ai_dev_system.db.repos.task_runs import TaskRunRepo

logger = logging.getLogger(__name__)


def background_loop(
    run_id: str,
    config: Config,
    stop_event: threading.Event,
    conn_factory,
) -> None:
    """Background thread: recover → ready → completion, every poll_interval_s.
    Order matters: recover first (clean state), then resolve deps, then check completion.
    """
    conn = conn_factory()
    try:
        while not stop_event.is_set():
            try:
                conn.execute("BEGIN")
                recover_dead_tasks(conn, run_id, config)
                mark_ready_tasks(conn, run_id)
                check_completion(conn, run_id)
                conn.execute("COMMIT")
            except Exception:
                logger.exception("Background loop error for run %s", run_id)
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
            stop_event.wait(timeout=config.poll_interval_s)
    finally:
        conn.close()


def mark_ready_tasks(conn: psycopg.Connection, run_id: str) -> int:
    """PENDING tasks whose deps are all SUCCESS/SKIPPED → READY.
    Respects retry_at: tasks with future retry_at stay PENDING.
    Returns count of tasks promoted.
    """
    event_repo = EventRepo(conn)
    rows = conn.execute("""
        UPDATE task_runs t
        SET status = 'READY'
        WHERE t.run_id = %s
          AND t.status = 'PENDING'
          AND (t.retry_at IS NULL OR t.retry_at <= now())
          AND NOT EXISTS (
              SELECT 1 FROM task_runs dep
              WHERE dep.run_id = t.run_id
                AND dep.task_id = ANY(t.resolved_dependencies)
                AND dep.status NOT IN ('SUCCESS', 'SKIPPED')
          )
        RETURNING task_run_id
    """, (run_id,)).fetchall()

    for row in rows:
        event_repo.insert(run_id, "TASK_READY", "system", task_run_id=row["task_run_id"])

    return len(rows)


def recover_dead_tasks(
    conn: psycopg.Connection,
    run_id: str,
    config: Config,
) -> None:
    """Detect RUNNING tasks with stale heartbeat → create retry or mark FAILED_FINAL."""
    repo = TaskRunRepo(conn)

    stale = conn.execute("""
        SELECT task_run_id, task_id, attempt_number, retry_count, worker_id
        FROM task_runs
        WHERE run_id = %s
          AND status = 'RUNNING'
          AND worker_id IS NOT NULL
          AND heartbeat_at < now() - interval '1 second' * %s
        FOR UPDATE SKIP LOCKED
    """, (run_id, config.heartbeat_timeout_s)).fetchall()

    for task in stale:
        task = dict(task)
        max_env = config.retry_policy["ENVIRONMENT_ERROR"]["max_retries"]
        delay = config.retry_policy["ENVIRONMENT_ERROR"]["retry_delay_s"]

        if task["retry_count"] < max_env:
            repo.mark_failed_retryable(
                task["task_run_id"], "ENVIRONMENT_ERROR", "worker_heartbeat_timeout"
            )
            repo.create_retry(run_id, task, retry_delay_s=delay, reset_retry_count=False)
            logger.warning("Dead worker detected: task %s rescheduled", task["task_id"])
        else:
            repo.mark_failed_final(
                task["task_run_id"], "ENVIRONMENT_ERROR", "worker_heartbeat_timeout"
            )
            # Local import to avoid circular dependency (failure.py imports background.py indirectly)
            from ai_dev_system.engine.failure import propagate_failure
            propagate_failure(conn, run_id,
                              failed_task_id=task["task_id"],
                              failed_task_run_id=task["task_run_id"])
            logger.error("Dead worker: task %s exhausted retries → FAILED_FINAL", task["task_id"])


def check_completion(conn: psycopg.Connection, run_id: str) -> None:
    """Detect run SUCCESS or PAUSED_FOR_DECISION."""
    event_repo = EventRepo(conn)

    row = conn.execute("""
        SELECT
            COUNT(*) FILTER (WHERE status = 'SUCCESS')            AS success_count,
            COUNT(*) FILTER (WHERE status = 'FAILED_FINAL')       AS failed_final_count,
            COUNT(*) FILTER (WHERE status = 'BLOCKED_BY_FAILURE') AS blocked_count,
            COUNT(*) FILTER (WHERE status = 'READY')              AS ready_count,
            COUNT(*) FILTER (WHERE status = 'RUNNING')            AS running_count,
            COUNT(*) FILTER (WHERE status = 'PENDING')            AS pending_count
        FROM task_runs
        WHERE run_id = %s
    """, (run_id,)).fetchone()

    active_count = row["ready_count"] + row["running_count"] + row["pending_count"]

    if (active_count == 0
            and row["failed_final_count"] == 0
            and row["blocked_count"] == 0
            and row["success_count"] > 0):
        updated = conn.execute("""
            UPDATE runs SET status = 'COMPLETED', completed_at = now()
            WHERE run_id = %s AND status = 'RUNNING_EXECUTION'
        """, (run_id,)).rowcount
        if updated > 0:
            event_repo.insert(run_id, "RUN_COMPLETED", "system",
                              payload={"outcome": "SUCCESS"})
            logger.info("Run %s completed successfully", run_id)

    elif (active_count == 0
          and row["failed_final_count"] > 0
          and row["running_count"] == 0
          and row["ready_count"] == 0):
        updated = conn.execute("""
            UPDATE runs SET status = 'PAUSED_FOR_DECISION'
            WHERE run_id = %s AND status = 'RUNNING_EXECUTION'
        """, (run_id,)).rowcount
        if updated > 0:
            logger.warning("Run %s paused for human decision", run_id)

    elif (active_count == 0
          and row["blocked_count"] > 0
          and row["failed_final_count"] == 0):
        logger.error(
            "Run %s: inconsistent state — %d BLOCKED but 0 FAILED_FINAL. Forcing PAUSED.",
            run_id, row["blocked_count"]
        )
        conn.execute("""
            UPDATE runs SET status = 'PAUSED_FOR_DECISION'
            WHERE run_id = %s AND status = 'RUNNING_EXECUTION'
        """, (run_id,))
