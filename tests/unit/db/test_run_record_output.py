"""RunRepo.record_output stores a logical output name -> artifact_id under
current_artifacts.outputs (the name-addressed map used to resolve downstream
task inputs), preserving existing keys.
"""
from ai_dev_system.db.helpers import load_json, new_uuid
from ai_dev_system.db.repos.runs import RunRepo


def _make_run(conn, current='{"spec_bundle_id": "abc"}'):
    rid = new_uuid()
    conn.execute(
        "INSERT INTO runs (run_id, project_id, status, title, current_artifacts, metadata) "
        "VALUES (?, ?, 'RUNNING_EXECUTION', 't', ?, '{}')",
        (rid, new_uuid(), current),
    )
    return rid


def _outputs(conn, rid):
    row = conn.execute("SELECT current_artifacts FROM runs WHERE run_id=?", (rid,)).fetchone()
    return load_json(row["current_artifacts"], default={})


def test_record_output_merges_and_preserves(conn):
    repo = RunRepo(conn)
    rid = _make_run(conn)
    repo.record_output(rid, "spec_bundle", "A1")
    repo.record_output(rid, "design_doc", "A2")
    ca = _outputs(conn, rid)
    assert ca["outputs"] == {"spec_bundle": "A1", "design_doc": "A2"}
    assert ca["spec_bundle_id"] == "abc"  # pre-existing top-level key preserved


def test_record_output_overwrites_same_name(conn):
    repo = RunRepo(conn)
    rid = _make_run(conn)
    repo.record_output(rid, "x", "A1")
    repo.record_output(rid, "x", "A2")
    assert _outputs(conn, rid)["outputs"]["x"] == "A2"


def test_record_output_handles_names_with_dots_and_spaces(conn):
    repo = RunRepo(conn)
    rid = _make_run(conn)
    repo.record_output(rid, "solution_design.md", "A1")
    repo.record_output(rid, "backend source code", "A2")
    out = _outputs(conn, rid)["outputs"]
    assert out["solution_design.md"] == "A1"
    assert out["backend source code"] == "A2"
