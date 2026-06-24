"""The real core skeleton chain (PARSE -> DESIGN -> IMPL -> VALIDATE) executes
to COMPLETED, with each task's outputs flowing to the next via the
name-addressed current_artifacts.outputs map. StubAgent stands in for a real
agent (writes a file per declared output).
"""
import copy
import json
import os
import uuid

import pytest

from ai_dev_system.agents.stub import StubAgent
from ai_dev_system.db.connection import get_connection
from ai_dev_system.db.helpers import load_json
from ai_dev_system.engine.runner import run_execution
from ai_dev_system.task_graph.skeleton import CORE_SKELETON


def _setup(conn, project_id, tmp_path):
    run_id = str(uuid.uuid4())
    spec_aid = str(uuid.uuid4())

    # The run + its pre-existing SPEC_BUNDLE (what TASK-PARSE's "raw_spec" means).
    conn.execute(
        "INSERT INTO runs (run_id, project_id, status, title, current_artifacts, metadata) "
        "VALUES (?, ?, 'RUNNING_PHASE_3', 'chain', ?, '{}')",
        (run_id, project_id, json.dumps({"spec_bundle_id": spec_aid})),
    )
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    (spec_dir / "proposal.md").write_text("the spec", encoding="utf-8")
    conn.execute(
        "INSERT INTO artifacts (artifact_id, run_id, artifact_type, version, status, created_by, "
        "input_artifact_ids, content_ref, content_checksum, content_size) "
        "VALUES (?, ?, 'SPEC_BUNDLE', 1, 'ACTIVE', 'system', '[]', ?, 'x', 0)",
        (spec_aid, run_id, str(spec_dir)),
    )

    # The approved task graph = the real core skeleton.
    graph = {"graph_version": 1, "tasks": copy.deepcopy(CORE_SKELETON)}
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    (graph_dir / "task_graph.json").write_text(json.dumps(graph), encoding="utf-8")
    graph_aid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO artifacts (artifact_id, run_id, artifact_type, version, status, created_by, "
        "input_artifact_ids, content_ref, content_checksum, content_size) "
        "VALUES (?, ?, 'TASK_GRAPH_APPROVED', 1, 'ACTIVE', 'system', '[]', ?, 'x', 0)",
        (graph_aid, run_id, str(graph_dir)),
    )
    conn.execute(
        "UPDATE runs SET current_artifacts = json_set(current_artifacts, '$.task_graph_approved_id', ?) WHERE run_id = ?",
        (graph_aid, run_id),
    )
    conn.commit()
    return run_id, graph_aid


@pytest.mark.integration
def test_core_chain_completes(file_config, project_id, tmp_path):
    conn = get_connection(file_config.database_url)
    try:
        run_id, graph_aid = _setup(conn, project_id, tmp_path)

        result = run_execution(
            run_id=run_id, graph_artifact_id=graph_aid,
            config=file_config, agent=StubAgent(),
            poll_interval_s=file_config.poll_interval_s,
        )

        assert result.status == "COMPLETED", f"got {result.status}"

        statuses = {
            r["task_id"]: r["status"]
            for r in conn.execute(
                "SELECT task_id, status FROM task_runs WHERE run_id = ?", (run_id,)
            ).fetchall()
        }
        for tid in ("TASK-PARSE", "TASK-DESIGN", "TASK-IMPL", "TASK-VALIDATE"):
            assert statuses.get(tid) == "SUCCESS", f"{tid}={statuses.get(tid)}"

        # Each task's declared outputs are name-addressed for downstream resolution.
        ca = load_json(
            conn.execute("SELECT current_artifacts FROM runs WHERE run_id=?", (run_id,)).fetchone()["current_artifacts"],
            default={},
        )
        outputs = ca.get("outputs", {})
        for name in ("spec_bundle", "design_doc", "implementation", "unit_tests", "validation_report"):
            assert name in outputs, f"missing output {name}; have {sorted(outputs)}"
    finally:
        conn.close()
