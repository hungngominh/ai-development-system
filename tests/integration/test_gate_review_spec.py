"""Integration test for gate review-spec CLI command (SP10)."""
import json
import os
import uuid
from pathlib import Path

import pytest

from ai_dev_system.db.connection import get_connection
from ai_dev_system.db.helpers import dump_json
from ai_dev_system.spec.generators.base import SECTION_FILES
from ai_dev_system.spec.tracer import build_trace_map, write_trace_map


def _make_spec_bundle(tmp_path: Path, with_trace_map: bool = True) -> tuple[Path, dict]:
    """Create a minimal spec bundle directory with section files + optional trace map."""
    spec_dir = tmp_path / "spec_bundle"
    spec_dir.mkdir()

    for name, filename in SECTION_FILES.items():
        (spec_dir / filename).write_text(
            f"# {name.title()}\n\n[brief:problem_statement] Some content for {name}.\n",
            encoding="utf-8",
        )

    trace_map = None
    if with_trace_map:
        class _FakeDraft:
            degraded: bool = False

            def __init__(self, content):
                self.content = content

        drafts = {
            name: _FakeDraft(
                "Content [brief:problem_statement] and [decision:auth_choice].\n"
            )
            for name in SECTION_FILES
        }
        brief = {"problem_statement": "test", "scope_in": ["web app"], "brief_version": 2}
        trace_map_data = build_trace_map(drafts, brief, [], [])
        write_trace_map(trace_map_data, spec_dir)
        trace_map = trace_map_data

    return spec_dir, trace_map


def _insert_run_with_spec_bundle(conn, project_id: str, spec_dir: Path) -> tuple[str, str]:
    """Insert a run and SPEC_BUNDLE artifact. Returns (run_id, artifact_id)."""
    run_id = str(uuid.uuid4())
    artifact_id = str(uuid.uuid4())

    conn.execute("""
        INSERT INTO runs (run_id, project_id, status, title, current_artifacts, metadata)
        VALUES (?, ?, 'RUNNING_PHASE_1D', 'SP10 Test', ?, '{}')
    """, (run_id, project_id, dump_json({"spec_bundle_id": artifact_id})))

    conn.execute("""
        INSERT INTO artifacts (
            artifact_id, run_id, artifact_type, version, status, created_by,
            input_artifact_ids, content_ref, content_checksum, content_size
        ) VALUES (?, ?, 'SPEC_BUNDLE', 1, 'ACTIVE', 'system', '[]', ?, 'stub', 0)
    """, (artifact_id, run_id, str(spec_dir)))

    conn.commit()
    return run_id, artifact_id


def _invoke_review_spec(db_url: str, run_id: str, cmd: str) -> "Result":
    from typer.testing import CliRunner
    from ai_dev_system.cli.main import app

    runner = CliRunner(env={"DATABASE_URL": db_url, "STORAGE_ROOT": "/tmp/sp10-test"})
    return runner.invoke(app, [
        "gate", "review-spec",
        "--run-id", run_id,
        "--cmd", cmd,
        "--json",
    ])


@pytest.mark.integration
def test_review_spec_render_shows_sections(file_config, project_id, tmp_path):
    conn = get_connection(file_config.database_url)
    spec_dir, _ = _make_spec_bundle(tmp_path)
    run_id, _ = _insert_run_with_spec_bundle(conn, project_id, spec_dir)
    conn.close()

    result = _invoke_review_spec(file_config.database_url, run_id, "render")

    assert result.exit_code == 0, result.output
    data = json.loads(result.output.strip().splitlines()[-1])
    assert data["status"] == "ok"
    assert data["run_id"] == run_id
    assert "functional" in data["sections"]
    assert "proposal" in data["sections"]
    assert data["trace_map_path"] is not None
    assert "trace_map.json" in data["trace_map_path"]


@pytest.mark.integration
def test_review_spec_trace_map_shows_summary(file_config, project_id, tmp_path):
    conn = get_connection(file_config.database_url)
    spec_dir, _ = _make_spec_bundle(tmp_path, with_trace_map=True)
    run_id, _ = _insert_run_with_spec_bundle(conn, project_id, spec_dir)
    conn.close()

    result = _invoke_review_spec(file_config.database_url, run_id, "trace-map")

    assert result.exit_code == 0, result.output
    data = json.loads(result.output.strip().splitlines()[-1])
    assert data["status"] == "ok"
    assert data["trace_map_path"] is not None
    summary = data["summary"]
    assert "total_markers" in summary
    assert "referenced_brief_fields" in summary
    assert summary["total_markers"] >= 0


@pytest.mark.integration
def test_review_spec_no_trace_map_returns_null_path(file_config, project_id, tmp_path):
    conn = get_connection(file_config.database_url)
    spec_dir, _ = _make_spec_bundle(tmp_path, with_trace_map=False)
    run_id, _ = _insert_run_with_spec_bundle(conn, project_id, spec_dir)
    conn.close()

    result = _invoke_review_spec(file_config.database_url, run_id, "render")

    assert result.exit_code == 0, result.output
    data = json.loads(result.output.strip().splitlines()[-1])
    assert data["trace_map_path"] is None


@pytest.mark.integration
def test_review_spec_missing_run_returns_error(file_config, tmp_path):
    result = _invoke_review_spec(file_config.database_url, str(uuid.uuid4()), "render")
    assert result.exit_code != 0
