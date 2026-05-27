"""Unit tests for gate.gate1_review.sections (G2).

Tests cover:
- Section assignment for all 5 resolution statuses + auto-resolved
- ReviewItem construction from qdr dict
- decision_context and blocks_what populated from matched decision
- Raw moderator output populated only for PARSE_FAILED
- build_sections: all 4 sections always returned (even if empty)
- total_pending counts only forced + parse_failed
"""

from __future__ import annotations

from ai_dev_system.debate.questions.models import Decision
from ai_dev_system.gate.gate1_review.loader import GateReviewContext
from ai_dev_system.gate.gate1_review.sections import (
    ReviewItem,
    ReviewSection,
    _build_review_item,
    _classify_item,
    build_sections,
    total_pending,
)


# ---- helpers ----


def _decision(id="D1", summary="Auth choice", blocks=None, has_safe_default=False) -> Decision:
    return Decision(
        id=id,
        summary=summary,
        classification="REQUIRED",
        domain_hints=["security"],
        blocks_what=blocks or [],
        has_safe_default=has_safe_default,
    )


def _qdr(
    q_id="Q1",
    status="RESOLVED",
    *,
    auto_resolution_reason=None,
    source_decision_id=None,
    agent_a_pos="pos_a",
    agent_b_pos="pos_b",
    moderator_summary="summary",
    confidence=0.9,
    caveat=None,
) -> dict:
    q = {
        "id": q_id,
        "text": "Use JWT?",
        "classification": "REQUIRED",
        "domain": "security",
        "agent_a": "SecuritySpecialist",
        "agent_b": "BackendArchitect",
        "source_decision_id": source_decision_id,
    }
    final = {
        "round_number": 1,
        "agent_a_position": agent_a_pos,
        "agent_b_position": agent_b_pos,
        "moderator_summary": moderator_summary,
        "resolution_status": status,
        "confidence": confidence,
        "caveat": caveat,
        "auto_resolution_reason": auto_resolution_reason,
    }
    return {"question": q, "rounds": [final], "final": final}


def _stub_ctx(results=None, decisions=None) -> GateReviewContext:
    """Minimal GateReviewContext for sections tests."""
    report = {
        "run_id": "r1",
        "brief": {},
        "generated_at": "t",
        "results": results or [],
    }
    decision_by_id = {d.id: d for d in decisions} if decisions else {}
    ctx = GateReviewContext.__new__(GateReviewContext)
    ctx.run_id = "r1"
    ctx.project_name = "Test"
    ctx.brief = {}
    ctx.is_legacy_brief = True
    ctx.debate_report = report
    ctx.decisions = decisions
    ctx.questions = []
    ctx.coverage_report = None
    ctx.decision_by_id = decision_by_id
    return ctx


# ---- _classify_item tests ----


def test_classify_resolved_is_consensus():
    item = _build_review_item(_qdr(status="RESOLVED"), {})
    assert _classify_item(item) == "consensus"


def test_classify_resolved_with_caveat_is_consensus():
    item = _build_review_item(_qdr(status="RESOLVED_WITH_CAVEAT", caveat="check X"), {})
    assert _classify_item(item) == "consensus"


def test_classify_escalate_is_forced():
    item = _build_review_item(_qdr(status="ESCALATE_TO_HUMAN"), {})
    assert _classify_item(item) == "forced"


def test_classify_need_more_evidence_is_forced():
    item = _build_review_item(_qdr(status="NEED_MORE_EVIDENCE"), {})
    assert _classify_item(item) == "forced"


def test_classify_parse_failed_is_parse_failed():
    item = _build_review_item(_qdr(status="MODERATOR_PARSE_FAILED"), {})
    assert _classify_item(item) == "parse_failed"


def test_classify_auto_resolved_when_reason_set():
    """auto_resolution_reason non-null → auto_resolved, regardless of status."""
    item = _build_review_item(
        _qdr(status="RESOLVED", auto_resolution_reason="OPTIONAL — no debate required."),
        {},
    )
    assert _classify_item(item) == "auto_resolved"


# ---- _build_review_item tests ----


def test_build_item_populates_basic_fields():
    item = _build_review_item(
        _qdr("Q3", "ESCALATE_TO_HUMAN", agent_a_pos="JWT", agent_b_pos="sessions"),
        {},
    )
    assert item.question_id == "Q3"
    assert item.agent_a_position == "JWT"
    assert item.agent_b_position == "sessions"
    assert item.resolution_status == "ESCALATE_TO_HUMAN"
    assert item.raw_moderator_output is None


def test_build_item_decision_context_from_matched_decision():
    d = _decision("D1", summary="Pick auth method", blocks=["login_flow"])
    item = _build_review_item(
        _qdr(source_decision_id="D1"),
        {"D1": d},
    )
    assert "Pick auth method" in item.decision_context
    assert item.blocks_what == ["login_flow"]


def test_build_item_empty_context_when_no_decision():
    item = _build_review_item(_qdr(source_decision_id=None), {})
    assert item.decision_context == ""
    assert item.blocks_what == []


