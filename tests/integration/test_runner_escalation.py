"""Scenario C: failure → escalation → skip → downstream runs → COMPLETED."""
import json
import uuid
import threading
import time
import pytest
from ai_dev_system.engine.runner import run_execution
from ai_dev_system.engine.escalation import resolve_escalation
from ai_dev_system.db.connection import get_connection
from ai_dev_system.db.repos.escalations import EscalationRepo
from ai_dev_system.agents.base import AgentResult
from ai_dev_system.config import Config


class FailingAgent:
    """Fails TASK-IMPL; succeeds everything else."""

    def run(self, task_id, output_path, promoted_outputs=(), context=None, timeout_s=3600.0, file_rules=()):
        import os
        os.makedirs(output_path, exist_ok=True)
        if task_id == "TASK-IMPL":
            return AgentResult(output_path=output_path, error="intentional failure")
        with open(os.path.join(output_path, "out.txt"), "w") as f:
            f.write(f"output of {task_id}")
        return AgentResult(output_path=output_path)


def _setup_failing_run(conn, project_id, tmp_path):
    run_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO runs (run_id, project_id, status, title, current_artifacts, metadata)
        VALUES (?, ?, 'RUNNING_PHASE_3', 'Escalation Test', '{}', '{}')
    """, (run_id, project_id))

    graph = {
        "graph_version": 1,
        "tasks": [
            {
                "id": "TASK-PARSE", "execution_type": "atomic",
                "phase": "parse_spec", "type": "design", "agent_type": "agent",
                "objective": "", "description": "", "done_definition": "",
                "verification_steps": [], "deps": [],
                "required_inputs": [], "expected_outputs": [],
            },
            {
                "id": "TASK-IMPL", "execution_type": "atomic",
                "phase": "implement", "type": "coding", "agent_type": "agent",
                "objective": "", "description": "", "done_definition": "",
                "verification_steps": [], "deps": ["TASK-PARSE"],
                "required_inputs": [], "expected_outputs": [],
            },
            {
                "id": "TASK-VALIDATE", "execution_type": "atomic",
                "phase": "validate", "type": "testing", "agent_type": "agent",
                "objective": "", "description": "", "done_definition": "",
                "verification_steps": [], "deps": ["TASK-IMPL"],
                "required_inputs": [], "expected_outputs": [],
            },
        ],
    }
    graph_dir = tmp_path / "graph_esc"
    graph_dir.mkdir(parents=True, exist_ok=True)
    (graph_dir / "task_graph.json").write_text(json.dumps(graph))

    artifact_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO artifacts (
            artifact_id, run_id, artifact_type, version, status, created_by,
            input_artifact_ids, content_ref, content_checksum, content_size
        ) VALUES (?, ?, 'TASK_GRAPH_APPROVED', 1, 'ACTIVE', 'system',
                  '[]', ?, 'stub', 0)
    """, (artifact_id, run_id, str(graph_dir)))
    conn.execute(
        "UPDATE runs SET current_artifacts = json_set(current_artifacts, '$.task_graph_approved_id', ?) WHERE run_id = ?",
        (artifact_id, run_id),
    )
    conn.commit()
    return run_id, artifact_id


@pytest.mark.integration
def test_escalation_skip_resumes_to_completed(file_config, project_id, tmp_path):
    """TASK-IMPL fails → BLOCKED VALIDATE → human skips → VALIDATE runs → COMPLETED."""
    test_config = Config(
        storage_root=str(tmp_path / "storage_esc"),
        database_url=file_config.database_url,
        poll_interval_s=0.1,
        heartbeat_interval_s=60.0,
        heartbeat_timeout_s=300.0,
        retry_policy={
            "EXECUTION_ERROR":    {"max_retries": 0, "retry_delay_s": 0},
            "ENVIRONMENT_ERROR":  {"max_retries": 0, "retry_delay_s": 0},
            "SPEC_AMBIGUITY":     {"max_retries": 0, "retry_delay_s": 0},
            "SPEC_CONTRADICTION": {"max_retries": 0, "retry_delay_s": 0},
            "UNKNOWN":            {"max_retries": 0, "retry_delay_s": 0},
        },
    )

    setup_conn = get_connection(file_config.database_url)
    run_id, artifact_id = _setup_failing_run(setup_conn, project_id, tmp_path)
    setup_conn.close()

    result_holder = {}

    def run():
        result_holder["result"] = run_execution(
            run_id=run_id,
            graph_artifact_id=artifact_id,
            config=test_config,
            agent=FailingAgent(),
            poll_interval_s=test_config.poll_interval_s,
        )

    t = threading.Thread(target=run, daemon=True)
    t.start()

    # Wait for PAUSED_FOR_DECISION
    resolve_conn = get_connection(file_config.database_url)
    try:
        for _ in range(100):
            time.sleep(0.2)
            row = resolve_conn.execute(
                "SELECT status FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row and row["status"] == "PAUSED_FOR_DECISION":
                break
        else:
            pytest.fail("Run never reached PAUSED_FOR_DECISION")

        # Find open escalation and skip it
        esc_repo = EscalationRepo(resolve_conn)
        open_escs = esc_repo.get_open(run_id)
        assert len(open_escs) == 1, f"Expected 1 escalation, got {len(open_escs)}"
        resolve_escalation(resolve_conn, open_escs[0]["escalation_id"], "skip", run_id)
        resolve_conn.commit()
    finally:
        resolve_conn.close()

    # Wait for run to finish
    t.join(timeout=30)
    assert "result" in result_holder, "run_execution did not complete"
    assert result_holder["result"].status == "COMPLETED", \
        f"Expected COMPLETED after skip, got {result_holder['result'].status}"
