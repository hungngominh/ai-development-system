import json
import os
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch, call
from ai_dev_system.verification.pipeline import run_verification, run_phase_v_pipeline
from ai_dev_system.verification.judge import StubVerificationLLMClient
from ai_dev_system.verification.report import VerificationReport


def _make_spec_artifact(tmp_path: Path, criteria_text: str) -> tuple[str, MagicMock]:
    """Write acceptance-criteria.md and return (artifact_id, mock_conn_that_finds_it)."""
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    (spec_dir / "acceptance-criteria.md").write_text(criteria_text, encoding="utf-8")

    artifact_id = str(uuid.uuid4())
    conn = MagicMock()

    def execute_side_effect(query, params=None):
        cursor = MagicMock()
        q = query.strip().lower()
        if "from artifacts" in q and "artifact_id" in q:
            cursor.fetchone.return_value = {"content_ref": str(spec_dir)}
        elif "from task_runs" in q:
            cursor.fetchall.return_value = []
        elif "count" in q and "verification_report" in q:
            cursor.fetchone.return_value = {"count": 0}
        elif "from runs" in q:
            cursor.fetchone.return_value = {"status": "RUNNING_PHASE_V"}
        else:
            cursor.fetchone.return_value = None
            cursor.fetchall.return_value = []
        return cursor

    conn.execute.side_effect = execute_side_effect
    return artifact_id, conn


def test_run_verification_returns_report(tmp_path):
    criteria_text = "# Acceptance Criteria\n\nAC-1: User can create tasks\n"
    spec_id, conn = _make_spec_artifact(tmp_path, criteria_text)
    stub_llm = StubVerificationLLMClient(verdicts={"AC-1": ("PASS", 0.95, "confirmed")})

    config = MagicMock()
    config.storage_root = str(tmp_path / "storage")
    os.makedirs(config.storage_root, exist_ok=True)

    with patch("ai_dev_system.verification.pipeline.promote_output") as mock_promote, \
         patch("ai_dev_system.verification.pipeline.task_run_repo_create") as mock_create:
        mock_create.return_value = {
            "task_run_id": str(uuid.uuid4()),
            "run_id": "run-1",
            "task_id": "verification",
            "attempt_number": 1,
        }
        mock_promote.return_value = str(uuid.uuid4())

        report = run_verification("run-1", spec_id, config, conn, stub_llm)

    assert isinstance(report, VerificationReport)
    assert report.run_id == "run-1"
    assert report.attempt == 1  # count was 0 → attempt 1
    assert len(report.criteria) == 1
    assert report.criteria[0].criterion_id == "AC-1"
    assert report.criteria[0].verdict == "PASS"
    assert report.overall == "ALL_PASS"


def test_run_verification_overall_has_fail(tmp_path):
    criteria_text = "# Acceptance Criteria\n\nAC-1: Coverage ≥ 80%\n"
    spec_id, conn = _make_spec_artifact(tmp_path, criteria_text)
    stub_llm = StubVerificationLLMClient(verdicts={"AC-1": ("FAIL", 0.99, "only 71%")})

    config = MagicMock()
    config.storage_root = str(tmp_path / "storage")
    os.makedirs(config.storage_root, exist_ok=True)

    with patch("ai_dev_system.verification.pipeline.promote_output") as mock_promote, \
         patch("ai_dev_system.verification.pipeline.task_run_repo_create") as mock_create:
        mock_create.return_value = {
            "task_run_id": str(uuid.uuid4()),
            "run_id": "run-1",
            "task_id": "verification",
            "attempt_number": 1,
        }
        mock_promote.return_value = str(uuid.uuid4())

        report = run_verification("run-1", spec_id, config, conn, stub_llm)

    assert report.overall == "HAS_FAIL"
    assert report.criteria[0].verdict == "FAIL"


def test_run_phase_v_pipeline_transitions_to_paused(tmp_path):
    criteria_text = "# Acceptance Criteria\n\nAC-1: All good\n"
    spec_id, conn = _make_spec_artifact(tmp_path, criteria_text)
    stub_llm = StubVerificationLLMClient(verdicts={})

    config = MagicMock()
    config.storage_root = str(tmp_path / "storage")
    os.makedirs(config.storage_root, exist_ok=True)

    status_updates = []
    original_side_effect = conn.execute.side_effect

    def tracking_execute(query, params=None):
        if "update runs set status" in query.lower():
            status_updates.append(params[0] if params else None)
            cursor = MagicMock()
            return cursor
        return original_side_effect(query, params)

    conn.execute.side_effect = tracking_execute

    with patch("ai_dev_system.verification.pipeline.promote_output") as mock_promote, \
         patch("ai_dev_system.verification.pipeline.task_run_repo_create") as mock_create:
        mock_create.return_value = {
            "task_run_id": str(uuid.uuid4()),
            "run_id": "run-1",
            "task_id": "verification",
            "attempt_number": 1,
        }
        mock_promote.return_value = str(uuid.uuid4())

        run_phase_v_pipeline("run-1", spec_id, config, conn, stub_llm)

    assert "PAUSED_AT_GATE_3" in status_updates
