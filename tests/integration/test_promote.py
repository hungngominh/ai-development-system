import os
import uuid
import pytest
from pathlib import Path
from ai_dev_system.storage.promote import promote_output
from ai_dev_system.agents.base import PromotedOutput

@pytest.fixture
def temp_output(tmp_path):
    """Simulate task output in temp path."""
    out = tmp_path / "agent_output"
    out.mkdir()
    (out / "result.json").write_text('{"status": "done"}')
    return str(out)

@pytest.fixture
def running_task_run(conn, seed_run):
    """A task_run in RUNNING state (for promote_output which needs RUNNING status)."""
    tid = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO task_runs (task_run_id, run_id, task_id, attempt_number, status,
            agent_type, input_artifact_ids, resolved_dependencies, promoted_outputs)
        VALUES (%s, %s, 'TASK-1', 1, 'RUNNING', 'StubAgent', '{}', '{}', '[]')
    """, (tid, seed_run))
    return {"task_run_id": tid, "run_id": seed_run, "task_id": "TASK-1",
            "attempt_number": 1, "input_artifact_ids": []}

def test_promote_creates_artifact_in_db(conn, seed_run, running_task_run, temp_output, tmp_path, config):
    """promote_output() inserts artifact record and returns artifact_id."""
    promoted = PromotedOutput(name="result.json", artifact_type="EXECUTION_LOG")
    cfg = config.__class__(storage_root=str(tmp_path), database_url=config.database_url)

    artifact_id = promote_output(conn, cfg, running_task_run, promoted, temp_output)

    assert artifact_id is not None
    row = conn.execute(
        "SELECT status, content_ref, version FROM artifacts WHERE artifact_id = %s",
        (artifact_id,)
    ).fetchone()
    assert row["status"] == "ACTIVE"
    assert row["version"] == 1
    assert os.path.exists(row["content_ref"])

def test_promote_writes_complete_marker(conn, seed_run, running_task_run, temp_output, tmp_path, config):
    promoted = PromotedOutput(name="result.json", artifact_type="EXECUTION_LOG")
    cfg = config.__class__(storage_root=str(tmp_path), database_url=config.database_url)

    artifact_id = promote_output(conn, cfg, running_task_run, promoted, temp_output)
    row = conn.execute("SELECT content_ref FROM artifacts WHERE artifact_id = %s", (artifact_id,)).fetchone()
    assert os.path.exists(os.path.join(row["content_ref"], "_complete.marker"))

def test_promote_increments_version_on_second_call(conn, seed_run, tmp_path, config):
    """Two promotions of same artifact_type on same run → versions 1 and 2."""
    promoted = PromotedOutput(name="f.txt", artifact_type="EXECUTION_LOG")
    cfg = config.__class__(storage_root=str(tmp_path), database_url=config.database_url)

    def make_task_run(task_id):
        tid = str(uuid.uuid4())
        conn.execute("""
            INSERT INTO task_runs (task_run_id, run_id, task_id, attempt_number, status,
                agent_type, input_artifact_ids, resolved_dependencies, promoted_outputs)
            VALUES (%s, %s, %s, 1, 'RUNNING', 'StubAgent', '{}', '{}', '[]')
        """, (tid, seed_run, task_id))
        return {"task_run_id": tid, "run_id": seed_run, "task_id": task_id,
                "attempt_number": 1, "input_artifact_ids": []}

    def make_temp(name):
        d = tmp_path / name
        d.mkdir()
        (d / "f.txt").write_text("data")
        return str(d)

    task1 = make_task_run("TASK-1")
    id1 = promote_output(conn, cfg, task1, promoted, make_temp("out1"))

    task2 = make_task_run("TASK-2")
    id2 = promote_output(conn, cfg, task2, promoted, make_temp("out2"))

    r1 = conn.execute("SELECT version, status FROM artifacts WHERE artifact_id = %s", (id1,)).fetchone()
    r2 = conn.execute("SELECT version, status FROM artifacts WHERE artifact_id = %s", (id2,)).fetchone()
    assert r1["version"] == 1
    assert r1["status"] == "SUPERSEDED"
    assert r2["version"] == 2
    assert r2["status"] == "ACTIVE"
