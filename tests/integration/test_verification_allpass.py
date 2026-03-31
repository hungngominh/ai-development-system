"""All-pass fast path: finalize_gate3 with empty decisions on an all-PASS report → COMPLETED."""
import json
import uuid
from pathlib import Path
import pytest
from ai_dev_system.gate.gate3_bridge import finalize_gate3


def test_allpass_empty_decisions_completes(conn, config, project_id, tmp_path):
    run_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO runs (run_id, project_id, status, title, current_artifacts, metadata)
        VALUES (%s, %s, 'PAUSED_AT_GATE_3', 'AllPass Test', '{}', '{}')
    """, (run_id, project_id))

    # All criteria PASS
    artifact_id = str(uuid.uuid4())
    artifact_dir = tmp_path / "allpass"
    artifact_dir.mkdir()
    report = {
        "run_id": run_id, "attempt": 1, "overall": "ALL_PASS",
        "generated_at": "2026-03-31T00:00:00+00:00",
        "criteria": [
            {"criterion_id": "AC-1", "criterion_text": "x", "verdict": "PASS",
             "confidence": 1.0, "evidence": [], "reasoning": "ok", "related_task_ids": []},
        ],
        "task_summary": {},
    }
    (artifact_dir / "verification_report.json").write_text(json.dumps(report))
    (artifact_dir / "_complete.marker").write_text("{}")
    conn.execute("""
        INSERT INTO artifacts (artifact_id, run_id, artifact_type, version, status, created_by,
                               input_artifact_ids, content_ref, content_checksum, content_size)
        VALUES (%s, %s, 'VERIFICATION_REPORT', 1, 'ACTIVE', 'system', '{}', %s, 'x', 0)
    """, (artifact_id, run_id, str(artifact_dir)))

    result = finalize_gate3(run_id, decisions=[], storage_root=config.storage_root, conn=conn)

    assert result.has_remediation is False
    assert result.aborted is False
    row = conn.execute("SELECT status FROM runs WHERE run_id = %s", (run_id,)).fetchone()
    assert row["status"] == "COMPLETED"
