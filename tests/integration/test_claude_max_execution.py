"""ClaudeMaxAgent drives the real execution worker loop to COMPLETED.

A fake LLM (returning a files-JSON) stands in for `claude -p`, so no real Max
calls are made. Mirrors test_runner_golden but swaps StubAgent for the
Max-backed agent — proving it satisfies the Agent protocol end to end.
"""
import json
import re
import uuid

import pytest

from ai_dev_system.agents.claude_max_agent import ClaudeMaxAgent
from ai_dev_system.config import Config  # noqa: F401 (kept parallel to golden test imports)
from ai_dev_system.db.connection import get_connection
from ai_dev_system.engine.runner import run_execution


class _EchoFilesLLM:
    """Return a files-JSON covering the exact output names the agent asked for
    (parsed from the prompt); fall back to a single output.txt otherwise."""

    def complete(self, system: str, user: str) -> str:
        m = re.search(r"exact names\): \[(.*?)\]", user)
        names = []
        if m and m.group(1).strip():
            names = [n.strip().strip("'\"") for n in m.group(1).split(",") if n.strip()]
        files = {n: f"# generated {n}\n" for n in names} or {"output.txt": "generated\n"}
        return json.dumps({"files": files, "summary": "ok"})


def _setup_run(conn, project_id, tmp_path):
    run_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO runs (run_id, project_id, status, title, current_artifacts, metadata)
           VALUES (?, ?, 'RUNNING_PHASE_3', 'Max Exec Test', '{}', '{}')""",
        (run_id, project_id),
    )
    graph = {
        "graph_version": 1,
        "tasks": [
            {
                "id": "TASK-PARSE", "execution_type": "atomic", "phase": "parse_spec",
                "type": "design", "agent_type": "SpecAnalyst", "objective": "parse",
                "description": "", "done_definition": "done", "verification_steps": [],
                "deps": [], "required_inputs": [], "expected_outputs": [],
            },
            {
                "id": "TASK-DESIGN", "execution_type": "atomic", "phase": "design_solution",
                "type": "design", "agent_type": "Architect", "objective": "design",
                "description": "", "done_definition": "done", "verification_steps": [],
                "deps": ["TASK-PARSE"], "required_inputs": [], "expected_outputs": [],
            },
        ],
    }
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir(parents=True, exist_ok=True)
    (graph_dir / "task_graph.json").write_text(json.dumps(graph))

    artifact_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO artifacts (
               artifact_id, run_id, artifact_type, version, status, created_by,
               input_artifact_ids, content_ref, content_checksum, content_size
           ) VALUES (?, ?, 'TASK_GRAPH_APPROVED', 1, 'ACTIVE', 'system', '[]', ?, 'x', 0)""",
        (artifact_id, run_id, str(graph_dir)),
    )
    conn.execute(
        "UPDATE runs SET current_artifacts = json_set(current_artifacts, '$.task_graph_approved_id', ?) WHERE run_id = ?",
        (artifact_id, run_id),
    )
    conn.commit()
    return run_id, artifact_id


@pytest.mark.integration
def test_claude_max_agent_completes_run(file_config, project_id, tmp_path):
    conn = get_connection(file_config.database_url)
    try:
        run_id, artifact_id = _setup_run(conn, project_id, tmp_path)

        result = run_execution(
            run_id=run_id,
            graph_artifact_id=artifact_id,
            config=file_config,
            agent=ClaudeMaxAgent(llm=_EchoFilesLLM()),
            poll_interval_s=file_config.poll_interval_s,
        )

        assert result.status == "COMPLETED", f"got {result.status}"
        statuses = {
            r["task_id"]: r["status"]
            for r in conn.execute(
                "SELECT task_id, status FROM task_runs WHERE run_id = ?", (run_id,)
            ).fetchall()
        }
        assert statuses.get("TASK-PARSE") == "SUCCESS"
        assert statuses.get("TASK-DESIGN") == "SUCCESS"
    finally:
        conn.close()
