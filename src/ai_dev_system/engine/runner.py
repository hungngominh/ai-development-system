# src/ai_dev_system/engine/runner.py
import dataclasses
import logging
import threading
import time
from dataclasses import dataclass

import psycopg
import psycopg.rows

from ai_dev_system.config import Config
from ai_dev_system.engine.background import background_loop
from ai_dev_system.engine.materializer import materialize_task_runs
from ai_dev_system.engine.worker import worker_loop

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    run_id: str
    status: str   # COMPLETED | PAUSED_FOR_DECISION | FAILED | ABORTED


def run_execution(
    run_id: str,
    graph_artifact_id: str,
    config: Config,
    agent,
    poll_interval_s: float = 5.0,
) -> ExecutionResult:
    """Full lifecycle: materialize → spawn threads → wait for terminal state.

    Args:
        run_id:              UUID of the run (status must be RUNNING_PHASE_3 or similar)
        graph_artifact_id:   UUID of TASK_GRAPH_APPROVED artifact
        config:              Config
        agent:               Agent protocol implementation
        poll_interval_s:     Poll interval override

    Returns:
        ExecutionResult with final run status.
    """
    effective_config = config
    if poll_interval_s != config.poll_interval_s:
        effective_config = dataclasses.replace(config, poll_interval_s=poll_interval_s)

    def conn_factory():
        """autocommit=True: worker_loop and background_loop manage transactions
        explicitly with BEGIN/COMMIT/ROLLBACK."""
        return psycopg.connect(
            effective_config.database_url,
            autocommit=True,
            row_factory=psycopg.rows.dict_row,
        )

    def tx_conn_factory():
        """autocommit=False: for short-lived transactional work (materialization)."""
        return psycopg.connect(
            effective_config.database_url,
            autocommit=False,
            row_factory=psycopg.rows.dict_row,
        )

    # Step 1: Materialize (idempotent, safe to run multiple times)
    with tx_conn_factory() as conn:
        try:
            materialize_task_runs(conn, run_id, graph_artifact_id, effective_config)
            conn.commit()
        except Exception:
            conn.rollback()
            raise

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
    terminal = {"COMPLETED", "FAILED", "ABORTED", "PAUSED_FOR_DECISION"}
    conn = conn_factory()
    try:
        while True:
            row = conn.execute(
                "SELECT status FROM runs WHERE run_id = %s", (run_id,)
            ).fetchone()
            if row and row["status"] in terminal:
                return row["status"]
            time.sleep(config.poll_interval_s / 2)
    finally:
        conn.close()
