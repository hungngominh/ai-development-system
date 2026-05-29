# src/ai_dev_system/engine/background.py
"""Background loop: dead-task recovery, ready-promotion, completion check.

PG → SQLite changes:
- `RETURNING` removed → SELECT then UPDATE
- `COUNT(*) FILTER (WHERE ...)` → `SUM(CASE WHEN ... THEN 1 ELSE 0 END)`
- `ANY(resolved_dependencies)` → JSON parse + Python check
- `now() - interval '1s' * N` → `datetime('now', '-N seconds')`
- `FOR UPDATE SKIP LOCKED` removed (SQLite single-writer)
"""
import json
import logging
import sqlite3
import threading
from typing import Optional

from ai_dev_system.config import Config
from ai_dev_system.db.repos.events import EventRepo
from ai_dev_system.db.repos.task_runs import TaskRunRepo
from ai_dev_system.engine.resolver import resolve_dependencies

logger = logging.getLogger(__name__)


def background_loop(
    run_id: str,
    config: Config,
    stop_event: threading.Event,
    conn_factory,
) -> None:
    """Background thread: recover → ready → completion, every poll_interval_s."""
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


def _deps_satisfied(conn: sqlite3.Connection, run_id: str, deps_json) -> bool:
    if deps_json is None:
        return True
    if isinstance(deps_json, str):
        deps = json.loads(deps_json) if deps_json.strip() else []
    elif isinstance(deps_json, list):
        deps = deps_json
    else:
        deps = list(deps_json)
    if not deps:
        return True
    placeholders = ",".join("?" for _ in deps)
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS c FROM task_runs
        WHERE run_id = ? AND task_id IN ({placeholders})
          AND status IN ('SUCCESS', 'SKIPPED')
        """,
        (run_id, *deps),
    ).fetchone()
    return row["c"] == len(deps)


def mark_ready_tasks(conn: sqlite3.Connection, run_id: str) -> int:
    """PENDING tasks whose deps are all SUCCESS/SKIPPED → READY.

    Respects retry_at: tasks with future retry_at stay PENDING.
    Returns count of tasks promoted.
    """
    event_repo = EventRepo(conn)
    candidates = conn.execute(
        """
        SELECT task_run_id, resolved_dependencies
        FROM task_runs
        WHERE run_id = ?
          AND status = 'PENDING'
          AND (retry_at IS NULL OR retry_at <= CURRENT_TIMESTAMP)
        """,
        (run_id,),
    ).fetchall()

    promoted = 0
    for row in candidates:
        if not _deps_satisfied(conn, run_id, row["resolved_dependencies"]):
            continue
        updated = conn.execute(
            "UPDATE task_runs SET status = 'READY' "
            "WHERE task_run_id = ? AND status = 'PENDING'",
            (row["task_run_id"],),
        ).rowcount
        if updated > 0:
            event_repo.insert(run_id, "TASK_READY", "system", task_run_id=row["task_run_id"])
            promoted += 1
    return promoted


def recover_dead_tasks(
    conn: sqlite3.Connection,
    run_id: str,
    config: Config,
) -> None:
    """Detect RUNNING tasks with stale heartbeat → create retry or mark FAILED_FINAL."""
    repo = TaskRunRepo(conn)
    timeout_s = int(config.heartbeat_timeout_s)

    stale = conn.execute(
        f"""
        SELECT task_run_id, task_id, attempt_number, retry_count, worker_id
        FROM task_runs
        WHERE run_id = ?
          AND status = 'RUNNING'
          AND worker_id IS NOT NULL
          AND heartbeat_at < datetime('now', '-{timeout_s} seconds')
        """,
        (run_id,),
    ).fetchall()

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
            from ai_dev_system.engine.failure import propagate_failure
            propagate_failure(
                conn, run_id,
                failed_task_id=task["task_id"],
                failed_task_run_id=task["task_run_id"],
            )
            logger.error(
                "Dead worker: task %s exhausted retries → FAILED_FINAL", task["task_id"]
            )


def check_completion(conn: sqlite3.Connection, run_id: str) -> None:
    """Detect run SUCCESS or PAUSED_FOR_DECISION."""
    event_repo = EventRepo(conn)

    row = conn.execute(
        """
        SELECT
            SUM(CASE WHEN status = 'SUCCESS'            THEN 1 ELSE 0 END) AS success_count,
            SUM(CASE WHEN status = 'FAILED_FINAL'       THEN 1 ELSE 0 END) AS failed_final_count,
            SUM(CASE WHEN status = 'BLOCKED_BY_FAILURE' THEN 1 ELSE 0 END) AS blocked_count,
            SUM(CASE WHEN status = 'READY'              THEN 1 ELSE 0 END) AS ready_count,
            SUM(CASE WHEN status = 'RUNNING'            THEN 1 ELSE 0 END) AS running_count,
            SUM(CASE WHEN status = 'PENDING'            THEN 1 ELSE 0 END) AS pending_count
        FROM task_runs
        WHERE run_id = ?
        """,
        (run_id,),
    ).fetchone()

    success_count      = row["success_count"]      or 0
    failed_final_count = row["failed_final_count"] or 0
    blocked_count      = row["blocked_count"]      or 0
    ready_count        = row["ready_count"]        or 0
    running_count      = row["running_count"]      or 0
    pending_count      = row["pending_count"]      or 0

    active_count = ready_count + running_count + pending_count

    if (active_count == 0
            and failed_final_count == 0
            and blocked_count == 0
            and success_count > 0):
        updated = conn.execute(
            """
            UPDATE runs SET status = 'COMPLETED', completed_at = CURRENT_TIMESTAMP
            WHERE run_id = ? AND status = 'RUNNING_EXECUTION'
            """,
            (run_id,),
        ).rowcount
        if updated > 0:
            event_repo.insert(run_id, "RUN_COMPLETED", "system",
                              payload={"outcome": "SUCCESS"})
            logger.info("Run %s completed successfully", run_id)

    elif (active_count == 0
          and failed_final_count > 0
          and running_count == 0
          and ready_count == 0):
        updated = conn.execute(
            """
            UPDATE runs SET status = 'PAUSED_FOR_DECISION'
            WHERE run_id = ? AND status = 'RUNNING_EXECUTION'
            """,
            (run_id,),
        ).rowcount
        if updated > 0:
            logger.warning("Run %s paused for human decision", run_id)

    elif (active_count == 0
          and blocked_count > 0
          and failed_final_count == 0):
        logger.error(
            "Run %s: inconsistent state — %d BLOCKED but 0 FAILED_FINAL. Forcing PAUSED.",
            run_id, blocked_count,
        )
        conn.execute(
            """
            UPDATE runs SET status = 'PAUSED_FOR_DECISION'
            WHERE run_id = ? AND status = 'RUNNING_EXECUTION'
            """,
            (run_id,),
        )
