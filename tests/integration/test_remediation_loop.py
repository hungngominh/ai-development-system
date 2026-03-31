"""
Integration: attempt counter increments correctly; attempt >= 3 -> PAUSED_AT_GATE_3B.
"""
import json
import uuid
from pathlib import Path
import pytest
from ai_dev_system.gate.gate3_bridge import finalize_gate3, Gate3Decision


def _insert_vr_artifact(conn, run_id: str, tmp_path: Path, attempt: int, status: str) -> str:
    artifact_id = str(uuid.uuid4())
    d = tmp_path / f"vr_{attempt}"
    d.mkdir()
    report = {
        "run_id": run_id, "attempt": attempt, "overall": "HAS_FAIL",
        "generated_at": "2026-03-31T00:00:00+00:00",
        "criteria": [
            {"criterion_id": "AC-1", "criterion_text": "x", "verdict": "FAIL",
             "confidence": 0.9, "evidence": [], "reasoning": "x", "related_task_ids": []}
        ],
        "task_summary": {},
    }
    (d / "verification_report.json").write_text(json.dumps(report))
    (d / "_complete.marker").write_text("{}")
    conn.execute("""
        INSERT INTO artifacts (artifact_id, run_id, artifact_type, version, status, created_by,
                               input_artifact_ids, content_ref, content_checksum, content_size)
        VALUES (%s, %s, 'VERIFICATION_REPORT', %s, %s, 'system', '{}', %s, 'chk', 0)
    """, (artifact_id, run_id, attempt, status, str(d)))
    return artifact_id


def test_attempt_counter_increments(conn, config, project_id, tmp_path):
    run_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO runs (run_id, project_id, status, title, current_artifacts, metadata)
        VALUES (%s, %s, 'PAUSED_AT_GATE_3', 'Loop Test', '{}', '{}')
    """, (run_id, project_id))

    # Attempt 1: 1 VERIFICATION_REPORT -> attempt count = 1 -> <3 -> remediation
    _insert_vr_artifact(conn, run_id, tmp_path, attempt=1, status="ACTIVE")

    result = finalize_gate3(run_id, decisions=[], storage_root=config.storage_root, conn=conn)

    assert result.has_remediation is True
    row = conn.execute("SELECT status FROM runs WHERE run_id = %s", (run_id,)).fetchone()
    assert row["status"] == "RUNNING_PHASE_V"


def test_attempt_3_triggers_paused_at_gate3b(conn, config, project_id, tmp_path):
    run_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO runs (run_id, project_id, status, title, current_artifacts, metadata)
        VALUES (%s, %s, 'PAUSED_AT_GATE_3', 'Loop Test 3x', '{}', '{}')
    """, (run_id, project_id))

    # 3 VERIFICATION_REPORT artifacts (attempts 1, 2, 3)
    _insert_vr_artifact(conn, run_id, tmp_path, 1, "SUPERSEDED")
    _insert_vr_artifact(conn, run_id, tmp_path, 2, "SUPERSEDED")
    _insert_vr_artifact(conn, run_id, tmp_path, 3, "ACTIVE")

    result = finalize_gate3(run_id, decisions=[], storage_root=config.storage_root, conn=conn)

    assert result.has_remediation is False
    assert result.aborted is False
    row = conn.execute("SELECT status FROM runs WHERE run_id = %s", (run_id,)).fetchone()
    assert row["status"] == "PAUSED_AT_GATE_3B"


def test_attempt_2_still_triggers_remediation(conn, config, project_id, tmp_path):
    run_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO runs (run_id, project_id, status, title, current_artifacts, metadata)
        VALUES (%s, %s, 'PAUSED_AT_GATE_3', 'Loop Test 2x', '{}', '{}')
    """, (run_id, project_id))

    # 2 VERIFICATION_REPORT artifacts -> attempt count = 2 -> still < 3
    _insert_vr_artifact(conn, run_id, tmp_path, 1, "SUPERSEDED")
    _insert_vr_artifact(conn, run_id, tmp_path, 2, "ACTIVE")

    result = finalize_gate3(run_id, decisions=[], storage_root=config.storage_root, conn=conn)

    assert result.has_remediation is True
    row = conn.execute("SELECT status FROM runs WHERE run_id = %s", (run_id,)).fetchone()
    assert row["status"] == "RUNNING_PHASE_V"
