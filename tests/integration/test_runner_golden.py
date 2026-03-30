"""Scenario A: happy path — all tasks succeed.
Graph: PARSE → DESIGN (2 tasks, DESIGN depends on PARSE).
"""
import json
import uuid
import pytest
from ai_dev_system.engine.runner import run_execution
from ai_dev_system.agents.stub import StubAgent
from ai_dev_system.config import Config


def _setup_run(conn, project_id, tmp_path, db_url):
    """Create run + graph artifact backed by a real file."""
    run_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO runs (run_id, project_id, status, title, current_artifacts, metadata)
        VALUES (%s, %s, 'RUNNING_PHASE_3', 'Golden Test', '{}', '{}')
    """, (run_id, project_id))

    graph = {
        "graph_version": 1,
        "tasks": [
            {
                "id": "TASK-PARSE", "execution_type": "atomic",
                "phase": "parse_spec", "type": "design",
                "agent_type": "SpecAnalyst",
                "objective": "parse", "description": "", "done_definition": "done",
                "verification_steps": [], "deps": [],
                "required_inputs": [], "expected_outputs": [],
            },
            {
                "id": "TASK-DESIGN", "execution_type": "atomic",
                "phase": "design_solution", "type": "design",
                "agent_type": "Architect",
                "objective": "design", "description": "", "done_definition": "done",
                "verification_steps": [], "deps": ["TASK-PARSE"],
                "required_inputs": [], "expected_outputs": [],
            },
        ],
    }
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir(parents=True, exist_ok=True)
    (graph_dir / "task_graph.json").write_text(json.dumps(graph))

    artifact_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO artifacts (
            artifact_id, run_id, artifact_type, version, status, created_by,
            input_artifact_ids, content_ref, content_checksum, content_size
        ) VALUES (%s, %s, 'TASK_GRAPH_APPROVED', 1, 'ACTIVE', 'system',
                  '{}', %s, 'stub', 0)
    """, (artifact_id, run_id, str(graph_dir)))
    conn.execute("""
        UPDATE runs SET current_artifacts = jsonb_set(
            current_artifacts, '{task_graph_approved_id}', to_jsonb(%s::text)
        ) WHERE run_id = %s
    """, (artifact_id, run_id))
    conn.commit()
    return run_id, artifact_id


@pytest.mark.integration
def test_golden_run_completes_all_tasks(config, project_id, tmp_path):
    """Full run: materialize → background resolves deps → worker executes → COMPLETED."""
    import psycopg
    import psycopg.rows

    conn = psycopg.connect(config.database_url, autocommit=False,
                           row_factory=psycopg.rows.dict_row)
    try:
        test_config = Config(
            storage_root=str(tmp_path / "storage"),
            database_url=config.database_url,
            poll_interval_s=0.1,
            heartbeat_interval_s=60.0,
            heartbeat_timeout_s=300.0,
        )
        run_id, artifact_id = _setup_run(conn, project_id, tmp_path, config.database_url)

        result = run_execution(
            run_id=run_id,
            graph_artifact_id=artifact_id,
            config=test_config,
            agent=StubAgent(),
        )

        assert result.status == "COMPLETED", f"Expected COMPLETED, got {result.status}"

        task_statuses = {
            r["task_id"]: r["status"]
            for r in conn.execute(
                "SELECT task_id, status FROM task_runs WHERE run_id = %s", (run_id,)
            ).fetchall()
        }
        assert task_statuses.get("TASK-PARSE") == "SUCCESS"
        assert task_statuses.get("TASK-DESIGN") == "SUCCESS"

        events = [
            r["event_type"]
            for r in conn.execute(
                "SELECT event_type FROM events WHERE run_id = %s ORDER BY occurred_at",
                (run_id,)
            ).fetchall()
        ]
        assert "PHASE_STARTED" in events
        assert events.count("TASK_COMPLETED") >= 2
        assert "RUN_COMPLETED" in events
    finally:
        conn.close()
