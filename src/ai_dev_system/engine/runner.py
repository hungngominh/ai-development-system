# src/ai_dev_system/engine/runner.py
"""Execution runner (SQLite).

psycopg → sqlite3: conn_factory now returns sqlite3.Connection via get_connection.
SQLite is single-writer; we still pass a factory so worker_loop and background_loop
each get their own connection (sqlite3 connections are not thread-safe to share).
"""
import dataclasses
import logging
import threading
import time
from dataclasses import dataclass

from ai_dev_system.config import Config
from ai_dev_system.db.connection import get_connection
from ai_dev_system.engine.background import background_loop
from ai_dev_system.engine.materializer import materialize_task_runs
from ai_dev_system.engine.worker import worker_loop

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    run_id: str
    status: str   # COMPLETED | FAILED | ABORTED


def run_execution(
    run_id: str,
    graph_artifact_id: str,
    config: Config,
    agent,
    poll_interval_s: float = 5.0,
) -> ExecutionResult:
    """Full lifecycle: materialize → spawn threads → wait for terminal state."""
    effective_config = config
    if poll_interval_s != config.poll_interval_s:
        effective_config = dataclasses.replace(config, poll_interval_s=poll_interval_s)

    def conn_factory():
        """Open a fresh SQLite connection. Each thread gets its own."""
        return get_connection(effective_config.database_url)

    # Step 1: Materialize (idempotent, safe to run multiple times)
    conn = conn_factory()
    try:
        conn.execute("BEGIN")
        try:
            materialize_task_runs(conn, run_id, graph_artifact_id, effective_config)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()

    stop_event = threading.Event()

    worker_thread = threading.Thread(
        target=worker_loop,
        args=(run_id, effective_config, agent, stop_event, conn_factory),
        name=f"worker-{run_id[:8]}",
        daemon=True,
    )
    background_thread = threading.Thread(
        target=background_loop,
        args=(run_id, effective_config, stop_event, conn_factory),
        name=f"bg-{run_id[:8]}",
        daemon=True,
    )

    worker_thread.start()
    background_thread.start()
    logger.info("Execution runner started for run %s", run_id)

    final_status = _wait_for_terminal_state(run_id, effective_config, conn_factory)

    stop_event.set()
    worker_thread.join(timeout=30)
    background_thread.join(timeout=10)

    if worker_thread.is_alive():
        logger.warning("Worker thread did not stop cleanly for run %s", run_id)

    logger.info("Run %s finished with status %s", run_id, final_status)
    return ExecutionResult(run_id=run_id, status=final_status)


def _wait_for_terminal_state(
    run_id: str,
    config: Config,
    conn_factory,
) -> str:
    """Poll runs.status until terminal. Returns the terminal status string."""
    # PAUSED_FOR_DECISION is intentionally excluded: threads keep running so
    # a human resolver (external or in a test) can unblock the run and let it
    # proceed to COMPLETED without restarting.
    terminal = {"COMPLETED", "FAILED", "ABORTED"}
    conn = conn_factory()
    try:
        while True:
            row = conn.execute(
                "SELECT status FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row and row["status"] in terminal:
                return row["status"]
            time.sleep(config.poll_interval_s / 2)
    finally:
        conn.close()
