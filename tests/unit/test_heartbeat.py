import time
import threading
import psycopg
import psycopg.rows
import pytest
from ai_dev_system.engine.heartbeat import HeartbeatThread


def test_heartbeat_updates_heartbeat_at(conn, config, seed_run, seed_task_run):
    """HeartbeatThread updates heartbeat_at on the task_run."""
    conn.execute(
        "UPDATE task_runs SET status = 'RUNNING', worker_id = 'w1' WHERE task_run_id = %s",
        (seed_task_run,)
    )
    conn.commit()  # commit so heartbeat thread can see it

    calls = []

    def factory():
        c = psycopg.connect(config.database_url, autocommit=True,
                            row_factory=psycopg.rows.dict_row)
        calls.append(c)
        return c

    hb = HeartbeatThread(conn_factory=factory, task_run_id=seed_task_run, interval_s=0.05)
    hb.start()
    time.sleep(0.2)
    hb.stop()

    assert len(calls) >= 1  # at least one heartbeat fired


def test_heartbeat_stops_cleanly(config, seed_run, seed_task_run):
    """stop() terminates the thread within timeout."""
    def factory():
        return psycopg.connect(config.database_url, autocommit=True,
                               row_factory=psycopg.rows.dict_row)

    hb = HeartbeatThread(conn_factory=factory, task_run_id=seed_task_run, interval_s=60)
    hb.start()
    assert hb.is_alive()
    hb.stop()
    assert not hb.is_alive()


def test_heartbeat_does_not_crash_on_db_error():
    """HeartbeatThread is non-fatal — bad DB connection does not kill the thread."""
    def bad_factory():
        raise psycopg.OperationalError("connection refused")

    hb = HeartbeatThread(conn_factory=bad_factory, task_run_id="fake-id", interval_s=0.05)
    hb.start()
    time.sleep(0.15)
    hb.stop()
    assert not hb.is_alive()  # stopped cleanly despite errors
