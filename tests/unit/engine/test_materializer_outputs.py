"""Materializer wiring for name-addressed task outputs:
- promoted_outputs derived from a task's expected_outputs
- required_inputs resolved via current_artifacts.outputs (then fuzzy, then lenient)
"""
import warnings

from ai_dev_system.db.helpers import dump_json, new_uuid
from ai_dev_system.engine.materializer import _build_promoted_outputs, _resolve_artifact_paths


def test_build_promoted_outputs_from_expected():
    pos = _build_promoted_outputs({"expected_outputs": ["spec_bundle", "design_doc"]})
    assert [p["name"] for p in pos] == ["spec_bundle", "design_doc"]
    assert all(p["artifact_type"] == "EXECUTION_LOG" for p in pos)


def test_build_promoted_outputs_empty():
    assert _build_promoted_outputs({"expected_outputs": []}) == []
    assert _build_promoted_outputs({}) == []


def _seed_run_with_output(conn, output_name):
    rid = new_uuid()
    aid = new_uuid()
    conn.execute(
        "INSERT INTO runs (run_id, project_id, status, title, current_artifacts, metadata) "
        "VALUES (?, ?, 'RUNNING_EXECUTION', 't', ?, '{}')",
        (rid, new_uuid(), dump_json({"outputs": {output_name: aid}})),
    )
    conn.execute(
        "INSERT INTO artifacts (artifact_id, run_id, artifact_type, version, status, created_by, "
        "input_artifact_ids, content_ref, content_checksum, content_size) "
        "VALUES (?, ?, 'EXECUTION_LOG', 1, 'ACTIVE', 'system', '[]', ?, 'x', 0)",
        (aid, rid, "/tmp/out/design"),
    )
    return rid, aid


def test_resolve_uses_outputs_map(conn):
    rid, aid = _seed_run_with_output(conn, "design_doc")
    ctx = _resolve_artifact_paths(conn, rid, {"required_inputs": ["design_doc"]})
    entry = ctx["required_inputs"][0]
    assert entry["artifact_id"] == aid
    assert entry["path"] == "/tmp/out/design"


def test_resolve_outputs_map_case_insensitive(conn):
    rid, aid = _seed_run_with_output(conn, "Design_Doc")
    ctx = _resolve_artifact_paths(conn, rid, {"required_inputs": ["design_doc"]})
    assert ctx["required_inputs"][0]["artifact_id"] == aid


def test_resolve_lenient_on_unresolved(conn):
    rid, _ = _seed_run_with_output(conn, "design_doc")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ctx = _resolve_artifact_paths(conn, rid, {"required_inputs": ["nonexistent_thing"]})
    entry = ctx["required_inputs"][0]
    assert entry["name"] == "nonexistent_thing"
    assert entry["path"] is None
    assert entry["artifact_id"] is None


def test_resolve_empty_inputs_passthrough(conn):
    rid, _ = _seed_run_with_output(conn, "design_doc")
    ctx = _resolve_artifact_paths(conn, rid, {"required_inputs": []})
    assert ctx["required_inputs"] == []
