"""
End-to-end: insert run + 1 PENDING task → resolve → pickup → execute → promote → verify ACTIVE artifact
"""
import os
import uuid
import pytest
from ai_dev_system.config import Config
from ai_dev_system.engine.resolver import resolve_dependencies
from ai_dev_system.engine.worker import pickup_task, execute_and_promote
from ai_dev_system.agents.stub import StubAgent


def test_single_task_flow(conn, tmp_path, config):
    """
    Full E2E flow: PENDING → READY → pickup → execute → SUCCESS with artifact ACTIVE
    """
    cfg = Config(storage_root=str(tmp_path), database_url=config.database_url)
    run_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())

    # Seed run (must include project_id — NOT NULL constraint)
    conn.execute("""
        INSERT INTO runs (run_id, project_id, status, title, current_artifacts, metadata)
        VALUES (%s, %s, 'RUNNING_PHASE_3', 'E2E Test', '{}', '{}')
    """, (run_id, project_id))

    # Seed PENDING task with no deps
    task_run_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO task_runs (task_run_id, run_id, task_id, attempt_number, status,
            agent_type, input_artifact_ids, resolved_dependencies, promoted_outputs)
        VALUES (%s, %s, 'TASK-1', 1, 'PENDING', 'StubAgent', '{}', '{}',
                '[{"name": "output.json", "artifact_type": "EXECUTION_LOG", "description": "e2e"}]')
    """, (task_run_id, run_id))

    # Phase 1: Resolve dependencies
    promoted_count = resolve_dependencies(conn, run_id)
    assert promoted_count == 1

    status = conn.execute(
        "SELECT status FROM task_runs WHERE task_run_id = %s", (task_run_id,)
    ).fetchone()["status"]
    assert status == "READY"

    # Phase 2: Worker picks up (Tx 1) then executes + promotes (Tx 2)
    task = pickup_task(conn, cfg, run_id=run_id, worker_id="e2e-worker")
    assert task is not None
    agent_result = StubAgent().run(task["task_id"], task["temp_path"], task["promoted_outputs_parsed"])
    status = execute_and_promote(conn, cfg, task, agent_result, worker_id="e2e-worker")
    assert status == "SUCCESS"

    # Phase 3: Verify artifact
    artifact = conn.execute("""
        SELECT status, version, content_ref FROM artifacts
        WHERE run_id = %s AND artifact_type = 'EXECUTION_LOG'
    """, (run_id,)).fetchone()
    assert artifact["status"] == "ACTIVE"
    assert artifact["version"] == 1
    assert artifact["content_ref"], "content_ref must be a non-empty absolute path"
    # _complete.marker is written by promote_output (not the agent); output.json by StubAgent
    assert os.path.exists(os.path.join(artifact["content_ref"], "_complete.marker"))
    assert os.path.exists(os.path.join(artifact["content_ref"], "output.json"))

    # Phase 4: Verify task_run final state
    task_row = conn.execute(
        "SELECT status, output_artifact_id FROM task_runs WHERE task_run_id = %s", (task_run_id,)
    ).fetchone()
    assert task_row["status"] == "SUCCESS"
    assert task_row["output_artifact_id"] is not None
