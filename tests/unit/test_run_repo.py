import pytest
from ai_dev_system.db.repos.runs import RunRepo


def test_update_status_changes_run_status(conn, project_id):
    repo = RunRepo(conn)
    # create a run first
    run_id = repo.create(project_id=project_id, pipeline_type="test")
    repo.update_status(run_id, "PAUSED_AT_GATE_1")
    row = conn.execute(
        "SELECT status FROM runs WHERE run_id = %s", (run_id,)
    ).fetchone()
    assert row["status"] == "PAUSED_AT_GATE_1"
