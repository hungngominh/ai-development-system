# src/ai_dev_system/spec/repair.py
"""Spec Generation v2 — Section repair loop (SP6).

When grounding detects violations in a section draft, repair_section()
rebuilds the section with the original draft + violation list prepended to
the user payload so the LLM knows exactly what to fix.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ai_dev_system.spec.generators.base import generate_with_retry, build_user_payload
from ai_dev_system.spec.grounding import GroundingViolation

if TYPE_CHECKING:
    from ai_dev_system.spec.generators.base import SectionDraft
    from ai_dev_system.spec.planner import SectionOutline


def repair_section(
    draft: "SectionDraft",
    violations: list[GroundingViolation],
    outline: "SectionOutline",
    brief: dict,
    approved_answers: dict,
    decisions: list,
    llm_client,
    *,
    system_prompt: str,
) -> "SectionDraft":
    """Regenerate a section with explicit repair instructions.

    Prepends a REPAIR TASK block to the user payload describing each violation.
    The original draft is included so the LLM can revise rather than rewrite.
    """
    violation_lines = [
        f"  [{v.severity.upper()}] {v.rule}: {v.message}"
        for v in violations
    ]
    repair_block = (
        "## REPAIR TASK\n"
        "The previous draft has the following grounding violations. "
        "Fix ONLY these issues — keep all other content intact:\n"
        + "\n".join(violation_lines)
        + "\n\n"
        "## PREVIOUS DRAFT (revise this)\n"
        + draft.content
    )

    base_payload = build_user_payload(
        outline, brief, approved_answers, decisions, include_full_brief=True,
    )
    user_payload = repair_block + "\n\n" + base_payload

    return generate_with_retry(
        system_prompt, user_payload, llm_client, draft.section, max_retries=1,
    )
