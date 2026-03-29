import pytest
from ai_dev_system.db.repos.task_runs import TaskRunRepo


def test_pickup_returns_ready_task(conn, seed_run, seed_task_run):
    repo = TaskRunRepo(conn)
    task = repo.pickup(run_id=seed_run, worker_id="worker-1")
    assert task is not None
    assert task["task_id"] == "TASK-1"
    assert task["status"] == "RUNNING"


def test_pickup_returns_none_when_no_ready_tasks(conn, seed_run):
    repo = TaskRunRepo(conn)
    task = repo.pickup(run_id=seed_run, worker_id="worker-1")
    assert task is None


def test_pickup_is_exclusive(conn, seed_run, seed_task_run):
    repo = TaskRunRepo(conn)
    t1 = repo.pickup(run_id=seed_run, worker_id="worker-1")
    t2 = repo.pickup(run_id=seed_run, worker_id="worker-2")
    assert t1 is not None
    assert t2 is None


def test_mark_success(conn, seed_run, seed_task_run):
    repo = TaskRunRepo(conn)
    task = repo.pickup(run_id=seed_run, worker_id="w1")
    repo.mark_success(task["task_run_id"], output_ref="/tmp/out", output_artifact_id=None)
    updated = conn.execute(
        "SELECT status FROM task_runs WHERE task_run_id = %s",
        (task["task_run_id"],)
    ).fetchone()
    assert updated["status"] == "SUCCESS"
