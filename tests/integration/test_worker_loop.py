# tests/integration/test_worker_loop.py
import os
import pytest
from ai_dev_system.engine.worker import pickup_task, execute_and_promote
from ai_dev_system.agents.stub import StubAgent

def test_full_pickup_execute_promote(conn, seed_run, seed_task_run, tmp_path, config):
    """READY task → pickup → execute stub → promote → artifact ACTIVE in DB."""
    cfg = config.__class__(storage_root=str(tmp_path), database_url=config.database_url)
    conn.execute("""
        UPDATE task_runs
        SET promoted_outputs = '[{"name": "result.json", "artifact_type": "EXECUTION_LOG", "description": "stub"}]'
        WHERE task_run_id = %s
    """, (seed_task_run,))

    task = pickup_task(conn, cfg, run_id=seed_run, worker_id="w1")
    assert task is not None

    result = StubAgent().run(task["task_id"], task["temp_path"], task["promoted_outputs_parsed"])
    status = execute_and_promote(conn, cfg, task, result, worker_id="w1")

    assert status == "SUCCESS"
    artifact = conn.execute("""
        SELECT status, version FROM artifacts WHERE run_id = %s AND artifact_type = 'EXECUTION_LOG'
    """, (seed_run,)).fetchone()
    assert artifact["status"] == "ACTIVE"
    assert artifact["version"] == 1

def test_no_ready_task_returns_none(conn, seed_run, tmp_path, config):
    cfg = config.__class__(storage_root=str(tmp_path), database_url=config.database_url)
    task = pickup_task(conn, cfg, run_id=seed_run, worker_id="w1")
    assert task is None
