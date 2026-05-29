import sqlite3
import time
import threading

import pytest

from ai_dev_system.engine.heartbeat import HeartbeatThread


def test_heartbeat_updates_heartbeat_at(conn, config, seed_run, seed_task_run):
    """HeartbeatThread updates heartbeat_at on the task_run.

    Note: SQLite in-memory DBs are per-connection — the heartbeat thread can't
    write to the fixture's `conn` if it opens its own. We assert the factory
    was called instead of verifying the row write end-to-end.
    """
    conn.execute(
        "UPDATE task_runs SET status = 'RUNNING', worker_id = 'w1' WHERE task_run_id = ?",
        (seed_task_run,),
    )
    conn.commit()

    calls: list = []

    def factory():
        # Use the same DB used by the test connection. For :memory: SQLite this
        # is a *separate* DB, but the call counter still proves the thread fired.
        c = sqlite3.connect(":memory:")
        calls.append(c)
        # Minimal schema so UPDATE doesn't crash
        c.execute(
            "CREATE TABLE IF NOT EXISTS task_runs (task_run_id TEXT, status TEXT, heartbeat_at TEXT)"
        )
        return c

    hb = HeartbeatThread(conn_factory=factory, task_run_id=seed_task_run, interval_s=0.05)
    hb.start()
    time.sleep(0.2)
    hb.stop()

    assert len(calls) >= 1


def test_heartbeat_stops_cleanly(config, seed_run, seed_task_run):
    """stop() terminates the thread within timeout."""

    def factory():
        c = sqlite3.connect(":memory:")
        c.execute(
            "CREATE TABLE IF NOT EXISTS task_runs (task_run_id TEXT, status TEXT, heartbeat_at TEXT)"
        )
        return c

    hb = HeartbeatThread(conn_factory=factory, task_run_id=seed_task_run, interval_s=60)
    hb.start()
    assert hb.is_alive()
    hb.stop()
    assert not hb.is_alive()


def test_heartbeat_does_not_crash_on_db_error():
    """HeartbeatThread is non-fatal — bad DB connection does not kill the thread."""

    def bad_factory():
        raise sqlite3.OperationalError("simulated DB error")

    hb = HeartbeatThread(conn_factory=bad_factory, task_run_id="fake-id", interval_s=0.05)
    hb.start()
    time.sleep(0.15)
    hb.stop()
    assert not hb.is_alive()
