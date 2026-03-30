import uuid
from ai_dev_system.db.repos.runs import RunRepo
from ai_dev_system.db.repos.task_runs import TaskRunRepo


def test_run_repo_create(conn, project_id):
    repo = RunRepo(conn)
    run_id = repo.create(project_id=project_id, pipeline_type="spec_pipeline")
    assert run_id
    row = conn.execute("SELECT * FROM runs WHERE run_id = %s", (run_id,)).fetchone()
    assert row["status"] == "RUNNING_PHASE_1A"
    assert str(row["project_id"]) == project_id


def test_task_run_repo_create_sync(conn, project_id):
    run_repo = RunRepo(conn)
    run_id = run_repo.create(project_id=project_id, pipeline_type="spec_pipeline")
    repo = TaskRunRepo(conn)
    task_run = repo.create_sync(run_id=run_id, task_type="normalize_idea")
    assert task_run["task_run_id"]
    assert task_run["run_id"] == run_id
    assert task_run["task_id"] == "normalize_idea"
    assert task_run["attempt_number"] == 1
    row = conn.execute(
        "SELECT * FROM task_runs WHERE task_run_id = %s", (task_run["task_run_id"],)
    ).fetchone()
    assert row["status"] == "RUNNING"
