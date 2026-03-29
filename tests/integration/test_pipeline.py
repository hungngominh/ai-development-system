import os
import json
from pathlib import Path
from ai_dev_system.pipeline import run_spec_pipeline, PipelineAborted
from ai_dev_system.gate.stub import StubGateIO


def test_spec_pipeline_end_to_end(conn, config, project_id):
    """Full pipeline: normalize -> gate 1 (approve) -> spec bundle."""
    io = StubGateIO(
        edits={
            "problem": "No knowledge sharing",
            "goal": "Internal forum",
            "constraints": {"hard": ["Must use PostgreSQL"]},
            "scope": {"type": "product", "complexity_hint": "medium"},
        },
        approve=True,
    )
    bundle = run_spec_pipeline(
        raw_idea="Build a forum for sharing knowledge",
        config=config,
        conn=conn,
        project_id=project_id,
        io=io,
    )
    assert bundle.version == 1
    assert (bundle.root_dir / "problem.md").exists()
    assert (bundle.root_dir / "constraints.md").exists()
    # Verify DB state
    runs = conn.execute("SELECT * FROM runs WHERE project_id = %s", (project_id,)).fetchall()
    assert len(runs) == 1
    task_runs = conn.execute(
        "SELECT * FROM task_runs WHERE run_id = %s ORDER BY started_at",
        (runs[0]["run_id"],)
    ).fetchall()
    assert len(task_runs) == 3
    assert all(tr["status"] == "SUCCESS" for tr in task_runs)
    # Verify artifacts
    artifacts = conn.execute(
        "SELECT * FROM artifacts WHERE run_id = %s ORDER BY version",
        (runs[0]["run_id"],)
    ).fetchall()
    assert len(artifacts) == 3
    types = {a["artifact_type"] for a in artifacts}
    assert "INITIAL_BRIEF" in types
    assert "APPROVED_BRIEF" in types
    assert "SPEC_BUNDLE" in types


def test_spec_pipeline_rejected_at_gate(conn, config, project_id):
    io = StubGateIO(approve=False)
    import pytest
    with pytest.raises(PipelineAborted):
        run_spec_pipeline(
            raw_idea="Build something",
            config=config,
            conn=conn,
            project_id=project_id,
            io=io,
        )
