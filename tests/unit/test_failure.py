import uuid
import pytest
from ai_dev_system.engine.failure import propagate_failure, _handle_failure
from ai_dev_system.config import Config


def _insert_task(conn, run_id, task_id, status, deps=None):
    tid = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO task_runs (
            task_run_id, run_id, task_id, attempt_number, status,
            agent_type, input_artifact_ids, resolved_dependencies, promoted_outputs,
            retry_count, worker_id, locked_at, heartbeat_at, started_at
        ) VALUES (%s, %s, %s, 1, %s, 'agent', '{}', %s, '[]', 0,
                  'w1', now(), now(), now())
    """, (tid, run_id, task_id, status, deps or []))
    return tid


def test_propagate_failure_blocks_direct_child(conn, seed_run):
    parent_id = _insert_task(conn, seed_run, "TASK-A", "FAILED_FINAL")
    child_id = _insert_task(conn, seed_run, "TASK-B", "PENDING", deps=["TASK-A"])
    propagate_failure(conn, seed_run, "TASK-A", parent_id)
    row = conn.execute(
        "SELECT status FROM task_runs WHERE task_run_id = %s", (child_id,)
    ).fetchone()
    assert row["status"] == "BLOCKED_BY_FAILURE"


def test_propagate_failure_bfs_blocks_grandchild(conn, seed_run):
    """BFS: A fails → B blocked → C blocked."""
    a_id = _insert_task(conn, seed_run, "TASK-A", "FAILED_FINAL")
    b_id = _insert_task(conn, seed_run, "TASK-B", "PENDING", deps=["TASK-A"])
    c_id = _insert_task(conn, seed_run, "TASK-C", "PENDING", deps=["TASK-B"])
    propagate_failure(conn, seed_run, "TASK-A", a_id)
    for tid in (b_id, c_id):
        row = conn.execute(
            "SELECT status FROM task_runs WHERE task_run_id = %s", (tid,)
        ).fetchone()
        assert row["status"] == "BLOCKED_BY_FAILURE", f"Expected BLOCKED for {tid}"


def test_propagate_failure_does_not_overwrite_terminal(conn, seed_run):
    """BFS skips SUCCESS and SKIPPED nodes."""
    a_id = _insert_task(conn, seed_run, "TASK-A", "FAILED_FINAL")
    _insert_task(conn, seed_run, "TASK-B", "SUCCESS", deps=["TASK-A"])
    propagate_failure(conn, seed_run, "TASK-A", a_id)
    row = conn.execute(
        "SELECT status FROM task_runs WHERE run_id = %s AND task_id = 'TASK-B'", (seed_run,)
    ).fetchone()
    assert row["status"] == "SUCCESS"


def test_propagate_failure_creates_escalation(conn, seed_run):
    a_id = _insert_task(conn, seed_run, "TASK-A", "RUNNING")
    conn.execute(
        "UPDATE task_runs SET status = 'FAILED_FINAL' WHERE task_run_id = %s", (a_id,)
    )
    propagate_failure(conn, seed_run, "TASK-A", a_id)
    esc = conn.execute(
        "SELECT * FROM escalations WHERE run_id = %s", (seed_run,)
    ).fetchone()
    assert esc is not None
    assert esc["status"] == "OPEN"
    assert esc["reason"] == "TASK_FAILURE"


def test_handle_failure_creates_retry_when_under_max(conn, seed_run, config):
    task_id = _insert_task(conn, seed_run, "TASK-A", "RUNNING")
    task = {"task_run_id": task_id, "task_id": "TASK-A", "run_id": seed_run,
            "retry_count": 0, "attempt_number": 1,
            "agent_type": "agent", "resolved_dependencies": [],
            "task_graph_artifact_id": None, "agent_routing_key": None,
            "context_snapshot": None}
    _handle_failure(conn, config, task, "exploded", "w1", seed_run, "EXECUTION_ERROR")
    row = conn.execute(
        "SELECT status FROM task_runs WHERE task_run_id = %s", (task_id,)
    ).fetchone()
    assert row["status"] == "FAILED_RETRYABLE"
    count = conn.execute(
        "SELECT COUNT(*) FROM task_runs WHERE run_id = %s AND task_id = 'TASK-A'",
        (seed_run,)
    ).scalar()
    assert count == 2


def test_handle_failure_marks_final_when_max_exceeded(conn, seed_run, config):
    max_retries = config.retry_policy["EXECUTION_ERROR"]["max_retries"]
    task_id = _insert_task(conn, seed_run, "TASK-A", "RUNNING")
    task = {"task_run_id": task_id, "task_id": "TASK-A", "run_id": seed_run,
            "retry_count": max_retries,  # already at max
            "attempt_number": max_retries + 1,
            "agent_type": "agent", "resolved_dependencies": [],
            "task_graph_artifact_id": None, "agent_routing_key": None,
            "context_snapshot": None}
    _handle_failure(conn, config, task, "still broken", "w1", seed_run, "EXECUTION_ERROR")
    row = conn.execute(
        "SELECT status FROM task_runs WHERE task_run_id = %s", (task_id,)
    ).fetchone()
    assert row["status"] == "FAILED_FINAL"
