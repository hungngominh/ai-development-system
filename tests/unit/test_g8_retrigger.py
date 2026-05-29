"""Unit tests for G8 brief-edit re-trigger (g8_retrigger.py)."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from ai_dev_system.debate.questions.models import Decision, Question
from ai_dev_system.gate.gate1_review.g8_retrigger import (
    _compute_affected_decisions,
    _load_retrigger_count,
    _save_retrigger_count,
    run_g8_retrigger,
)


def _make_decision(
    id: str,
    brief_field_refs: list[str] | None = None,
) -> Decision:
    return Decision(
        id=id,
        summary=f"Decision {id}",
        classification="REQUIRED",
        domain_hints=["backend"],
        blocks_what=["deployment"],
        has_safe_default=False,
        brief_field_refs=brief_field_refs or [],
    )


# ── _compute_affected_decisions ───────────────────────────────────────────────

class TestComputeAffectedDecisions:
    def test_returns_decisions_with_matching_refs(self):
        decisions = [
            _make_decision("d1", brief_field_refs=["scope_in", "deployment_target"]),
            _make_decision("d2", brief_field_refs=["primary_user"]),
            _make_decision("d3", brief_field_refs=["scope_out"]),
        ]
        affected = _compute_affected_decisions(decisions, edited_fields=["scope_in"])
        assert len(affected) == 1
        assert affected[0].id == "d1"

    def test_empty_edited_fields_returns_nothing(self):
        decisions = [_make_decision("d1", brief_field_refs=["scope_in"])]
        affected = _compute_affected_decisions(decisions, edited_fields=[])
        assert affected == []

    def test_legacy_inventory_no_refs_returns_all(self):
        decisions = [
            _make_decision("d1"),  # no brief_field_refs
            _make_decision("d2"),
        ]
        affected = _compute_affected_decisions(decisions, edited_fields=["scope_in"])
        assert len(affected) == 2  # worst-case full re-materialize

    def test_multiple_edited_fields_union(self):
        decisions = [
            _make_decision("d1", brief_field_refs=["scope_in"]),
            _make_decision("d2", brief_field_refs=["scope_out"]),
            _make_decision("d3", brief_field_refs=["primary_user"]),
        ]
        affected = _compute_affected_decisions(
            decisions, edited_fields=["scope_in", "scope_out"]
        )
        assert {d.id for d in affected} == {"d1", "d2"}


# ── retrigger count persistence ───────────────────────────────────────────────

class TestRetriggerCount:
    def test_default_count_is_zero(self, conn, seed_run):
        run_id = seed_run
        assert _load_retrigger_count(conn, run_id) == 0

    def test_save_and_reload(self, conn, seed_run):
        run_id = seed_run
        _save_retrigger_count(conn, run_id, 3)
        conn.commit()
        assert _load_retrigger_count(conn, run_id) == 3

    def test_save_preserves_other_metadata(self, conn, seed_run):
        run_id = seed_run
        conn.execute(
            "UPDATE runs SET metadata = ? WHERE run_id = ?",
            (json.dumps({"other_key": "other_value"}), run_id),
        )
        conn.commit()
        _save_retrigger_count(conn, run_id, 2)
        conn.commit()
        row = conn.execute(
            "SELECT metadata FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        meta = json.loads(row["metadata"])
        assert meta["g8_retrigger_count"] == 2
        assert meta["other_key"] == "other_value"


# ── run_g8_retrigger — noop paths ─────────────────────────────────────────────

class TestRunG8RetriggerNoop:
    def test_noop_when_no_decision_inventory(self, conn, seed_run):
        class _StubLLM:
            def complete(self, system, user):
                return "{}"

        result = run_g8_retrigger(
            run_id=seed_run,
            brief_edits=[{"field_name": "scope_in", "operation": "set", "value": "new"}],
            brief={"scope_in": ["new"]},
            conn=conn,
            llm_client=_StubLLM(),
        )
        assert result["noop"] is True
        assert result["new_questions"] == []

    def test_noop_when_no_affected_decisions(self, conn, seed_run, tmp_path):
        """Decision exists but edited field doesn't match its brief_field_refs."""
        _create_decision_inventory(conn, seed_run, tmp_path, [
            _make_decision("d1", brief_field_refs=["primary_user"]),
        ])

        class _StubLLM:
            def complete(self, system, user):
                return "{}"

        result = run_g8_retrigger(
            run_id=seed_run,
            brief_edits=[{"field_name": "scope_in", "operation": "set", "value": "new"}],
            brief={"scope_in": ["new"], "primary_user": "teams"},
            conn=conn,
            llm_client=_StubLLM(),
        )
        assert result["noop"] is True


