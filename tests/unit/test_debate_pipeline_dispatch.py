"""Dispatch unit tests for run_debate_pipeline.

Validates the spec phase1-migration §Dispatcher Pattern wiring:
- `use_question_pipeline_v2` routes question gen by flag.
- `use_debate_v2` routes the engine call by flag.
- Snapshot semantics: flag state captured once at entry, not re-read
  mid-run (encoded via the function-arg `flags` override).

These tests target the private `_question_path` / `_debate_path`
helpers directly so we can exercise the matrix without spinning up
SQLite + StubLLM full pipeline. Integration tests in
`tests/integration/test_debate_pipeline.py` cover the end-to-end
backward-compat (no flags) path.
"""

from dataclasses import dataclass

import pytest

from ai_dev_system.debate.questions.models import (
    CoverageReport,
    Decision,
    PipelineResult,
)
from ai_dev_system.debate.report import DebateReport, Question
from ai_dev_system.debate_pipeline import _debate_path, _question_path
from ai_dev_system.feature_flags import FeatureFlags


SAMPLE_BRIEF_V1 = {"raw_idea": "Build a thing"}
SAMPLE_BRIEF_V2 = {
    "brief_version": 2,
    "problem_statement": "Teams need a thing",
    "scope_in": ["the thing"],
    "scope_out": [],
}


def _all_flags_off() -> FeatureFlags:
    return FeatureFlags()


def _all_flags_on() -> FeatureFlags:
    # Linear order requires the chain: eval → intake → questions → debate.
    return FeatureFlags(
        eval_harness_enabled=True,
        use_intake_wizard=True,
        use_question_pipeline_v2=True,
        use_debate_v2=True,
    )


# ---- _question_path ----


def test_question_path_v1_when_flag_off(monkeypatch):
    """Flag off → calls legacy generate_questions, returns no decisions/digest."""
    calls = []

    def fake_legacy(brief, llm_client):
        calls.append(("legacy", brief))
        return [Question(id="Q1", text="?", classification="REQUIRED",
                         domain="security", agent_a="SecuritySpecialist",
                         agent_b="BackendArchitect")]

    monkeypatch.setattr(
        "ai_dev_system.debate_pipeline.generate_questions", fake_legacy
    )
    questions, decisions, digest = _question_path(
        _all_flags_off(), SAMPLE_BRIEF_V1, SAMPLE_BRIEF_V2, llm_client=object(),
    )

    assert calls == [("legacy", SAMPLE_BRIEF_V1)]
    assert len(questions) == 1
    assert decisions is None
    assert digest is None


def test_question_path_v2_when_flag_on_and_brief_v2_present(monkeypatch):
    """Flag on + brief_v2 → calls v2 pipeline with computed digest."""
    pipeline_calls = []
    sample_q = Question(id="Q1", text="?", classification="REQUIRED",
                        domain="security", agent_a="SecuritySpecialist",
                        agent_b="BackendArchitect")
    sample_d = Decision(id="D1", summary="x", classification="REQUIRED",
                        domain_hints=["security"])

    def fake_v2(brief, digest, llm_client):
        pipeline_calls.append((brief, digest))
        return PipelineResult(
            decisions=[sample_d],
            questions_final=[sample_q],
            coverage_report=CoverageReport(
                checks=[], covered_decision_ids=[], missing_decision_ids=[],
                domain_distribution={}, classification_distribution={},
                total_questions=1,
            ),
            critic_iterations=0,
        )

    monkeypatch.setattr(
        "ai_dev_system.debate_pipeline.run_question_pipeline_v2", fake_v2
    )
    questions, decisions, digest = _question_path(
        _all_flags_on(), SAMPLE_BRIEF_V1, SAMPLE_BRIEF_V2, llm_client=object(),
    )

    assert len(pipeline_calls) == 1
    received_brief, received_digest = pipeline_calls[0]
    assert received_brief is SAMPLE_BRIEF_V2  # not the v1 brief
    assert isinstance(received_digest, str) and received_digest  # non-empty
    assert digest == received_digest
    assert questions == [sample_q]
    assert decisions == [sample_d]


