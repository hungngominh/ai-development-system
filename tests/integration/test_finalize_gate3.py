"""
Integration tests for finalize_gate3().
These tests require DATABASE_URL and a live DB with v4-verification.sql applied.
"""
import json
import uuid
from pathlib import Path
import pytest
from ai_dev_system.gate.gate3_bridge import finalize_gate3, Gate3Decision, Gate3Result
from ai_dev_system.db.repos.runs import RunRepo


# ─── Helpers ────────────────────────────────────────────────────────────────

def _seed_run_at_phase_v(conn, project_id: str) -> str:
    """Insert a run in PAUSED_AT_GATE_3 status — the state finalize_gate3 is called from."""
    run_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO runs (run_id, project_id, status, title, current_artifacts, metadata)
        VALUES (%s, %s, 'PAUSED_AT_GATE_3', 'Gate3 Test Run', '{}', '{}')
    """, (run_id, project_id))
    return run_id


def _seed_verification_report(conn, run_id: str, tmp_path: Path, criteria_verdicts: dict) -> str:
    """Insert a VERIFICATION_REPORT artifact with given verdicts. Returns artifact_id."""
    artifact_id = str(uuid.uuid4())
    artifact_dir = tmp_path / f"vr_{artifact_id[:8]}"
    artifact_dir.mkdir()

    criteria = [
        {
            "criterion_id": cid,
            "criterion_text": f"Text for {cid}",
            "verdict": verdict,
            "confidence": 0.9,
            "evidence": [],
            "reasoning": "test",
            "related_task_ids": [],
        }
        for cid, verdict in criteria_verdicts.items()
    ]
    report = {
        "run_id": run_id,
        "attempt": 1,
        "overall": "ALL_PASS" if all(v == "PASS" for v in criteria_verdicts.values()) else "HAS_FAIL",
        "generated_at": "2026-03-31T00:00:00+00:00",
        "criteria": criteria,
        "task_summary": {},
    }
    (artifact_dir / "verification_report.json").write_text(json.dumps(report))
    (artifact_dir / "_complete.marker").write_text("{}")

    conn.execute("""
        INSERT INTO artifacts (
            artifact_id, run_id, artifact_type, version, status, created_by,
            input_artifact_ids, content_ref, content_checksum, content_size
        ) VALUES (%s, %s, 'VERIFICATION_REPORT', 1, 'ACTIVE', 'system',
                  '{}', %s, 'test-checksum', 0)
    """, (artifact_id, run_id, str(artifact_dir)))
    return artifact_id


# ─── Tests ──────────────────────────────────────────────────────────────────

def test_finalize_gate3_all_pass_completes_run(conn, config, project_id, tmp_path):
    run_id = _seed_run_at_phase_v(conn, project_id)
    _seed_verification_report(conn, run_id, tmp_path, {"AC-1": "PASS", "AC-2": "PASS"})

    result = finalize_gate3(run_id, decisions=[], storage_root=config.storage_root, conn=conn)

    assert result.run_id == run_id
    assert result.has_remediation is False
    assert result.aborted is False

    row = conn.execute("SELECT status FROM runs WHERE run_id = %s", (run_id,)).fetchone()
    assert row["status"] == "COMPLETED"


def test_finalize_gate3_abort_sets_aborted(conn, config, project_id, tmp_path):
    run_id = _seed_run_at_phase_v(conn, project_id)
    _seed_verification_report(conn, run_id, tmp_path, {"AC-1": "FAIL"})

    result = finalize_gate3(
        run_id,
        decisions=[Gate3Decision(criterion_id="AC-1", action="ABORT")],
        storage_root=config.storage_root,
        conn=conn,
    )

    assert result.aborted is True
    row = conn.execute("SELECT status FROM runs WHERE run_id = %s", (run_id,)).fetchone()
    assert row["status"] == "ABORTED"


def test_finalize_gate3_all_skipped_completes(conn, config, project_id, tmp_path):
    run_id = _seed_run_at_phase_v(conn, project_id)
    _seed_verification_report(conn, run_id, tmp_path, {"AC-1": "FAIL"})

    result = finalize_gate3(
        run_id,
        decisions=[Gate3Decision(criterion_id="AC-1", action="SKIP")],
        storage_root=config.storage_root,
        conn=conn,
    )

    assert result.has_remediation is False
    assert result.aborted is False
    row = conn.execute("SELECT status FROM runs WHERE run_id = %s", (run_id,)).fetchone()
    assert row["status"] == "COMPLETED"


def test_finalize_gate3_fail_triggers_remediation(conn, config, project_id, tmp_path):
    run_id = _seed_run_at_phase_v(conn, project_id)
    _seed_verification_report(conn, run_id, tmp_path, {"AC-1": "PASS", "AC-2": "FAIL"})

    # No decisions for AC-2 → it stays FAIL → should generate remediation
    result = finalize_gate3(run_id, decisions=[], storage_root=config.storage_root, conn=conn)

    assert result.has_remediation is True
    assert result.remediation_graph is not None
    row = conn.execute("SELECT status FROM runs WHERE run_id = %s", (run_id,)).fetchone()
    assert row["status"] == "RUNNING_PHASE_V"


def test_finalize_gate3_attempt3_pauses_at_gate3b(conn, config, project_id, tmp_path):
    run_id = _seed_run_at_phase_v(conn, project_id)

    # Seed 3 VERIFICATION_REPORT artifacts to simulate 3 attempts already done
    for i in range(1, 4):
        artifact_id = str(uuid.uuid4())
        artifact_dir = tmp_path / f"vr_{i}"
        artifact_dir.mkdir()
        (artifact_dir / "verification_report.json").write_text(json.dumps({
            "run_id": run_id, "attempt": i, "overall": "HAS_FAIL",
            "generated_at": "2026-03-31T00:00:00+00:00",
            "criteria": [{"criterion_id": "AC-1", "criterion_text": "x",
                           "verdict": "FAIL", "confidence": 0.9,
                           "evidence": [], "reasoning": "x", "related_task_ids": []}],
            "task_summary": {},
        }))
        (artifact_dir / "_complete.marker").write_text("{}")
        status = "ACTIVE" if i == 3 else "SUPERSEDED"
        conn.execute("""
            INSERT INTO artifacts (
                artifact_id, run_id, artifact_type, version, status, created_by,
                input_artifact_ids, content_ref, content_checksum, content_size
            ) VALUES (%s, %s, 'VERIFICATION_REPORT', %s, %s, 'system',
                      '{}', %s, 'chk', 0)
        """, (artifact_id, run_id, i, status, str(artifact_dir)))

    # attempt count = 3 → should escalate to PAUSED_AT_GATE_3B
    result = finalize_gate3(run_id, decisions=[], storage_root=config.storage_root, conn=conn)

    row = conn.execute("SELECT status FROM runs WHERE run_id = %s", (run_id,)).fetchone()
    assert row["status"] == "PAUSED_AT_GATE_3B"
    assert result.has_remediation is False
