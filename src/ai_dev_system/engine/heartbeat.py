# src/ai_dev_system/engine/heartbeat.py
import logging
import threading
from typing import Callable

import psycopg

logger = logging.getLogger(__name__)


class HeartbeatThread(threading.Thread):
    """Per-task heartbeat. Lives only while agent is executing.
    Receives conn_factory (not conn) — creates and closes a short-lived
    connection each tick, so the worker thread's connection is not shared.
    Non-fatal: any DB error is logged and swallowed.
    """

    def __init__(
        self,
        conn_factory: Callable[[], psycopg.Connection],
        task_run_id: str,
        interval_s: float = 30.0,
    ):
        super().__init__(daemon=True, name=f"hb-{task_run_id[:8]}")
        self._stop_event = threading.Event()
        self.conn_factory = conn_factory
        self.task_run_id = task_run_id
        self.interval_s = interval_s

    def run(self) -> None:
        while not self._stop_event.wait(self.interval_s):
            conn = None
            try:
                conn = self.conn_factory()
                conn.execute("""
                    UPDATE task_runs SET heartbeat_at = now()
                    WHERE task_run_id = %s AND status = 'RUNNING'
                """, (self.task_run_id,))
            except Exception:
                logger.warning(
                    "HeartbeatThread: failed to update heartbeat for %s",
                    self.task_run_id, exc_info=True
                )
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass

    def stop(self) -> None:
        """Signal thread to stop and wait up to 5 seconds."""
        self._stop_event.set()
        self.join(timeout=5)
        if self.is_alive():
            logger.warning("HeartbeatThread did not stop cleanly for %s", self.task_run_id)
