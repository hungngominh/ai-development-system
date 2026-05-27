# src/ai_dev_system/spec/generators/base.py
"""Shared types and helpers for section generators (SP2).

SectionDraft is the unit of output from each generator. All 5 section
generators share the same prompt structure; this module provides the
common boilerplate so each generator only defines its section-specific
system prompt and any post-processing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai_dev_system.spec.planner import SectionOutline

SECTION_FILES = {
    "proposal": "proposal.md",
    "design": "design.md",
    "functional": "functional.md",
    "non_functional": "non-functional.md",
    "acceptance_criteria": "acceptance-criteria.md",
}

_INLINE_REF_NOTE = (
    "Mỗi assertion PHẢI reference nguồn theo format: "
    "`[brief:field_name]` hoặc `[decision:decision_id]` inline. "
    "Ví dụ: 'Theo brief [brief:problem_statement]: \"...\"' hoặc "
    "'Auth sử dụng JWT [decision:auth_choice]'."
)

_SCOPE_GUARD_NOTE = (
    "SCOPE GUARD:\n"
    "- Chỉ mention features có trong scope_in. Không invent thêm.\n"
    "- Nếu scope_out có item X, KHÔNG được mention X một cách tích cực.\n"
    "- Verbatim quote brief field khi reference: "
    "'Theo brief, {{field}}: \"{{value}}\".'"
)


@dataclass
class SectionDraft:
    section: str           # e.g. "proposal"
    content: str           # markdown text
    degraded: bool = False  # True if generation failed and content is a placeholder
    error: str | None = None  # error message if degraded


def build_common_system_prompt(
    section_instruction: str,
    *,
    length_guide: str = "400-800 words",
) -> str:
    """Build the shared generator system prompt prefix."""
    return (
        f"You are a technical writer generating the '{section_instruction}' section "
        f"of a structured technical spec. Target length: {length_guide}.\n\n"
        f"{_INLINE_REF_NOTE}\n\n"
        f"{_SCOPE_GUARD_NOTE}\n\n"
        "Return ONLY Markdown prose — NO JSON wrapper. "
        "Start with an appropriate `## ` heading."
    )


def build_user_payload(
    outline: "SectionOutline",
    brief: dict,
    approved_answers: dict,
    decisions: list,
    *,
    include_full_brief: bool = True,
) -> str:
    """Build the user message for a section generator LLM call."""
    import json

    parts: list[str] = []

    if include_full_brief:
        # Keep brief compact for token budget
        brief_compact = {
            k: v for k, v in brief.items()
            if k not in {"brief_version"} and not k.startswith("_")
        }
        parts.append(f"## PROJECT BRIEF\n```json\n{json.dumps(brief_compact, ensure_ascii=False, indent=2)}\n```")

    if approved_answers:
        ans_lines = [f"- {qid}: {ans}" for qid, ans in list(approved_answers.items())[:20]]
        parts.append("## APPROVED DECISIONS\n" + "\n".join(ans_lines))

    if decisions:
        dec_lines = [
            f"- [{d.id}] {d.summary} ({d.classification}, domain: {', '.join(d.domain_hints or [])})"
            for d in decisions[:15]
        ]
        parts.append("## DECISION INVENTORY\n" + "\n".join(dec_lines))

    # Section-specific outline
    outline_lines = [
        "## YOUR SECTION OUTLINE",
        "**Must cover:**",
        *[f"- {item}" for item in outline.must_cover],
        "",
        "**Must reference these brief fields verbatim:**",
        *[f"- `{ref}`" for ref in outline.must_reference],
        "",
        "**Must NOT mention (owned by other sections):**",
        *[f"- {item}" for item in outline.must_not_mention],
    ]
    if outline.assumptions_for_this_section:
        outline_lines.extend([
            "",
            "**Assumptions to surface in this section:**",
            *[f"- {a}" for a in outline.assumptions_for_this_section],
        ])
    parts.append("\n".join(outline_lines))

    return "\n\n".join(parts)


def generate_with_retry(
    system_prompt: str,
    user_payload: str,
    llm_client,
    section: str,
    *,
    max_retries: int = 2,
) -> SectionDraft:
    """Call LLM with up to max_retries attempts. Returns degraded draft on failure."""
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            content = llm_client.complete(system=system_prompt, user=user_payload)
            if content and content.strip():
                return SectionDraft(section=section, content=content.strip())
            # empty response → retry
            last_error = ValueError("LLM returned empty content")
        except Exception as exc:
            last_error = exc

    # All retries exhausted → degraded placeholder
    placeholder = (
        f"## {section.replace('_', ' ').title()}\n\n"
        f"*(Section generation failed after {max_retries} attempts — "
        f"error: {last_error}. Please review and fill in manually.)*"
    )
    return SectionDraft(
        section=section, content=placeholder,
        degraded=True, error=str(last_error),
    )
