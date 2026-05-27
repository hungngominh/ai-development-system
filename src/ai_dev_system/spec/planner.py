# src/ai_dev_system/spec/planner.py
"""Spec Generation v2 — Section Planner (SP1).

Rule-based outline generation: given brief_v2 + approved_answers + decisions +
questions, produce a SectionOutline for each of the 5 spec sections.

The planner is DETERMINISTIC — no LLM call. It expands the fixed SectionRules
constants with brief-specific values (actual scope_in items, NFR priorities,
decision IDs, etc.) to pre-allocate "what each section must contain" before
parallel generation begins.

Prevents overlap (functional reqs leaking into design.md) and gaps (acceptance
criteria without matching scope_in coverage).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai_dev_system.debate.questions.models import Decision, Question

SECTION_NAMES = [
    "proposal",
    "design",
    "functional",
    "non_functional",
    "acceptance_criteria",
]

_DOMAIN_DESIGN = frozenset({"backend", "data", "devops", "infra", "security"})


@dataclass
class SectionOutline:
    section: str                         # one of SECTION_NAMES
    must_cover: list[str]                # required topics for this section
    must_reference: list[str]            # brief field IDs / decision IDs to cite
    must_not_mention: list[str]          # topics owned by other sections
    assumptions_for_this_section: list[str]  # skipped brief fields relevant here
    estimated_tokens: int = 800


@dataclass
class PlannerOutput:
    outlines: list[SectionOutline]
    assumptions_to_surface: list[str]
    open_questions: list[str]            # from ESCALATE / PARSE_FAILED debate items


def build_outlines(
    brief: dict,
    approved_answers: dict,
    decisions: "list[Decision] | None" = None,
    questions: "list[Question] | None" = None,
) -> PlannerOutput:
    """Build section outlines from brief + approved_answers + optional v2 inputs.

    Works for both legacy (brief is a normalized v1 dict) and v2 briefs.
    With a v2 brief the outlines are richer; with legacy they degrade
    gracefully to a minimal set of must_cover points.
    """
    # Normalize inputs
    decisions = decisions or []
    questions = questions or []
    is_v2 = bool(brief) and brief.get("brief_version") == 2

    # Collect decision IDs relevant to design-domain sections
    design_decision_refs = [
        d.id for d in decisions
        if any(h in _DOMAIN_DESIGN for h in (d.domain_hints or []))
    ]

    # Collect open questions from debate (ESCALATE / PARSE_FAILED in approved_answers
    # typically means the user provided a manual answer — we flag them as surfaced
    # assumptions rather than as "open"). True open questions would be none here,
    # but we can surface brief.known_unknowns.
    open_questions: list[str] = list(brief.get("known_unknowns") or [])

    # Global assumptions: brief fields the user skipped ("skipped" source)
    skipped_fields = [
        k for k, v in brief.items()
        if isinstance(v, dict) and v.get("source") == "skipped"
    ] if is_v2 else []
    assumptions_to_surface = skipped_fields + (brief.get("assumptions") or [])

    outlines = [
        _proposal_outline(brief, assumptions_to_surface, is_v2),
        _design_outline(brief, decisions, design_decision_refs, approved_answers, assumptions_to_surface, is_v2),
        _functional_outline(brief, decisions, assumptions_to_surface, is_v2),
        _non_functional_outline(brief, assumptions_to_surface, is_v2),
        _acceptance_outline(brief, assumptions_to_surface, is_v2),
    ]

    return PlannerOutput(
        outlines=outlines,
        assumptions_to_surface=assumptions_to_surface,
        open_questions=open_questions,
    )


# ---- per-section outline builders ----


def _proposal_outline(brief: dict, assumptions: list[str], is_v2: bool) -> SectionOutline:
    scope_in = brief.get("scope_in") or []
    scope_out = brief.get("scope_out") or []
    success_metric = brief.get("success_metric") or brief.get("raw_idea", "")
    who = brief.get("who_feels_pain") or brief.get("primary_user") or "target users"

    must_cover = [
        f"Problem statement (verbatim from brief): {str(brief.get('problem_statement', ''))[:120]}",
        f"Target users: {who}",
        "Cost of doing nothing / current workaround",
        f"Success metrics: {str(success_metric)[:120]}",
        f"Scope IN summary: {', '.join(scope_in[:5]) or '(not specified)'}",
        f"Scope OUT summary: {', '.join(scope_out[:5]) or 'none'}",
    ]
    if assumptions:
        must_cover.append(f"Assumptions / open fields: {', '.join(assumptions[:5])}")

    return SectionOutline(
        section="proposal",
        must_cover=must_cover,
        must_reference=["problem_statement", "success_metric", "scope_in", "scope_out",
                        "who_feels_pain", "cost_of_doing_nothing"],
        must_not_mention=["specific tech stack", "API endpoints", "DB schema",
                          "performance numbers", "test scenarios"],
        assumptions_for_this_section=[a for a in assumptions if "problem" in a.lower() or "user" in a.lower()],
        estimated_tokens=600,
    )


def _design_outline(
    brief: dict,
    decisions: list,
    design_decision_refs: list[str],
    approved_answers: dict,
    assumptions: list[str],
    is_v2: bool,
) -> SectionOutline:
    stack = brief.get("must_use_stack") or brief.get("existing_auth") or []
    deployment = brief.get("deployment_target") or "(not specified)"
    must_not_use = brief.get("must_not_use") or []

    must_cover = [
        "Architecture overview",
        f"Tech stack decisions (from approved decisions: {', '.join(list(approved_answers.keys())[:5])})",
        f"Integration points (must_use_stack: {stack})",
        f"Deployment topology: {deployment}",
        "Trade-offs and rejected alternatives",
    ]
    if must_not_use:
        must_cover.append(f"Explicitly excluded technologies: {must_not_use}")

    must_ref = ["must_use_stack", "must_not_use", "existing_auth", "deployment_target", "data_residency"]
    must_ref.extend(design_decision_refs[:8])

    return SectionOutline(
        section="design",
        must_cover=must_cover,
        must_reference=must_ref,
        must_not_mention=["business goals", "acceptance test scenarios", "user stories",
                          "NFR numeric targets (those go in non_functional)"],
        assumptions_for_this_section=[a for a in assumptions if "stack" in a.lower() or "tech" in a.lower() or "deploy" in a.lower()],
        estimated_tokens=1200,
    )


def _functional_outline(brief: dict, decisions: list, assumptions: list[str], is_v2: bool) -> SectionOutline:
    scope_in = brief.get("scope_in") or []
    scope_out = brief.get("scope_out") or []
    primary_user = brief.get("who_feels_pain") or brief.get("primary_user") or "users"

    must_cover = [f"Requirements for scope item: {item}" for item in scope_in[:8]]
    must_cover.extend([
        f"Primary user flow for: {primary_user}",
        "Explicit scope-out exclusions (with rationale for each scope_out item)",
    ])
    if not scope_in:
        must_cover.append("Functional requirements based on approved decisions")

    must_ref = ["scope_in", "scope_out", "who_feels_pain"]
    if is_v2:
        must_ref.append("primary_user")

    return SectionOutline(
        section="functional",
        must_cover=must_cover,
        must_reference=must_ref,
        must_not_mention=["latency targets", "scaling decisions", "tech stack choices",
                          "test scenarios / Given-When-Then"],
        assumptions_for_this_section=[a for a in assumptions if "scope" in a.lower() or "feature" in a.lower()],
        estimated_tokens=1000,
    )


def _non_functional_outline(brief: dict, assumptions: list[str], is_v2: bool) -> SectionOutline:
    nfr = brief.get("nfr_priority") or []
    rps = brief.get("expected_rps") or "(not specified)"
    latency = brief.get("latency_target") or "(not specified)"
    availability = brief.get("availability_target") or "(not specified)"
    compliance = brief.get("compliance") or []
    data_volume = brief.get("expected_data_volume") or "(not specified)"

    must_cover = [
        f"NFR priority ranking (verbatim from brief): {', '.join(nfr) or '(not specified)'}",
        f"Performance target: expected RPS={rps}, latency={latency}",
        f"Availability target: {availability}",
        f"Data volume + retention: {data_volume}",
        f"Compliance requirements: {compliance}",
    ]

    return SectionOutline(
        section="non_functional",
        must_cover=must_cover,
        must_reference=["nfr_priority", "expected_rps", "availability_target",
                        "latency_target", "expected_data_volume", "compliance"],
        must_not_mention=["feature list", "user stories", "test scenarios"],
        assumptions_for_this_section=[a for a in assumptions if "nfr" in a.lower() or "performance" in a.lower() or "compliance" in a.lower()],
        estimated_tokens=600,
    )


def _acceptance_outline(brief: dict, assumptions: list[str], is_v2: bool) -> SectionOutline:
    scope_in = brief.get("scope_in") or []
    success_metric = brief.get("success_metric") or ""
    done_definition = brief.get("done_definition") or ""

    must_cover = [f"Given/When/Then AC for scope item: {item}" for item in scope_in[:8]]
    must_cover.extend([
        f"AC for success metric: {str(success_metric)[:100]}",
        f"Done definition: {str(done_definition)[:100]}",
        "All ACs must have measurable thresholds (numbers, not 'fast' or 'good')",
    ])

    return SectionOutline(
        section="acceptance_criteria",
        must_cover=must_cover,
        must_reference=["scope_in", "success_metric", "done_definition"],
        must_not_mention=["how to implement", "technology choices", "architecture"],
        assumptions_for_this_section=[a for a in assumptions if "acceptance" in a.lower() or "done" in a.lower() or "metric" in a.lower()],
        estimated_tokens=800,
    )