# ── run_g8_retrigger — retrigger path ─────────────────────────────────────────

class TestRunG8RetriggerActive:
    def test_retrigger_appends_questions(self, conn, seed_run, tmp_path):
        _create_decision_inventory(conn, seed_run, tmp_path, [
            _make_decision("d1", brief_field_refs=["scope_in"]),
        ])
        _create_coverage_report(conn, seed_run, tmp_path)

        class _StubLLM:
            def complete(self, system, user):
                return json.dumps([{
                    "id": "Q99",
                    "text": "Which scope to prioritize?",
                    "domain": "backend",
                    "agent_a": "TechLead",
                    "agent_b": "ProductManager",
                }])

        result = run_g8_retrigger(
            run_id=seed_run,
            brief_edits=[{"field_name": "scope_in", "operation": "set", "value": "new scope"}],
            brief={"scope_in": ["new scope"], "problem_statement": "test"},
            conn=conn,
            llm_client=_StubLLM(),
        )

        assert result["noop"] is False
        assert result["retrigger_count"] == 1
        # New questions should have -r1 suffix
        assert all("-r1" in qid for qid in result["new_questions"])

    def test_retrigger_count_increments(self, conn, seed_run, tmp_path):
        _create_decision_inventory(conn, seed_run, tmp_path, [
            _make_decision("d1", brief_field_refs=["scope_in"]),
        ])
        _create_coverage_report(conn, seed_run, tmp_path)
        _save_retrigger_count(conn, seed_run, 2)
        conn.commit()

        class _StubLLM:
            def complete(self, system, user):
                return json.dumps([{
                    "id": "Qx",
                    "text": "A long detailed question about deployment architecture?",
                    "domain": "backend",
                    "agent_a": "TechLead",
                    "agent_b": "ProductManager",
                }])

        result = run_g8_retrigger(
            run_id=seed_run,
            brief_edits=[{"field_name": "scope_in", "operation": "set", "value": "v"}],
            brief={"scope_in": ["v"]},
            conn=conn,
            llm_client=_StubLLM(),
        )
        assert result["retrigger_count"] == 3
        assert all("-r3" in qid for qid in result["new_questions"])


# ── helpers ───────────────────────────────────────────────────────────────────

def _create_decision_inventory(conn, run_id: str, tmp_path: Path, decisions: list[Decision]):
    """Create DECISION_INVENTORY artifact with given decisions."""
    art_dir = tmp_path / "decision_inventory"
    art_dir.mkdir(exist_ok=True)

    import dataclasses
    with open(art_dir / "decisions.json", "w", encoding="utf-8") as f:
        json.dump([dataclasses.asdict(d) for d in decisions], f)

    artifact_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO artifacts (
            artifact_id, run_id, artifact_type, version, status, created_by,
            input_artifact_ids, content_ref, content_checksum, content_size
        ) VALUES (?, ?, 'DECISION_INVENTORY', 1, 'ACTIVE', 'system', '[]', ?, 'stub', 0)
    """, (artifact_id, run_id, str(art_dir)))
    conn.commit()


def _create_coverage_report(conn, run_id: str, tmp_path: Path):
    """Create minimal QUESTION_COVERAGE_REPORT artifact."""
    art_dir = tmp_path / "coverage_report"
    art_dir.mkdir(exist_ok=True)

    with open(art_dir / "coverage_report.json", "w", encoding="utf-8") as f:
        json.dump({"decisions": [], "questions": [], "retriggers": []}, f)

    artifact_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO artifacts (
            artifact_id, run_id, artifact_type, version, status, created_by,
            input_artifact_ids, content_ref, content_checksum, content_size
        ) VALUES (?, ?, 'QUESTION_COVERAGE_REPORT', 1, 'ACTIVE', 'system', '[]', ?, 'stub', 0)
    """, (artifact_id, run_id, str(art_dir)))
    conn.commit()
