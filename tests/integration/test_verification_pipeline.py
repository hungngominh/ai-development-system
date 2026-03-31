"""
Integration test: run_phase_v_pipeline() end-to-end with a real DB.
Requires DATABASE_URL + v4-verification.sql applied.
"""
import json
import uuid
from pathlib import Path
import pytest
from ai_dev_system.verification.pipeline import run_phase_v_pipeline
from ai_dev_system.verification.judge import StubVerificationLLMClient


def _seed_run_phase_v(conn, project_id: str) -> str:
    run_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO runs (run_id, project_id, status, title, current_artifacts, metadata)
        VALUES (%s, %s, 'RUNNING_PHASE_V', 'Phase V Test', '{}', '{}')
    """, (run_id, project_id))
    return run_id


def _seed_spec_bundle(conn, run_id: str, tmp_path: Path, ac_content: str) -> str:
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    (spec_dir / "acceptance-criteria.md").write_text(ac_content, encoding="utf-8")
    (spec_dir / "_complete.marker").write_text("{}")

    artifact_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO artifacts (artifact_id, run_id, artifact_type, version, status, created_by,
                               input_artifact_ids, content_ref, content_checksum, content_size)
        VALUES (%s, %s, 'SPEC_BUNDLE', 1, 'ACTIVE', 'system', '{}', %s, 'spec-chk', 0)
    """, (artifact_id, run_id, str(spec_dir)))
    return artifact_id


def test_run_phase_v_pipeline_creates_artifact(conn, config, project_id, tmp_path):
    run_id = _seed_run_phase_v(conn, project_id)
    spec_id = _seed_spec_bundle(conn, run_id, tmp_path,
                                "# Acceptance Criteria\n\nAC-1: User can login\n")

    stub = StubVerificationLLMClient(verdicts={"AC-1": ("PASS", 0.98, "login works")})
    report = run_phase_v_pipeline(run_id, spec_id, config, conn, stub)

    # run.status should be PAUSED_AT_GATE_3
    row = conn.execute("SELECT status FROM runs WHERE run_id = %s", (run_id,)).fetchone()
    assert row["status"] == "PAUSED_AT_GATE_3"

    # VERIFICATION_REPORT artifact should exist
    art = conn.execute(
        "SELECT content_ref FROM artifacts WHERE run_id = %s AND artifact_type = 'VERIFICATION_REPORT' AND status = 'ACTIVE'",
        (run_id,),
    ).fetchone()
    assert art is not None

    # Report file should be readable and correct
    report_file = Path(art["content_ref"]) / "verification_report.json"
    assert report_file.exists()
    data = json.loads(report_file.read_text())
    assert data["run_id"] == run_id
    assert data["attempt"] == 1
    assert data["overall"] == "ALL_PASS"
    assert data["criteria"][0]["criterion_id"] == "AC-1"


def test_run_phase_v_pipeline_has_fail(conn, config, project_id, tmp_path):
    run_id = _seed_run_phase_v(conn, project_id)
    spec_id = _seed_spec_bundle(conn, run_id, tmp_path,
                                "# Acceptance Criteria\n\nAC-1: Coverage >= 80%\n")

    stub = StubVerificationLLMClient(verdicts={"AC-1": ("FAIL", 0.99, "only 71%")})
    report = run_phase_v_pipeline(run_id, spec_id, config, conn, stub)

    assert report.overall == "HAS_FAIL"
    row = conn.execute("SELECT status FROM runs WHERE run_id = %s", (run_id,)).fetchone()
    assert row["status"] == "PAUSED_AT_GATE_3"
