import json
import uuid
import pytest
from ai_dev_system.engine.materializer import materialize_task_runs, _build_context, ArtifactResolutionError, _resolve_artifact_paths


def test_materializer_creates_pending_task_runs(conn, seed_run, seed_graph_artifact, config):
    materialize_task_runs(conn, seed_run, seed_graph_artifact, config)
    rows = conn.execute(
        "SELECT task_id, status, retry_count FROM task_runs WHERE run_id = ? ORDER BY task_id",
        (seed_run,)
    ).fetchall()
    assert len(rows) == 2
    assert {r["task_id"] for r in rows} == {"TASK-PARSE", "TASK-DESIGN"}
    assert all(r["status"] == "PENDING" for r in rows)
    assert all(r["retry_count"] == 0 for r in rows)


def test_materializer_sets_run_status_running_execution(conn, seed_run, seed_graph_artifact, config):
    conn.execute("UPDATE runs SET status = 'RUNNING_PHASE_3' WHERE run_id = ?", (seed_run,))
    materialize_task_runs(conn, seed_run, seed_graph_artifact, config)
    status = conn.execute(
        "SELECT status FROM runs WHERE run_id = ?", (seed_run,)
    ).fetchone()[0]
    assert status == "RUNNING_EXECUTION"


def test_materializer_is_idempotent(conn, seed_run, seed_graph_artifact, config):
    materialize_task_runs(conn, seed_run, seed_graph_artifact, config)
    materialize_task_runs(conn, seed_run, seed_graph_artifact, config)
    count = conn.execute(
        "SELECT COUNT(*) FROM task_runs WHERE run_id = ?", (seed_run,)
    ).fetchone()[0]
    assert count == 2


def test_materializer_resolves_dependencies(conn, seed_run, seed_graph_artifact, config):
    materialize_task_runs(conn, seed_run, seed_graph_artifact, config)
    design = conn.execute(
        "SELECT resolved_dependencies FROM task_runs WHERE run_id = ? AND task_id = 'TASK-DESIGN'",
        (seed_run,)
    ).fetchone()
    assert "TASK-PARSE" in (design["resolved_dependencies"] or [])


def test_build_context_returns_snapshot():
    task = {
        "id": "TASK-PARSE", "phase": "parse_spec", "type": "design",
        "agent_type": "SpecAnalyst",
        "objective": "Parse all specs", "description": "Detailed desc",
        "done_definition": "All parsed", "verification_steps": ["step1"],
        "required_inputs": ["spec.md"], "expected_outputs": ["summary.json"],
    }
    ctx = _build_context(task)
    assert ctx["task_id"] == "TASK-PARSE"
    assert ctx["required_inputs"] == ["spec.md"]
    assert ctx["verification_steps"] == ["step1"]


def test_resolve_artifact_paths_lenient_on_missing(conn, seed_run):
    # Unresolvable inputs are passed through with path=None (warn, don't raise)
    # so a task still runs instead of failing the whole graph.
    import warnings as _warnings

    context = {
        "task_id": "TASK-IMPL",
        "required_inputs": ["some_nonexistent_artifact.md"],
    }
    with _warnings.catch_warnings():
        _warnings.simplefilter("ignore")
        result = _resolve_artifact_paths(conn, seed_run, context)
    assert result["required_inputs"] == [
        {"name": "some_nonexistent_artifact.md", "artifact_id": None, "path": None}
    ]


def test_resolve_artifact_paths_returns_empty_for_no_inputs(conn, seed_run):
    context = {"task_id": "TASK-PARSE", "required_inputs": []}
    result = _resolve_artifact_paths(conn, seed_run, context)
    assert result["required_inputs"] == []
