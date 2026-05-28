"""Brief metrics — 6 metrics measuring intake brief quality.

Per spec 2026-05-23-evaluation-harness-design.md Layer 1.

Input: a brief_v2 dict (output of intake wizard).
Output: BriefMetricsReport with each metric + pass/fail per threshold.

Metrics are pure functions over brief content. No LLM calls. No DB.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# 8 critical fields per locked decision (Intake Wizard spec + locked-decisions.md)
CRITICAL_FIELDS: tuple[str, ...] = (
    "problem_statement",
    "scope_in",
    "scope_out",
    "success_metric",
    "primary_user",
    "deployment_target",
    "compliance",
    "current_workaround",
)

# 7 sections of the generic_v1 template
SECTIONS: dict[str, tuple[str, ...]] = {
    "context": ("problem_statement", "who_feels_pain", "current_workaround", "cost_of_doing_nothing"),
    "scope": ("scope_in", "scope_out", "success_metric", "done_definition", "deadline"),
    "persona": (
        "primary_user", "user_count_now", "user_count_year1",
        "user_languages", "accessibility",
    ),
    "constraints": (
        "must_use_stack", "must_not_use", "compliance",
        "data_residency", "budget_infra", "team_skills",
    ),
    "integration": (
        "greenfield_or_brownfield", "existing_auth", "data_sources",
        "must_integrate_with", "deployment_target",
    ),
    "nfr": (
        "nfr_priority", "expected_rps", "expected_data_volume",
        "availability_target", "latency_target",
    ),
    "risk": (
        "known_unknowns", "failed_attempts", "inspiration_refs",
        "political_constraints",
    ),
}

# Thresholds from eval harness spec
THRESHOLDS = {
    "critical_fill_rate": 0.875,            # ≥ 7/8 critical fields filled
    "ai_suggest_acceptance": 0.6,           # ≥ 60% of AI suggestions confirmed
    "assumption_count_max": 5,              # ≤ 5 assumptions
    "consistency_violations_max": 0,        # 0 rule violations
    "field_coverage_per_section": 0.5,      # min 50% per section
    "followup_question_count_max": 10,      # ≤ 10 followup questions
}


@dataclass
class BriefMetricsReport:
    """Per-brief metrics with pass/fail evaluation."""

    # Raw metric values
    critical_fill_rate: float = 0.0
    ai_suggest_acceptance: float = 0.0
    assumption_count: int = 0
    consistency_violations: int = 0
    field_coverage_per_section: float = 0.0
    followup_question_count: int = 0

    # Per-metric pass booleans
    pass_critical_fill: bool = False
    pass_ai_suggest: bool = False
    pass_assumption: bool = False
    pass_consistency: bool = False
    pass_field_coverage: bool = False
    pass_followup: bool = False

    # Per-section breakdown (for debugging)
    section_coverage: dict[str, float] = field(default_factory=dict)
    missing_critical: list[str] = field(default_factory=list)

    def overall_pass(self) -> bool:
        return all([
            self.pass_critical_fill,
            self.pass_ai_suggest,
            self.pass_assumption,
            self.pass_consistency,
            self.pass_field_coverage,
            self.pass_followup,
        ])

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _get_field_value(brief: dict, field_id: str) -> Any:
    """Extract the value of a field from brief v2 structure.

    Brief v2 schema: brief["fields"][field_id] = {"value": ..., "source": ..., "rationale": ...}
    Falls back to brief.get(field_id) if not in fields wrapper (lenient).
    """
    fields = brief.get("fields", {})
    if field_id in fields:
        entry = fields[field_id]
        if isinstance(entry, dict):
            return entry.get("value")
        return entry
    # Lenient fallback
    return brief.get(field_id)


def _get_field_source(brief: dict, field_id: str) -> str | None:
    """Extract the source of a field (user | ai_suggested_confirmed | skipped)."""
    fields = brief.get("fields", {})
    if field_id in fields:
        entry = fields[field_id]
        if isinstance(entry, dict):
            return entry.get("source")
    return None


def _is_filled(value: Any) -> bool:
    """A field is filled if non-None, non-empty-string, non-empty-collection."""
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) > 0
    return True  # numbers, bools, etc.


def compute_critical_fill_rate(brief: dict) -> tuple[float, list[str]]:
    """Fraction of 8 critical fields with non-empty values.

    Returns (rate, missing_field_list).
    """
    missing = []
    filled = 0
    for f in CRITICAL_FIELDS:
        if _is_filled(_get_field_value(brief, f)):
            filled += 1
        else:
            missing.append(f)
    return filled / len(CRITICAL_FIELDS), missing


def compute_ai_suggest_acceptance(brief: dict) -> float:
    """Fraction of AI-suggested values that user confirmed.

    Acceptance = (count where source = 'ai_suggested_confirmed')
                 / (count where source in {'ai_suggested_confirmed', 'ai_suggested_rejected'})

    If denominator is 0 (no suggestions offered), returns 1.0 (vacuously true).
    """
    confirmed = 0
    rejected = 0
    for entry in brief.get("fields", {}).values():
        if not isinstance(entry, dict):
            continue
        source = entry.get("source")
        if source == "ai_suggested_confirmed":
            confirmed += 1
        elif source == "ai_suggested_rejected":
            rejected += 1
    total = confirmed + rejected
    if total == 0:
        return 1.0
    return confirmed / total


def compute_assumption_count(brief: dict) -> int:
    """Number of items in brief.assumptions (gaps surfaced for spec generator).

    Note: distinct from skipped fields. Assumptions are unresolved decisions Stage 2
    didn't catch.
    """
    assumptions = brief.get("assumptions", [])
    return len(assumptions) if isinstance(assumptions, list) else 0


def compute_consistency_violations(brief: dict, rules: list | None = None) -> int:
    """Number of cross-field consistency rule violations.

    `rules` is a list of callable(brief)→bool (True = violation). If None, no rules run
    (returns 0). Real rules live in intake/consistency_rules.py (built in M2).
    """
    if rules is None:
        return 0
    count = 0
    for rule in rules:
        try:
            if rule(brief):
                count += 1
        except Exception:
            # Rule errored — conservatively count as 0 (don't block on rule bugs)
            continue
    return count


def compute_field_coverage_per_section(brief: dict) -> tuple[float, dict[str, float]]:
    """Minimum section-level fill rate, plus per-section breakdown.

    Returns (min_coverage, {section_name: coverage_rate}).
    """
    per_section: dict[str, float] = {}
    for section_name, fields in SECTIONS.items():
        filled = sum(1 for f in fields if _is_filled(_get_field_value(brief, f)))
        per_section[section_name] = filled / len(fields) if fields else 1.0
    min_cov = min(per_section.values()) if per_section else 0.0
    return min_cov, per_section


def compute_followup_question_count(brief: dict) -> int:
    """Number of Stage-2 followup questions asked.

    Stored in brief.audit as events with event='followup_asked' (built in M2).
    Returns 0 if audit not present.
    """
    audit = brief.get("audit", [])
    if not isinstance(audit, list):
        return 0
    return sum(1 for ev in audit if isinstance(ev, dict) and ev.get("event") == "followup_asked")


def compute_brief_metrics(
    brief: dict,
    consistency_rules: list | None = None,
) -> BriefMetricsReport:
    """Run all 6 brief metrics on a single brief dict, return BriefMetricsReport."""
    rate, missing = compute_critical_fill_rate(brief)
    acc = compute_ai_suggest_acceptance(brief)
    assumptions = compute_assumption_count(brief)
    violations = compute_consistency_violations(brief, consistency_rules)
    min_cov, per_section = compute_field_coverage_per_section(brief)
    followup_count = compute_followup_question_count(brief)

    return BriefMetricsReport(
        critical_fill_rate=rate,
        ai_suggest_acceptance=acc,
        assumption_count=assumptions,
        consistency_violations=violations,
        field_coverage_per_section=min_cov,
        followup_question_count=followup_count,

        pass_critical_fill=rate >= THRESHOLDS["critical_fill_rate"],
        pass_ai_suggest=acc >= THRESHOLDS["ai_suggest_acceptance"],
        pass_assumption=assumptions <= THRESHOLDS["assumption_count_max"],
        pass_consistency=violations <= THRESHOLDS["consistency_violations_max"],
        pass_field_coverage=min_cov >= THRESHOLDS["field_coverage_per_section"],
        pass_followup=followup_count <= THRESHOLDS["followup_question_count_max"],

        section_coverage=per_section,
        missing_critical=missing,
    )
