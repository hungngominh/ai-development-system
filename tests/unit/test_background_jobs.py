import uuid
import time
import pytest
from datetime import datetime, timezone, timedelta
from ai_dev_system.engine.background import mark_ready_tasks, recover_dead_tasks, check_completion
from ai_dev_system.config import Config


def _insert_task(conn, run_id, task_id, status, deps=None, retry_at=None, retry_count=0):
    tid = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO task_runs (
            task_run_id, run_id, task_id, attempt_number, status,
            agent_type, input_artifact_ids, resolved_dependencies, promoted_outputs,
            retry_count, retry_at
        ) VALUES (%s, %s, %s, 1, %s, 'agent', '{}', %s, '[]', %s, %s)
    """, (tid, run_id, task_id, status, deps or [], retry_count, retry_at))
    return tid


def test_mark_ready_tasks_no_deps(conn, seed_run):
    _insert_task(conn, seed_run, "TASK-A", "PENDING", deps=[])
    count = mark_ready_tasks(conn, seed_run)
    assert count >= 1
    row = conn.execute(
        "SELECT status FROM task_runs WHERE run_id = %s AND task_id = 'TASK-A'", (seed_run,)
    ).fetchone()
    assert row["status"] == "READY"


def test_mark_ready_tasks_respects_retry_at(conn, seed_run):
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    _insert_task(conn, seed_run, "TASK-A", "PENDING", deps=[], retry_at=future)
    count = mark_ready_tasks(conn, seed_run)
    assert count == 0
    row = conn.execute(
        "SELECT status FROM task_runs WHERE run_id = %s AND task_id = 'TASK-A'", (seed_run,)
    ).fetchone()
    assert row["status"] == "PENDING"  # not promoted yet


def test_mark_ready_tasks_waits_for_dep(conn, seed_run):
    _insert_task(conn, seed_run, "TASK-A", "PENDING", deps=["TASK-PARSE"])
    _insert_task(conn, seed_run, "TASK-PARSE", "PENDING", deps=[])
    count = mark_ready_tasks(conn, seed_run)
    statuses = {
        r["task_id"]: r["status"]
        for r in conn.execute(
            "SELECT task_id, status FROM task_runs WHERE run_id = %s", (seed_run,)
        ).fetchall()
    }
    assert statuses["TASK-PARSE"] == "READY"
    assert statuses["TASK-A"] == "PENDING"


def test_check_completion_marks_success(conn, seed_run):
    _insert_task(conn, seed_run, "TASK-A", "SUCCESS")
    conn.execute("UPDATE runs SET status = 'RUNNING_EXECUTION' WHERE run_id = %s", (seed_run,))
    check_completion(conn, seed_run)
    status = conn.execute(
        "SELECT status FROM runs WHERE run_id = %s", (seed_run,)
    ).scalar()
    assert status == "COMPLETED"


def test_check_completion_paused_when_failed_and_blocked(conn, seed_run):
    _insert_task(conn, seed_run, "TASK-A", "FAILED_FINAL")
    _insert_task(conn, seed_run, "TASK-B", "BLOCKED_BY_FAILURE", deps=["TASK-A"])
    conn.execute("UPDATE runs SET status = 'RUNNING_EXECUTION' WHERE run_id = %s", (seed_run,))
    check_completion(conn, seed_run)
    status = conn.execute(
        "SELECT status FROM runs WHERE run_id = %s", (seed_run,)
    ).scalar()
    assert status == "PAUSED_FOR_DECISION"


def test_check_completion_paused_on_leaf_failure(conn, seed_run):
    """Leaf task failure (no downstream) also pauses the run."""
    _insert_task(conn, seed_run, "TASK-A", "FAILED_FINAL")
    conn.execute("UPDATE runs SET status = 'RUNNING_EXECUTION' WHERE run_id = %s", (seed_run,))
    check_completion(conn, seed_run)
    status = conn.execute(
        "SELECT status FROM runs WHERE run_id = %s", (seed_run,)
    ).scalar()
    assert status == "PAUSED_FOR_DECISION"


def test_recover_dead_tasks_creates_retry(conn, config, seed_run):
    tid = _insert_task(conn, seed_run, "TASK-A", "RUNNING")
    conn.execute(
        "UPDATE task_runs SET heartbeat_at = now() - interval '300 seconds', "
        "worker_id = 'dead-worker', retry_count = 0 WHERE task_run_id = %s",
        (tid,)
    )
    recover_dead_tasks(conn, seed_run, config)
    row = conn.execute(
        "SELECT status FROM task_runs WHERE task_run_id = %s", (tid,)
    ).fetchone()
    assert row["status"] == "FAILED_RETRYABLE"
    count = conn.execute(
        "SELECT COUNT(*) FROM task_runs WHERE run_id = %s AND task_id = 'TASK-A'", (seed_run,)
    ).scalar()
    assert count == 2
