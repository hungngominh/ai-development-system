"""Followup gap detection unit tests."""
from __future__ import annotations

import pytest

from ai_dev_system.intake.engine import FieldAnswer, new_state
from ai_dev_system.intake.followup import (
    Gap,
    _score_ambiguity,
    detect_gaps,
)
from ai_dev_system.intake.template import load_template


@pytest.fixture
def tpl():
    return load_template("generic_v1")


@pytest.fixture
def state(tpl):
    return new_state(tpl, "r1", "p1")


def _fill(state, **kv):
    """Convenience: set answers from kwargs with source=user."""
    for fid, value in kv.items():
        state.answers[fid] = FieldAnswer(value=value, source="user")


# ---------------------------------------------------------------------------
# Gap (de)serialization
# ---------------------------------------------------------------------------

def test_gap_roundtrips_via_dict():
    g = Gap(kind="critical_blank", message="x", target_field_id="scope_in")
    assert Gap.from_dict(g.to_dict()) == g


# ---------------------------------------------------------------------------
# Critical blank detection
# ---------------------------------------------------------------------------

def test_critical_blank_detected_for_skipped_criticals(tpl, state):
    # Mark every critical field skipped
    for fid in tpl.critical_field_ids:
        state.answers[fid] = FieldAnswer(value=None, source="skipped")
    # And answer all non-critical
    for fld in tpl.fields:
        if fld.id not in tpl.critical_field_ids and fld.id not in state.answers:
            state.answers[fld.id] = FieldAnswer(value="x", source="user")

    gaps = detect_gaps(state, tpl, llm=None)
    crit_gaps = [g for g in gaps if g.kind == "critical_blank"]
    assert {g.target_field_id for g in crit_gaps} == set(tpl.critical_field_ids)


def test_critical_blank_skipped_when_answered(tpl, state):
    _fill(state,
          problem_statement="real problem statement here",
          scope_in=["search"], scope_out=["mobile"],
          success_metric="80% adoption", primary_user="employees",
          deployment_target="AWS", compliance=["SOC2"],
          current_workaround="manual spreadsheet")
    gaps = detect_gaps(state, tpl, llm=None)
    crit = [g for g in gaps if g.kind == "critical_blank"]
    assert crit == []


# ---------------------------------------------------------------------------
# Consistency rules → gaps
# ---------------------------------------------------------------------------

def test_inconsistency_gap_emitted_for_residency_mismatch(tpl, state):
    _fill(state,
          problem_statement="x" * 20, current_workaround="x" * 20,
          scope_in=["a"], scope_out=["b"], success_metric="x" * 20,
          primary_user="x" * 20, deployment_target="AWS us-east-1",
          compliance=["VN-decree-13"],
          data_residency="VN-only")
    gaps = detect_gaps(state, tpl, llm=None)
    kinds = [(g.kind, g.rule_id) for g in gaps]
    assert ("inconsistency", "residency_vs_deploy") in kinds


def test_scope_mismatch_gap_uses_dedicated_kind(tpl, state):
    _fill(state,
          problem_statement="x" * 20, current_workaround="x" * 20,
          scope_in=["a", "b", "c", "d", "e", "f", "g", "h"],
          scope_out=["x"], success_metric="x" * 20,
          primary_user="x" * 20, deployment_target="AWS",
          compliance=["none"], deadline="2 tuần")
    gaps = detect_gaps(state, tpl, llm=None)
    matched = [g for g in gaps if g.rule_id == "scope_vs_deadline"]
    assert len(matched) == 1
    assert matched[0].kind == "scope_mismatch"


# ---------------------------------------------------------------------------
# Ambiguity (LLM-driven)
# ---------------------------------------------------------------------------

class StubAmbiguityLLM:
    def __init__(self, score: float = 0.7, hint: str = ""):
        self.score = score
        self.hint = hint
        self.calls = 0
    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        import json as _json
        return _json.dumps({"score": self.score, "hint": self.hint})


def test_ambiguity_skipped_when_llm_none(tpl, state):
    """Even very short text_long answers don't emit ambiguity gaps without an LLM."""
    _fill(state, problem_statement="x")
    gaps = detect_gaps(state, tpl, llm=None)
    assert not any(g.kind == "ambiguity" for g in gaps)


def test_ambiguity_emitted_when_score_below_threshold(tpl, state):
    _fill(state, problem_statement="The system is sometimes slow occasionally")
    llm = StubAmbiguityLLM(score=0.3, hint="cần thêm metric")
    gaps = detect_gaps(state, tpl, llm=llm, ambiguity_threshold=0.5)
    amb = [g for g in gaps if g.kind == "ambiguity"]
    assert any(g.target_field_id == "problem_statement" for g in amb)
    assert any("metric" in g.message or "metric" in (g.hint or "") for g in amb)


def test_ambiguity_not_emitted_when_score_high(tpl, state):
    _fill(state, problem_statement="Employees lose 30 min/day searching docs in Slack")
    llm = StubAmbiguityLLM(score=0.9)
    gaps = detect_gaps(state, tpl, llm=llm)
    amb = [g for g in gaps if g.kind == "ambiguity"
           and g.target_field_id == "problem_statement"]
    assert amb == []


def test_ambiguity_short_answer_flagged_without_llm_score(tpl, state):
    """<10 char text_long is flagged as ambiguity even though no real scoring."""
    _fill(state, problem_statement="short")
    llm = StubAmbiguityLLM(score=0.9)  # would normally pass
    gaps = detect_gaps(state, tpl, llm=llm)
    assert any(g.kind == "ambiguity" and g.target_field_id == "problem_statement"
               for g in gaps)
    # LLM wasn't called because the short-circuit fired before
    assert llm.calls == 0


def test_ambiguity_falls_back_on_llm_error(tpl, state):
    _fill(state, problem_statement="Some moderately long answer for testing fallback")

    class BrokenLLM:
        def complete(self, system, user):
            raise RuntimeError("api down")

    gaps = detect_gaps(state, tpl, llm=BrokenLLM())
    # Score defaults to 1.0 → no ambiguity gap emitted
    assert not any(g.kind == "ambiguity"
                   and g.target_field_id == "problem_statement" for g in gaps)


# ---------------------------------------------------------------------------
# detect_gaps ordering / completeness
# ---------------------------------------------------------------------------

def test_detect_gaps_returns_empty_for_complete_brief(tpl, state):
    # Fill all critical with non-trivial answers + valid combos
    _fill(state,
          problem_statement="Detailed problem with metrics and context",
          current_workaround="Manual spreadsheet shared on Drive",
          scope_in=["search", "browse"], scope_out=["mobile"],
          success_metric="80% adoption in 1 month",
          primary_user="internal employees, 200 ppl",
          deployment_target="on-prem k8s",
          compliance=["none"])
    gaps = detect_gaps(state, tpl, llm=None)
    assert gaps == []


def test_detect_gaps_orders_critical_first(tpl, state):
    # Leave one critical blank and create one inconsistency
    state.answers["problem_statement"] = FieldAnswer(value=None, source="skipped")
    _fill(state,
          current_workaround="x" * 20,
          scope_in=["a"], scope_out=["b"], success_metric="x" * 20,
          primary_user="x" * 20, deployment_target="AWS",
          compliance=["VN-decree-13"], data_residency="VN-only")
    gaps = detect_gaps(state, tpl, llm=None)
    # critical_blank should be first kind in list
    assert gaps[0].kind == "critical_blank"
    assert gaps[0].target_field_id == "problem_statement"
    assert any(g.kind == "inconsistency" for g in gaps)
