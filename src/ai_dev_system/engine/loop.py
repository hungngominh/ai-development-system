# src/ai_dev_system/engine/loop.py
import logging
import time
from typing import Optional

import psycopg

from ai_dev_system.config import Config
from ai_dev_system.engine.worker import pickup_task, execute_and_promote

logger = logging.getLogger(__name__)

def run_worker_loop(
    config: Config,
    run_id: str,
    worker_id: str,
    agent,
    idle_backoff_s: float = 1.0,
    max_iterations: Optional[int] = None,
) -> None:
    """
    Main worker loop.

    Transaction boundary (two-transaction model):
      Tx 1: pickup — lock task_run row, set RUNNING (short)
      Tx 2: promote — version lock + artifact insert + task_run SUCCESS (after agent finishes)

    This ensures the FOR UPDATE SKIP LOCKED row is released before agent execution,
    so other workers can attempt other tasks while this one runs.
    """
    iterations = 0
    conn_factory = lambda: psycopg.connect(
        config.database_url, autocommit=False, row_factory=psycopg.rows.dict_row
    )

    while True:
        if max_iterations is not None and iterations >= max_iterations:
            break
        iterations += 1

        # Tx 1: pickup
        task = None
        with conn_factory() as conn:
            try:
                conn.execute("BEGIN")
                task = pickup_task(conn, config, run_id, worker_id)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                logger.exception("Pickup error")
                time.sleep(idle_backoff_s * 2)
                continue

        if task is None:
            logger.debug("No tasks available, backing off %ss", idle_backoff_s)
            time.sleep(idle_backoff_s)
            continue

        # Agent execution (outside any transaction)
        result = agent.run(
            task_id=task["task_id"],
            output_path=task["temp_path"],
            promoted_outputs=task["promoted_outputs_parsed"],
        )

        # Tx 2: promote
        with conn_factory() as conn:
            try:
                conn.execute("BEGIN")
                status = execute_and_promote(conn, config, task, result, worker_id)
                conn.execute("COMMIT")
                logger.info("Task %s → %s", task["task_id"], status)
            except Exception:
                conn.execute("ROLLBACK")
                logger.exception("Promotion error for task %s", task["task_id"])
                time.sleep(idle_backoff_s * 2)
