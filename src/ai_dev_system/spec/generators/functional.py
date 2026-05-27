# src/ai_dev_system/spec/generators/functional.py
"""Spec Generation v2 — Functional requirements section generator (SP2)."""

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
    "functional",
    length_guide="600-1200 words",
) + (
    "\n\n"
    "Structure: one subsection per scope_in item. Each subsection:\n"
    "  - User story: As <user>, I want <feature>, so that <value>.\n"
    "  - Detailed requirements (MUST/SHOULD/MAY language).\n"
    "  - Out of scope reference if a related scope_out item exists.\n\n"
    "Use MUST/SHOULD/MAY/MUST NOT per RFC 2119 semantics.\n"
    "End with a 'Scope Boundaries' subsection explicitly listing scope_out items and why excluded.\n"
    "DO NOT include tech stack choices or performance numbers here."
)


def generate_functional(
    outline: "SectionOutline",
    brief: dict,
    approved_answers: dict,
    decisions: "list[Decision]",
    llm_client,
) -> SectionDraft:
    """Generate the functional.md section."""
    user = build_user_payload(
        outline, brief, approved_answers, decisions,
        include_full_brief=True,
    )
    return generate_with_retry(_SYSTEM_PROMPT, user, llm_client, "functional")
