# src/ai_dev_system/engine/loop.py
"""Legacy single-worker loop. Kept for Phase-1 tests; runner.py is the new path.

PG → SQLite: psycopg.connect lambda → get_connection().
"""
import logging
import time
from typing import Optional

from ai_dev_system.config import Config
from ai_dev_system.db.connection import get_connection
from ai_dev_system.db.repos.task_runs import TaskRunRepo
from ai_dev_system.engine.worker import pickup_task, execute_and_promote

logger = logging.getLogger(__name__)


def _recover_failed(conn_factory, task_run_id: str, error_type: str, error_detail: str) -> None:
    """Best-effort recovery: mark task FAILED in a new connection."""
    try:
        conn = conn_factory()
        try:
            conn.execute("BEGIN")
            TaskRunRepo(conn).mark_failed(task_run_id, error_type, error_detail)
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()
    except Exception:
        logger.exception("Recovery transaction failed for task_run %s", task_run_id)


def run_worker_loop(
    config: Config,
    run_id: str,
    worker_id: str,
    agent,
    idle_backoff_s: float = 1.0,
    max_iterations: Optional[int] = None,
) -> None:
    """Main worker loop (Phase 1 legacy path).

    Two-transaction model:
      Tx 1: pickup — mark RUNNING (short)
      Tx 2: promote — version lock + artifact insert + task_run SUCCESS

    Use max_iterations to bound execution in tests/scripts.
    """
    iterations = 0
    conn_factory = lambda: get_connection(config.database_url)

    while True:
        if max_iterations is not None and iterations >= max_iterations:
            break
        iterations += 1

        # Tx 1: pickup
        task = None
        conn = conn_factory()
        try:
            try:
                conn.execute("BEGIN")
                task = pickup_task(conn, config, run_id, worker_id)
                conn.execute("COMMIT")
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                logger.exception("Pickup error")
                time.sleep(idle_backoff_s * 2)
                continue
        finally:
            conn.close()

        if task is None:
            logger.debug("No tasks available, backing off %ss", idle_backoff_s)
            time.sleep(idle_backoff_s)
            continue

        # Agent execution (outside any transaction)
        try:
            result = agent.run(
                task_id=task["task_id"],
                output_path=task["temp_path"],
                promoted_outputs=task["promoted_outputs_parsed"],
            )
        except Exception:
            logger.exception("Agent error for task %s", task["task_id"])
            _recover_failed(conn_factory, task["task_run_id"], "EXECUTION_ERROR", "agent_exception")
            time.sleep(idle_backoff_s * 2)
            continue

        # Tx 2: promote
        conn = conn_factory()
        try:
            try:
                conn.execute("BEGIN")
                status = execute_and_promote(conn, config, task, result, worker_id)
                conn.execute("COMMIT")
                logger.info("Task %s → %s", task["task_id"], status)
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                logger.exception("Promotion error for task %s", task["task_id"])
                _recover_failed(conn_factory, task["task_run_id"], "EXECUTION_ERROR", "promotion_failed")
                time.sleep(idle_backoff_s * 2)
                continue
        finally:
            conn.close()
