"""Unit tests for gate.gate1_review.renderer (G3).

Snapshot-style tests: verify that rendered markdown blocks contain
the right keywords and structural elements for each section type.
"""

from __future__ import annotations

from ai_dev_system.gate.gate1_review.renderer import (
    render_brief_header,
    render_forced_section,
    render_parse_failed_section,
    render_consensus_section,
    render_auto_resolved_section,
    render_item_detail,
    render_optional_expanded,
    _render_consensus_item,
)
from ai_dev_system.gate.gate1_review.sections import ReviewItem, ReviewSection


# ---- helpers ----


def _item(
    qid="Q1",
    status="ESCALATE_TO_HUMAN",
    *,
    classification="REQUIRED",
    domain="security",
    agent_a="SecuritySpecialist",
    agent_b="BackendArchitect",
    agent_a_pos="Use JWT",
    agent_b_pos="Use sessions",
    summary="JWT wins",
    confidence=0.45,
    caveat=None,
    auto_reason=None,
    raw_mod=None,
    decision_context="",
    blocks_what=None,
) -> ReviewItem:
    return ReviewItem(
        question_id=qid,
        question_text="Use JWT?",
        classification=classification,
        domain=domain,
        decision_context=decision_context,
        blocks_what=blocks_what or [],
        agent_a=agent_a,
        agent_b=agent_b,
        agent_a_position=agent_a_pos,
        agent_b_position=agent_b_pos,
        moderator_summary=summary,
        confidence=confidence,
        resolution_status=status,
        caveat=caveat,
        auto_resolution_reason=auto_reason,
        raw_moderator_output=raw_mod,
    )


def _stub_ctx(is_legacy=True, project_name="TestProject", brief=None):
    from ai_dev_system.gate.gate1_review.loader import GateReviewContext
    ctx = GateReviewContext.__new__(GateReviewContext)
    ctx.run_id = "r1"
    ctx.project_name = project_name
    ctx.is_legacy_brief = is_legacy
    ctx.brief = brief or {"raw_idea": "Build a thing"}
    ctx.debate_report = {"results": []}
    ctx.decisions = None
    ctx.questions = []
    ctx.coverage_report = None
    ctx.decision_by_id = {}
    return ctx


# ---- brief header ----


def test_brief_header_legacy_shows_raw_idea():
    ctx = _stub_ctx(is_legacy=True, brief={"raw_idea": "Build the platform"})
    out = render_brief_header(ctx)
    assert "Legacy" in out
    assert "Build the platform" in out


def test_brief_header_v2_shows_problem_statement():
    ctx = _stub_ctx(is_legacy=False, brief={
        "brief_version": 2,
        "problem_statement": "Teams need async comms",
        "scope_in": ["chat", "notifications"],
        "nfr_priority": ["latency", "reliability"],
    })
    out = render_brief_header(ctx)
    assert "Teams need async comms" in out
    assert "chat" in out
    assert "show brief" in out


# ---- forced section ----


def test_forced_section_shows_agent_positions():
    section = ReviewSection(name="forced", items=[
        _item("Q3", "ESCALATE_TO_HUMAN", agent_a_pos="Use JWT", agent_b_pos="Use sessions")
    ])
    out = render_forced_section(section)
    assert "Q3" in out
    assert "Use JWT" in out
    assert "Use sessions" in out
    assert "chọn A" in out or "chon A" in out.lower() or "chọn" in out


def test_forced_section_shows_decision_context():
    section = ReviewSection(name="forced", items=[
        _item("Q3", decision_context="Pick auth method", blocks_what=["login_flow"])
    ])
    out = render_forced_section(section)
    assert "Pick auth method" in out
    assert "login_flow" in out


def test_forced_section_empty_shows_no_forced_message():
    out = render_forced_section(ReviewSection(name="forced"))
    assert "không có câu nào cần quyết định" in out.lower()


def test_forced_section_low_confidence_note():
    section = ReviewSection(name="forced", items=[
        _item("Q1", confidence=0.35)
    ])
    out = render_forced_section(section)
    assert "0.35" in out


# ---- parse_failed section ----


def test_parse_failed_shows_raw_output():
    section = ReviewSection(name="parse_failed", items=[
        _item("Q5", "MODERATOR_PARSE_FAILED", raw_mod="Sure here's my response: blah blah")
    ])
    out = render_parse_failed_section(section)
    assert "Q5" in out
    assert "blah blah" in out
    assert "⚠️" in out or "parse" in out.lower()


def test_parse_failed_empty_returns_empty_string():
    out = render_parse_failed_section(ReviewSection(name="parse_failed"))
    assert out == ""


# ---- consensus section ----


def test_consensus_item_one_liner():
    item = _item("Q2", "RESOLVED", summary="PostgreSQL wins", confidence=0.92)
    out = _render_consensus_item(item)
    assert "Q2" in out
    assert "PostgreSQL wins" in out
    assert "0.92" in out


def test_consensus_section_with_caveat():
    section = ReviewSection(name="consensus", items=[
        _item("Q2", "RESOLVED_WITH_CAVEAT", summary="Use Postgres", caveat="review at scale")
    ])
    out = render_consensus_section(section)
    assert "review at scale" in out


def test_consensus_section_shows_count():
    section = ReviewSection(name="consensus", items=[
        _item("Q1", "RESOLVED"),
        _item("Q2", "RESOLVED"),
    ])
    out = render_consensus_section(section)
    assert "2" in out


# ---- auto_resolved section ----


def test_auto_resolved_section_shows_count():
    section = ReviewSection(name="auto_resolved", items=[
        _item("Q10", auto_reason="OPTIONAL — auto.", classification="OPTIONAL"),
        _item("Q11", auto_reason="OPTIONAL — safe default.", classification="OPTIONAL"),
    ])
    out = render_auto_resolved_section(section)
    assert "2" in out
    assert "expand optional" in out


def test_auto_resolved_empty_returns_empty():
    out = render_auto_resolved_section(ReviewSection(name="auto_resolved"))
    assert out == ""


# ---- item detail ----


def test_item_detail_shows_all_fields():
    item = _item("Q3", "ESCALATE_TO_HUMAN",
                 agent_a_pos="JWT is better", agent_b_pos="Sessions scale better",
                 summary="No consensus", confidence=0.4, caveat="check team size",
                 decision_context="Pick auth", blocks_what=["api_auth"])
    out = render_item_detail(item)
    assert "JWT is better" in out
    assert "Sessions scale better" in out
    assert "No consensus" in out
    assert "check team size" in out
    assert "Pick auth" in out
    assert "api_auth" in out


def test_item_detail_auto_resolved_shows_reason():
    item = _item("Q10", "RESOLVED", auto_reason="OPTIONAL — safe default from D1.")
    out = render_item_detail(item)
    assert "OPTIONAL" in out


# ---- render_optional_expanded ----


def test_render_optional_expanded_lists_all_items():
    items = [
        _item("Q10", auto_reason="OPTIONAL — no debate."),
        _item("Q11", auto_reason="OPTIONAL — safe default D2."),
    ]
    out = render_optional_expanded(items)
    assert "Q10" in out
    assert "Q11" in out
    assert "OPTIONAL" in out


def test_render_optional_expanded_empty():
    out = render_optional_expanded([])
    assert "không có" in out.lower() or "Không có" in out
