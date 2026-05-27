# src/ai_dev_system/spec/generators/design.py
"""Spec Generation v2 — Design section generator (SP3)."""

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
    "design",
    length_guide="800-1500 words",
) + (
    "\n\n"
    "Structure: Architecture Overview → Components → Integration Points → "
    "Deployment Topology → Decisions Table → Trade-offs\n\n"
    "MUST include a 'Decisions' table with columns: Decision ID | Choice | Rationale.\n"
    "Each row maps an approved decision to its chosen answer and why.\n"
    "MUST reference must_use_stack and must_not_use explicitly.\n"
    "Tone: technical, decision-heavy. Audience: senior engineers.\n"
    "DO NOT include user stories, acceptance tests, or NFR numeric targets."
)


def generate_design(
    outline: "SectionOutline",
    brief: dict,
    approved_answers: dict,
    decisions: "list[Decision]",
    llm_client,
) -> SectionDraft:
    """Generate the design.md section."""
    user = build_user_payload(
        outline, brief, approved_answers, decisions,
        include_full_brief=True,
    )
    return generate_with_retry(_SYSTEM_PROMPT, user, llm_client, "design")
