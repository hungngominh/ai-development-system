"""Unit tests for spec.planner (SP1).

Tests cover:
- build_outlines returns all 5 sections
- each section's outline has non-empty must_cover
- v2 brief values are embedded in outlines (scope_in, nfr_priority, etc.)
- legacy brief (no brief_version) produces minimal but valid outlines
- decisions with design-domain hints appear in design outline must_reference
- assumptions_to_surface extracted from brief.assumptions + skipped fields
- SectionOutline collapsed_by_default (spec: all 5 always returned)
"""

from __future__ import annotations

import pytest

from ai_dev_system.debate.questions.models import Decision
from ai_dev_system.spec.planner import (
    SECTION_NAMES,
    SectionOutline,
    build_outlines,
)


# ---- fixtures ----


def _brief_v2(
    problem_statement="Teams need async comms",
    scope_in=None,
    scope_out=None,
    nfr_priority=None,
    who_feels_pain="engineers",
    success_metric="DAU > 1000",
    known_unknowns=None,
) -> dict:
    return {
        "brief_version": 2,
        "problem_statement": problem_statement,
        "who_feels_pain": who_feels_pain,
        "scope_in": scope_in or ["chat", "notifications"],
        "scope_out": scope_out or ["mobile app"],
        "nfr_priority": nfr_priority or ["latency", "reliability"],
        "success_metric": success_metric,
        "must_use_stack": ["FastAPI", "PostgreSQL"],
        "must_not_use": ["PHP"],
        "deployment_target": "AWS us-east-1",
        "compliance": ["GDPR"],
        "known_unknowns": known_unknowns or [],
        "assumptions": [],
    }


def _decisions() -> list[Decision]:
    return [
        Decision(id="D1", summary="Auth choice", classification="REQUIRED",
                 domain_hints=["security"], blocks_what=[]),
        Decision(id="D2", summary="DB choice", classification="STRATEGIC",
                 domain_hints=["data"], blocks_what=[]),
        Decision(id="D3", summary="Frontend framework", classification="STRATEGIC",
                 domain_hints=["frontend"], blocks_what=[]),
    ]


# ---- basic structure ----


def test_build_outlines_returns_all_five_sections():
    brief = _brief_v2()
    out = build_outlines(brief, {}, decisions=[], questions=[])
    section_names = [o.section for o in out.outlines]
    for name in SECTION_NAMES:
        assert name in section_names


def test_all_outlines_have_non_empty_must_cover():
    brief = _brief_v2()
    out = build_outlines(brief, {"Q1": "JWT"}, decisions=_decisions())
    for outline in out.outlines:
        assert len(outline.must_cover) > 0, f"{outline.section} has empty must_cover"


def test_all_outlines_have_non_empty_must_reference():
    brief = _brief_v2()
    out = build_outlines(brief, {}, decisions=[])
    for outline in out.outlines:
        assert len(outline.must_reference) > 0, f"{outline.section} has empty must_reference"


def test_all_outlines_have_non_empty_must_not_mention():
    brief = _brief_v2()
    out = build_outlines(brief, {}, decisions=[])
    for outline in out.outlines:
        assert len(outline.must_not_mention) > 0, f"{outline.section} has empty must_not_mention"


def test_all_outlines_have_positive_estimated_tokens():
    brief = _brief_v2()
    out = build_outlines(brief, {}, decisions=[])
    for outline in out.outlines:
        assert outline.estimated_tokens > 0


# ---- v2 brief embedding ----


def test_proposal_embeds_problem_statement():
    brief = _brief_v2(problem_statement="Unique problem statement ABC")
    out = build_outlines(brief, {})
    proposal = next(o for o in out.outlines if o.section == "proposal")
    assert any("Unique problem statement ABC" in item for item in proposal.must_cover)


def test_functional_embeds_scope_in_items():
    brief = _brief_v2(scope_in=["voting", "search", "notifications"])
    out = build_outlines(brief, {})
    func = next(o for o in out.outlines if o.section == "functional")
    # At least some scope_in items appear in must_cover
    assert any("voting" in item or "search" in item or "notification" in item
               for item in func.must_cover)


def test_non_functional_embeds_nfr_priority():
    brief = _brief_v2(nfr_priority=["latency", "reliability", "cost"])
    out = build_outlines(brief, {})
    nf = next(o for o in out.outlines if o.section == "non_functional")
    nfr_text = " ".join(nf.must_cover)
    assert "latency" in nfr_text or "reliability" in nfr_text


def test_acceptance_criteria_references_scope_in_and_success_metric():
    brief = _brief_v2(scope_in=["chat"], success_metric="retention > 40%")
    out = build_outlines(brief, {})
    ac = next(o for o in out.outlines if o.section == "acceptance_criteria")
    assert "scope_in" in ac.must_reference
    assert "success_metric" in ac.must_reference
    assert any("chat" in item for item in ac.must_cover)


# ---- design outline + decisions ----


def test_design_includes_design_domain_decision_ids():
    decisions = [
        Decision(id="D_backend", summary="API choice", classification="REQUIRED",
                 domain_hints=["backend"], blocks_what=[]),
        Decision(id="D_frontend", summary="UI lib", classification="STRATEGIC",
                 domain_hints=["frontend"], blocks_what=[]),  # frontend NOT in design domain
    ]
    brief = _brief_v2()
    out = build_outlines(brief, {}, decisions=decisions)
    design = next(o for o in out.outlines if o.section == "design")
    assert "D_backend" in design.must_reference
    # frontend is NOT in _DOMAIN_DESIGN
    assert "D_frontend" not in design.must_reference


# ---- legacy brief ----


def test_legacy_brief_produces_valid_outlines():
    """Brief without brief_version → graceful fallback, all 5 sections still built."""
    legacy = {"raw_idea": "Build a chat app"}
    out = build_outlines(legacy, {"Q1": "JWT"})
    assert len(out.outlines) == 5
    for o in out.outlines:
        assert o.section in SECTION_NAMES


def test_empty_brief_does_not_crash():
    out = build_outlines({}, {})
    assert len(out.outlines) == 5


# ---- assumptions ----


def test_assumptions_to_surface_from_brief_assumptions():
    brief = _brief_v2()
    brief["assumptions"] = ["Search engine not selected", "Moderation TBD"]
    out = build_outlines(brief, {})
    assert "Search engine not selected" in out.assumptions_to_surface
    assert "Moderation TBD" in out.assumptions_to_surface


def test_open_questions_from_known_unknowns():
    brief = _brief_v2(known_unknowns=["Latency budget for DB", "Auth provider TBD"])
    out = build_outlines(brief, {})
    assert "Latency budget for DB" in out.open_questions


def test_no_assumptions_gives_empty_list():
    brief = _brief_v2()
    out = build_outlines(brief, {})
    assert isinstance(out.assumptions_to_surface, list)
    assert isinstance(out.open_questions, list)
