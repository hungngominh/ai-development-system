# src/ai_dev_system/spec/grounding.py
"""Spec Generation v2 — Rule-based grounding checker (SP5).

Four rules checked per section draft:
  1. scope_out_positive  — scope_out items not mentioned in a building/positive context
  2. inline_refs         — [brief:field] markers present for each must_reference field
  3. measurable_ac       — acceptance_criteria uses numbers, not vague words
  4. scope_in_coverage   — at least some scope_in items appear in functional/AC sections
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

SeverityT = Literal["error", "warning"]

_VAGUE_WORDS = re.compile(
    r"\b(fast|good|properly|efficiently|effectively|adequate|appropriate|reasonable|nice)\b",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"\d+")
_SCOPE_OUT_EXCLUSION_PHRASES = re.compile(
    r"(scope.out|excluded|not includ|ngoài phạm vi|không bao gồm|out of scope|will not|won't)",
    re.IGNORECASE,
)


@dataclass
class GroundingViolation:
    rule: str
    message: str
    severity: SeverityT = "error"


@dataclass
class GroundingReport:
    section: str
    violations: list[GroundingViolation] = field(default_factory=list)
    passed_rules: list[str] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(v.severity == "error" for v in self.violations)

    @property
    def has_violations(self) -> bool:
        return bool(self.violations)


def check_section(
    section: str,
    content: str,
    outline,        # SectionOutline
    brief: dict,
) -> GroundingReport:
    """Run all applicable grounding rules for one section.

    Returns a GroundingReport with violations and passed rules.
    """
    report = GroundingReport(section=section)

    _check_scope_out(content, brief, report)
    _check_inline_refs(content, outline, report)
    if section == "acceptance_criteria":
        _check_measurable_ac(content, report)
    if section in ("functional", "acceptance_criteria"):
        _check_scope_in_coverage(content, brief, report)

    return report


# ---- individual rule checkers ----


def _check_scope_out(content: str, brief: dict, report: GroundingReport) -> None:
    """Rule: scope_out items must not be mentioned positively in the spec."""
    scope_out = brief.get("scope_out") or []
    violations: list[str] = []

    for item in scope_out:
        item_lower = item.lower()
        pos = content.lower().find(item_lower)
        if pos == -1:
            continue
        # Check if there's an exclusion phrase within 120 chars before the match
        context_before = content[max(0, pos - 120): pos].lower()
        if _SCOPE_OUT_EXCLUSION_PHRASES.search(context_before):
            continue  # Mentioned in "excluded / out of scope" context — OK
        violations.append(item)

    if violations:
        report.violations.append(GroundingViolation(
            rule="scope_out_positive",
            message=f"Scope-out items appear without exclusion context: {violations}",
            severity="error",
        ))
    else:
        report.passed_rules.append("scope_out_positive")


def _check_inline_refs(content: str, outline, report: GroundingReport) -> None:
    """Rule: each must_reference field should have a [brief:field] marker in content."""
    missing: list[str] = []
    for ref in outline.must_reference:
        if ref.startswith("[") or ":" in ref:
            # Decision ref like [decision:D1] or already formatted — skip
            continue
        marker = f"[brief:{ref}]"
        if marker not in content:
            missing.append(ref)

    if missing:
        report.violations.append(GroundingViolation(
            rule="inline_refs",
            message=f"Missing [brief:field] markers for: {missing}",
            severity="warning",
        ))
    else:
        report.passed_rules.append("inline_refs")


def _check_measurable_ac(content: str, report: GroundingReport) -> None:
    """Rule: acceptance_criteria must not use vague words without a number nearby."""
    # Find all vague word occurrences; check if a number appears within 150 chars
    vague_hits: list[str] = []
    for match in _VAGUE_WORDS.finditer(content):
        word = match.group(0)
        start = match.start()
        context = content[max(0, start - 150): start + 150]
        if not _NUMBER_RE.search(context):
            vague_hits.append(word)

    if vague_hits:
        report.violations.append(GroundingViolation(
            rule="measurable_ac",
            message=(
                f"Vague words without numeric threshold found: {list(set(vague_hits))}. "
                "Use measurable thresholds (e.g. '< 200ms', '>= 99.9%')."
            ),
            severity="error",
        ))
    else:
        report.passed_rules.append("measurable_ac")


def _check_scope_in_coverage(content: str, brief: dict, report: GroundingReport) -> None:
    """Rule: at least some scope_in items must appear in functional/AC sections."""
    scope_in = brief.get("scope_in") or []
    if not scope_in:
        report.passed_rules.append("scope_in_coverage")
        return

    content_lower = content.lower()
    covered = [item for item in scope_in if item.lower() in content_lower]

    if not covered:
        report.violations.append(GroundingViolation(
            rule="scope_in_coverage",
            message=f"None of scope_in items mentioned: {scope_in}",
            severity="error",
        ))
    else:
        report.passed_rules.append("scope_in_coverage")
