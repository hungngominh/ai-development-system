# src/ai_dev_system/spec/generators/acceptance_criteria.py
"""Spec Generation v2 — Acceptance criteria section generator (SP3)."""

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
    "acceptance_criteria",
    length_guide="500-1000 words",
) + (
    "\n\n"
    "Structure: one AC group per scope_in item + one group per success_metric.\n"
    "Each AC MUST follow Given/When/Then format.\n"
    "MUST include measurable thresholds — e.g. 'Then response time < 200ms', "
    "'Then error rate < 0.1%'. Reject any AC with 'fast', 'good', 'properly', etc. "
    "as the only measure.\n"
    "DO NOT describe how to implement — only observable outcomes.\n"
    "End with a 'Done Definition' section quoting brief.done_definition verbatim."
)


def generate_acceptance_criteria(
    outline: "SectionOutline",
    brief: dict,
    approved_answers: dict,
    decisions: "list[Decision]",
    llm_client,
) -> SectionDraft:
    """Generate the acceptance-criteria.md section."""
    user = build_user_payload(
        outline, brief, approved_answers, decisions,
        include_full_brief=True,
    )
    return generate_with_retry(_SYSTEM_PROMPT, user, llm_client, "acceptance_criteria")
