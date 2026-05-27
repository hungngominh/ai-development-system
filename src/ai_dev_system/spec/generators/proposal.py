# src/ai_dev_system/spec/generators/proposal.py
"""Spec Generation v2 — Proposal section generator (SP2)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ai_dev_system.spec.generators.base import (
    SectionDraft,
    build_common_system_prompt,
    build_user_payload,
    generate_with_retry,
)

if TYPE_CHECKING:
    from ai_dev_system.debate.questions.models import Decision
    from ai_dev_system.spec.planner import SectionOutline

_SYSTEM_PROMPT = build_common_system_prompt(
    "proposal",
    length_guide="300-500 words",
) + (
    "\n\n"
    "Structure: Problem → Target Users → Value (cost of doing nothing) → Scope → Success Metrics\n"
    "Tone: executive summary, non-technical. Avoid jargon.\n"
    "MUST quote brief.problem_statement verbatim in the opening paragraph.\n"
    "MUST quote brief.success_metric verbatim in the Success Metrics subsection."
)


def generate_proposal(
    outline: "SectionOutline",
    brief: dict,
    approved_answers: dict,
    decisions: "list[Decision]",
    llm_client,
) -> SectionDraft:
    """Generate the proposal.md section."""
    user = build_user_payload(
        outline, brief, approved_answers, decisions,
        include_full_brief=True,
    )
    return generate_with_retry(_SYSTEM_PROMPT, user, llm_client, "proposal")