def test_build_item_empty_context_when_decision_not_in_map():
    item = _build_review_item(_qdr(source_decision_id="D99"), {})
    assert item.decision_context == ""


def test_build_item_raw_moderator_output_only_for_parse_failed():
    ok_item = _build_review_item(_qdr(status="RESOLVED"), {})
    assert ok_item.raw_moderator_output is None

    fail_item = _build_review_item(
        _qdr(status="MODERATOR_PARSE_FAILED", moderator_summary="raw garbage output"),
        {},
    )
    assert fail_item.raw_moderator_output == "raw garbage output"


def test_build_item_caveat_preserved():
    item = _build_review_item(
        _qdr(status="RESOLVED_WITH_CAVEAT", caveat="double-check at impl"),
        {},
    )
    assert item.caveat == "double-check at impl"


# ---- build_sections tests ----


def test_build_sections_returns_all_four_sections_even_if_empty():
    ctx = _stub_ctx(results=[])
    sections = build_sections(ctx)
    names = [s.name for s in sections]
    assert names == ["forced", "parse_failed", "consensus", "auto_resolved"]
    for s in sections:
        assert len(s.items) == 0


def test_build_sections_routes_each_status():
    results = [
        _qdr("Q1", "ESCALATE_TO_HUMAN"),
        _qdr("Q2", "MODERATOR_PARSE_FAILED"),
        _qdr("Q3", "RESOLVED"),
        _qdr("Q4", "RESOLVED", auto_resolution_reason="OPTIONAL — auto."),
        _qdr("Q5", "NEED_MORE_EVIDENCE"),
        _qdr("Q6", "RESOLVED_WITH_CAVEAT", caveat="check X"),
    ]
    ctx = _stub_ctx(results=results)
    sections = build_sections(ctx)
    by_name = {s.name: s for s in sections}

    assert [i.question_id for i in by_name["forced"].items] == ["Q1", "Q5"]
    assert [i.question_id for i in by_name["parse_failed"].items] == ["Q2"]
    assert [i.question_id for i in by_name["consensus"].items] == ["Q3", "Q6"]
    assert [i.question_id for i in by_name["auto_resolved"].items] == ["Q4"]


def test_build_sections_collapsed_defaults():
    ctx = _stub_ctx(results=[_qdr()])
    sections = build_sections(ctx)
    by_name = {s.name: s for s in sections}

    assert by_name["forced"].collapsed_by_default is False
    assert by_name["parse_failed"].collapsed_by_default is False
    assert by_name["consensus"].collapsed_by_default is True
    assert by_name["auto_resolved"].collapsed_by_default is True


def test_build_sections_injects_decision_context():
    d = _decision("D1", summary="Choose DB", blocks=["data_layer"])
    results = [_qdr("Q1", "RESOLVED", source_decision_id="D1")]
    ctx = _stub_ctx(results=results, decisions=[d])
    sections = build_sections(ctx)
    item = next(i for s in sections for i in s.items if i.question_id == "Q1")
    assert "Choose DB" in item.decision_context
    assert "data_layer" in item.blocks_what


# ---- total_pending tests ----


def test_total_pending_counts_only_forced_and_parse_failed():
    forced = ReviewSection(name="forced", items=[ReviewItem(
        question_id="Q1", question_text="?", classification="REQUIRED", domain="sec",
        decision_context="", blocks_what=[], agent_a="A", agent_b="B",
        agent_a_position="a", agent_b_position="b", moderator_summary="m",
        confidence=0.4, resolution_status="ESCALATE_TO_HUMAN",
        caveat=None, auto_resolution_reason=None, raw_moderator_output=None,
    )])
    parse_fail = ReviewSection(name="parse_failed", items=[ReviewItem(
        question_id="Q2", question_text="?", classification="REQUIRED", domain="sec",
        decision_context="", blocks_what=[], agent_a="A", agent_b="B",
        agent_a_position="a", agent_b_position="b", moderator_summary="raw",
        confidence=0.0, resolution_status="MODERATOR_PARSE_FAILED",
        caveat="failed", auto_resolution_reason=None, raw_moderator_output="raw",
    )])
    consensus = ReviewSection(name="consensus", items=[ReviewItem(
        question_id="Q3", question_text="?", classification="REQUIRED", domain="sec",
        decision_context="", blocks_what=[], agent_a="A", agent_b="B",
        agent_a_position="a", agent_b_position="b", moderator_summary="m",
        confidence=0.9, resolution_status="RESOLVED",
        caveat=None, auto_resolution_reason=None, raw_moderator_output=None,
    )])
    auto_r = ReviewSection(name="auto_resolved", items=[ReviewItem(
        question_id="Q4", question_text="?", classification="OPTIONAL", domain="ux",
        decision_context="", blocks_what=[], agent_a="A", agent_b="B",
        agent_a_position="", agent_b_position="", moderator_summary="auto",
        confidence=1.0, resolution_status="RESOLVED",
        caveat=None, auto_resolution_reason="OPTIONAL — auto.", raw_moderator_output=None,
    )])

    assert total_pending([forced, parse_fail, consensus, auto_r]) == 2
