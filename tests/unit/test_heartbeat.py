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


def test_heartbeat_at_actually_advances_in_db(file_db_url):
    """End-to-end: heartbeat_at must be committed so a SEPARATE connection (like
    the background stale-sweep) sees it advance.

    Regression for the missing-commit bug: HeartbeatThread.run() ran the UPDATE
    but never committed, and get_connection() opens SQLite in manual-commit mode
    (isolation_level=''), so every heartbeat was rolled back on close(). Effect:
    heartbeat_at never moved past claim time → any task running longer than
    heartbeat_timeout_s got a false worker_heartbeat_timeout and a spurious retry.
    """
    from ai_dev_system.db.connection import get_connection
    from ai_dev_system.db.helpers import new_uuid

    run_id = new_uuid()
    task_run_id = new_uuid()
    stale = "2000-01-01 00:00:00"

    seed = get_connection(file_db_url)
    seed.execute(
        "INSERT INTO runs (run_id, project_id, status, title, current_artifacts, metadata)"
        " VALUES (?, 'p1', 'RUNNING_PHASE_3', 'hb', '{}', '{}')",
        (run_id,),
    )
    seed.execute(
        "INSERT INTO task_runs (task_run_id, run_id, task_id, attempt_number, status,"
        " agent_type, input_artifact_ids, resolved_dependencies, promoted_outputs,"
        " worker_id, heartbeat_at)"
        " VALUES (?, ?, 'TASK-1', 1, 'RUNNING', 'StubAgent', '[]', '[]', '[]', 'w1', ?)",
        (task_run_id, run_id, stale),
    )
    seed.commit()
    seed.close()

    hb = HeartbeatThread(
        conn_factory=lambda: get_connection(file_db_url),
        task_run_id=task_run_id,
        interval_s=0.05,
    )
    hb.start()
    time.sleep(0.3)
    hb.stop()

    check = get_connection(file_db_url)
    hb_at = check.execute(
        "SELECT heartbeat_at FROM task_runs WHERE task_run_id = ?", (task_run_id,)
    ).fetchone()["heartbeat_at"]
    check.close()

    assert hb_at != stale, (
        "heartbeat_at was never persisted — HeartbeatThread.run() is missing conn.commit()"
    )


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
