"""Unit tests for gate.gate1_review.loader (G1).

Tests cover:
- load_gate1_context: v2 run with all artifacts
- load_gate1_context: legacy run (no intake_brief, no decisions)
- load_gate1_context: v2 run missing optional artifacts (fallback to None)
- load_gate1_context: missing run raises ValueError
- load_gate1_context: missing DEBATE_REPORT raises ValueError
- GateReviewContext.decision_by_id auto-populated from decisions list
- _extract_questions: correct Question objects from debate_report dict
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from ai_dev_system.db.connection import get_connection
from ai_dev_system.db.migrator import apply_schema
from ai_dev_system.gate.gate1_review.loader import (
    GateReviewContext,
    load_gate1_context,
)


# ---- fixtures ----


@pytest.fixture
def db():
    conn = get_connection("sqlite:///:memory:")
    apply_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def project_id():
    return uuid.uuid4().hex


def _make_run(db, project_id, *, pipeline_version=2, legacy=0, intake_brief_id=None, title=None):
    """Insert a minimal run row and return run_id."""
    run_id = uuid.uuid4().hex
    db.execute(
        """
        INSERT INTO runs (run_id, project_id, status, pipeline_version, legacy,
                          intake_brief_id, title, current_artifacts, metadata)
        VALUES (?, ?, 'PAUSED_AT_GATE_1', ?, ?, ?, ?,
                '{"debate_report_id":null}', '{}')
        """,
        (run_id, project_id, pipeline_version, legacy, intake_brief_id, title or run_id),
    )
    return run_id


def _insert_artifact(db, run_id, artifact_type, content_ref, *, status="ACTIVE"):
    artifact_id = uuid.uuid4().hex
    db.execute(
        """
        INSERT INTO artifacts (artifact_id, run_id, artifact_type, version, status,
                               created_by, input_artifact_ids, content_ref,
                               content_checksum, content_size)
        VALUES (?, ?, ?, 1, ?, 'system', '[]', ?, 'abc', 0)
        """,
        (artifact_id, run_id, artifact_type, status, content_ref),
    )
    return artifact_id


def _set_debate_report_id(db, run_id, artifact_id):
    db.execute(
        """
        UPDATE runs
        SET current_artifacts = json_set(current_artifacts, '$.debate_report_id', ?)
        WHERE run_id = ?
        """,
        (artifact_id, run_id),
    )


def _make_debate_report(tmp_path: Path, run_id: str, *, results=None) -> Path:
    report_dir = tmp_path / f"debate_{run_id}"
    report_dir.mkdir(exist_ok=True)
    report = {
        "run_id": run_id,
        "brief": {"raw_idea": "Build a thing"},
        "generated_at": "2026-05-23T00:00:00Z",
        "results": results or [],
    }
    (report_dir / "debate_report.json").write_text(json.dumps(report), encoding="utf-8")
    return report_dir


def _make_intake_brief(tmp_path: Path, run_id: str) -> Path:
    brief_dir = tmp_path / f"brief_{run_id}"
    brief_dir.mkdir(exist_ok=True)
    brief = {
        "brief_version": 2,
        "problem_statement": "Teams need a thing",
        "scope_in": ["the thing"],
        "scope_out": [],
    }
    (brief_dir / "brief.json").write_text(json.dumps(brief), encoding="utf-8")
    return brief_dir


def _make_decision_inventory(tmp_path: Path, run_id: str, decisions=None) -> Path:
    inv_dir = tmp_path / f"decisions_{run_id}"
    inv_dir.mkdir(exist_ok=True)
    data = decisions or [
        {"id": "D1", "summary": "Auth choice", "classification": "REQUIRED",
         "domain_hints": ["security"], "blocks_what": [], "has_safe_default": False,
         "brief_field_refs": []},
    ]
    (inv_dir / "decisions.json").write_text(json.dumps(data), encoding="utf-8")
    return inv_dir


def _sample_qdr(q_id="Q1", status="RESOLVED"):
    return {
        "question": {
            "id": q_id,
            "text": "Use JWT?",
            "classification": "REQUIRED",
            "domain": "security",
            "agent_a": "SecuritySpecialist",
            "agent_b": "BackendArchitect",
            "source_decision_id": "D1",
        },
        "rounds": [
            {
                "round_number": 1,
                "agent_a_position": "Use JWT",
                "agent_b_position": "Use sessions",
                "moderator_summary": "JWT wins",
                "resolution_status": status,
                "confidence": 0.9,
                "caveat": None,
                "auto_resolution_reason": None,
            }
        ],
        "final": {
            "round_number": 1,
            "agent_a_position": "Use JWT",
            "agent_b_position": "Use sessions",
            "moderator_summary": "JWT wins",
            "resolution_status": status,
            "confidence": 0.9,
            "caveat": None,
            "auto_resolution_reason": None,
        },
    }


# ---- tests: load_gate1_context ----


def test_load_v2_run_all_artifacts(db, project_id, tmp_path):
    """v2 run with all 3 optional artifacts present → full context."""
    run_id = _make_run(db, project_id, pipeline_version=2, legacy=0, title="Test Run")

    report_dir = _make_debate_report(tmp_path, run_id, results=[_sample_qdr()])
    dr_id = _insert_artifact(db, run_id, "DEBATE_REPORT", str(report_dir))
    _set_debate_report_id(db, run_id, dr_id)

    brief_dir = _make_intake_brief(tmp_path, run_id)
    brief_id = _insert_artifact(db, run_id, "INTAKE_BRIEF", str(brief_dir))
    # Stamp intake_brief_id on run (v5 column)
    db.execute("UPDATE runs SET intake_brief_id = ? WHERE run_id = ?", (brief_id, run_id))

    inv_dir = _make_decision_inventory(tmp_path, run_id)
    _insert_artifact(db, run_id, "DECISION_INVENTORY", str(inv_dir))

    ctx = load_gate1_context(run_id, db)

    assert ctx.run_id == run_id
    assert ctx.project_name == "Test Run"
    assert ctx.is_legacy_brief is False
    assert ctx.brief.get("brief_version") == 2
    assert ctx.decisions is not None
    assert len(ctx.decisions) == 1
    assert ctx.decisions[0].id == "D1"
    assert ctx.questions is not None
    assert len(ctx.questions) == 1
    assert ctx.questions[0].id == "Q1"
    assert ctx.coverage_report is None  # not seeded


def test_load_legacy_run(db, project_id, tmp_path):
    """Legacy run (pipeline_version=1) → is_legacy_brief=True, decisions=None."""
    run_id = _make_run(db, project_id, pipeline_version=1, legacy=1)

    report_dir = _make_debate_report(tmp_path, run_id)
    dr_id = _insert_artifact(db, run_id, "DEBATE_REPORT", str(report_dir))
    _set_debate_report_id(db, run_id, dr_id)

    ctx = load_gate1_context(run_id, db)

    assert ctx.is_legacy_brief is True
    assert ctx.decisions is None
    assert ctx.coverage_report is None
    # brief falls back to debate_report.brief
    assert ctx.brief == {"raw_idea": "Build a thing"}


def test_load_v2_run_missing_optional_artifacts(db, project_id, tmp_path):
    """v2 run with no DECISION_INVENTORY or INTAKE_BRIEF → graceful None."""
    run_id = _make_run(db, project_id, pipeline_version=2, legacy=0)

    report_dir = _make_debate_report(tmp_path, run_id)
    dr_id = _insert_artifact(db, run_id, "DEBATE_REPORT", str(report_dir))
    _set_debate_report_id(db, run_id, dr_id)

    ctx = load_gate1_context(run_id, db)

    assert ctx.is_legacy_brief is True  # no INTAKE_BRIEF → falls back to debate_report.brief
    assert ctx.decisions is None
    assert ctx.coverage_report is None


def test_load_raises_on_missing_run(db):
    with pytest.raises(ValueError, match="not found"):
        load_gate1_context("nonexistent-run-id", db)


def test_load_raises_on_missing_debate_report(db, project_id, tmp_path):
    run_id = _make_run(db, project_id)
    # No DEBATE_REPORT artifact seeded
    with pytest.raises(ValueError, match="DEBATE_REPORT"):
        load_gate1_context(run_id, db)


def test_decision_by_id_populated_automatically(db, project_id, tmp_path):
    """GateReviewContext.decision_by_id is built from decisions list in __post_init__."""
    run_id = _make_run(db, project_id, pipeline_version=2, legacy=0)

    report_dir = _make_debate_report(tmp_path, run_id)
    dr_id = _insert_artifact(db, run_id, "DEBATE_REPORT", str(report_dir))
    _set_debate_report_id(db, run_id, dr_id)

    inv_dir = _make_decision_inventory(tmp_path, run_id, decisions=[
        {"id": "D1", "summary": "Auth", "classification": "REQUIRED",
         "domain_hints": [], "blocks_what": ["AUTH"], "has_safe_default": False,
         "brief_field_refs": []},
        {"id": "D2", "summary": "DB", "classification": "STRATEGIC",
         "domain_hints": [], "blocks_what": [], "has_safe_default": True,
         "brief_field_refs": []},
    ])
    _insert_artifact(db, run_id, "DECISION_INVENTORY", str(inv_dir))

    ctx = load_gate1_context(run_id, db)

    assert "D1" in ctx.decision_by_id
    assert "D2" in ctx.decision_by_id
    assert ctx.decision_by_id["D1"].blocks_what == ["AUTH"]
    assert ctx.decision_by_id["D2"].has_safe_default is True


def test_extract_questions_preserves_source_decision_id(db, project_id, tmp_path):
    """Questions with source_decision_id are correctly reconstructed."""
    run_id = _make_run(db, project_id, pipeline_version=2, legacy=0)
    results = [_sample_qdr("Q1", "RESOLVED")]
    results[0]["question"]["source_decision_id"] = "D1"

    report_dir = _make_debate_report(tmp_path, run_id, results=results)
    dr_id = _insert_artifact(db, run_id, "DEBATE_REPORT", str(report_dir))
    _set_debate_report_id(db, run_id, dr_id)

    ctx = load_gate1_context(run_id, db)

    assert ctx.questions[0].source_decision_id == "D1"


def test_project_name_falls_back_to_run_id(db, project_id, tmp_path):
    """When runs.title is NULL, project_name == run_id."""
    run_id = _make_run(db, project_id, pipeline_version=2, legacy=0, title=None)
    # Override title to be NULL
    db.execute("UPDATE runs SET title = NULL WHERE run_id = ?", (run_id,))

    report_dir = _make_debate_report(tmp_path, run_id)
    dr_id = _insert_artifact(db, run_id, "DEBATE_REPORT", str(report_dir))
    _set_debate_report_id(db, run_id, dr_id)

    ctx = load_gate1_context(run_id, db)
    assert ctx.project_name == run_id
