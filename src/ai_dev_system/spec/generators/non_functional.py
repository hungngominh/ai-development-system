# src/ai_dev_system/spec/generators/non_functional.py
"""Spec Generation v2 — Non-functional requirements section generator (SP3)."""

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
    "non_functional",
    length_guide="400-700 words",
) + (
    "\n\n"
    "Structure: present NFRs in the EXACT priority order from brief.nfr_priority.\n"
    "Each NFR subsection MUST state a measurable numeric target — no vague language.\n"
    "Examples: 'p95 latency < 300ms', 'availability >= 99.9%', '10k concurrent users'.\n"
    "NEVER write 'should be fast' or 'high availability' without a number.\n"
    "Include: Performance | Availability | Scalability | Data Retention | Compliance | Security.\n"
    "DO NOT include feature list, user stories, or tech stack choices here."
)


def generate_non_functional(
    outline: "SectionOutline",
    brief: dict,
    approved_answers: dict,
    decisions: "list[Decision]",
    llm_client,
) -> SectionDraft:
    """Generate the non-functional.md section."""
    user = build_user_payload(
        outline, brief, approved_answers, decisions,
        include_full_brief=True,
    )
    return generate_with_retry(_SYSTEM_PROMPT, user, llm_client, "non_functional")