def test_question_path_falls_back_to_v1_when_brief_v2_missing(monkeypatch):
    """Flag on but no brief_v2 → warn + degrade to v1, no decisions."""
    monkeypatch.setattr(
        "ai_dev_system.debate_pipeline.generate_questions",
        lambda brief, client: [Question(id="Q1", text="?", classification="REQUIRED",
                                        domain="security",
                                        agent_a="SecuritySpecialist",
                                        agent_b="BackendArchitect")],
    )
    # If the v2 path is reached unexpectedly, this assertion fires:
    def fail_if_called(*_a, **_kw):
        raise AssertionError("v2 pipeline should NOT run when brief_v2 is None")
    monkeypatch.setattr(
        "ai_dev_system.debate_pipeline.run_question_pipeline_v2", fail_if_called
    )

    with pytest.warns(UserWarning, match="brief_v2"):
        questions, decisions, digest = _question_path(
            _all_flags_on(), SAMPLE_BRIEF_V1, None, llm_client=object(),
        )

    assert len(questions) == 1
    assert decisions is None
    assert digest is None


# ---- _debate_path ----


def _stub_report(run_id="r") -> DebateReport:
    return DebateReport(run_id=run_id, brief={}, results=[], generated_at="t")


def test_debate_path_v1_when_flag_off(monkeypatch):
    """Flag off → engine called with v1 signature (no kwargs beyond defaults)."""
    captured: dict = {}

    def fake_engine(questions, llm_client, *, run_id, brief, **kwargs):
        captured["questions"] = questions
        captured["brief"] = brief
        captured["kwargs"] = kwargs
        return _stub_report(run_id)

    monkeypatch.setattr("ai_dev_system.debate_pipeline.run_debate", fake_engine)

    report = _debate_path(
        _all_flags_off(), questions=[], llm_client=object(),
        run_id="r", brief={"x": 1}, decisions=None, digest=None,
    )

    assert isinstance(report, DebateReport)
    # v1: no enrichment kwargs supplied
    assert captured["kwargs"] == {}


def test_debate_path_v2_when_flag_on_wires_full_enrichment(monkeypatch):
    """Flag on → engine receives config, registry, brief_digest, decisions."""
    captured: dict = {}

    def fake_engine(questions, llm_client, *, run_id, brief, **kwargs):
        captured["kwargs"] = kwargs
        return _stub_report(run_id)

    monkeypatch.setattr("ai_dev_system.debate_pipeline.run_debate", fake_engine)

    decisions = [Decision(id="D1", summary="s", classification="REQUIRED",
                          domain_hints=["security"])]
    report = _debate_path(
        _all_flags_on(), questions=[], llm_client=object(),
        run_id="r", brief={}, decisions=decisions, digest="DIGEST",
    )

    assert isinstance(report, DebateReport)
    kwargs = captured["kwargs"]
    assert "config" in kwargs and kwargs["config"] is not None
    assert "registry" in kwargs and kwargs["registry"] is not None
    assert kwargs["brief_digest"] == "DIGEST"
    assert kwargs["decisions"] == decisions
    # registry was loaded from the real .md files → all 12 agents present
    assert len(kwargs["registry"]) == 12


# ---- snapshot semantics ----


def test_flag_snapshot_overrides_env(monkeypatch):
    """Passing an explicit FeatureFlags must win over env vars — proves the
    dispatcher reads the snapshot, not the live env, mid-run."""
    monkeypatch.setenv("FF_USE_DEBATE_V2", "true")
    monkeypatch.setenv("FF_USE_QUESTION_PIPELINE_V2", "true")
    monkeypatch.setenv("FF_USE_INTAKE_WIZARD", "true")
    monkeypatch.setenv("FF_EVAL_HARNESS_ENABLED", "true")
    # Caller passes an explicit all-off snapshot.
    forced_off = _all_flags_off()

    monkeypatch.setattr(
        "ai_dev_system.debate_pipeline.generate_questions",
        lambda brief, client: [],
    )

    def fail_if_called(*a, **kw):
        raise AssertionError("v2 pipeline should not run when caller forces flags off")

    monkeypatch.setattr(
        "ai_dev_system.debate_pipeline.run_question_pipeline_v2", fail_if_called
    )
    # Should NOT call v2 even though env says yes.
    _question_path(forced_off, SAMPLE_BRIEF_V1, SAMPLE_BRIEF_V2, llm_client=object())
