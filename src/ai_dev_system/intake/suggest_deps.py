"""Dependency map: which already-answered brief fields are relevant context when
suggesting a value for a given target field.

Rationale (per intake-wizard-design.md): rather than stuff the whole 30+ field
brief into every suggest prompt, we pre-pick 3-5 fields that meaningfully
constrain the target. Cheaper tokens + sharper suggestions.

If a target is not in this map, the Suggester falls back to the 4 fields the
spec considers "always relevant": problem_statement, primary_user, scope_in,
deployment_target. Skipped fields are filtered out by the caller.
"""
from __future__ import annotations


# target_field_id → ordered tuple of dependency field ids (most relevant first)
DEPENDENCY_MAP: dict[str, tuple[str, ...]] = {
    # Tech surface
    "deployment_target": (
        "data_residency", "existing_auth", "budget_infra",
        "compliance", "must_use_stack",
    ),
    "data_residency": (
        "compliance", "deployment_target", "primary_user",
    ),

    # Users & scale
    "user_count_now": (
        "primary_user", "problem_statement", "who_feels_pain",
    ),
    "user_count_year1": (
        "user_count_now", "primary_user", "success_metric",
    ),
    "user_languages": (
        "primary_user", "data_residency", "who_feels_pain",
    ),
    "accessibility": (
        "primary_user", "compliance", "user_languages",
    ),

    # Environment & integration
    "greenfield_or_brownfield": (
        "existing_auth", "data_sources", "must_integrate_with",
        "team_skills",
    ),
    "existing_auth": (
        "primary_user", "deployment_target", "compliance",
    ),
    "data_sources": (
        "must_integrate_with", "deployment_target", "scope_in",
    ),
    "must_integrate_with": (
        "data_sources", "existing_auth", "scope_in",
    ),

    # NFR cluster
    "nfr_priority": (
        "deadline", "expected_rps", "availability_target",
        "team_skills", "compliance",
    ),
    "expected_rps": (
        "user_count_now", "user_count_year1", "primary_user",
    ),
    "expected_data_volume": (
        "user_count_year1", "data_sources", "primary_user",
    ),
    "availability_target": (
        "cost_of_doing_nothing", "primary_user", "budget_infra",
        "compliance",
    ),
    "latency_target": (
        "primary_user", "nfr_priority", "expected_rps",
    ),

    # Scope helpers (lower-confidence suggests but still helpful)
    "done_definition": (
        "problem_statement", "scope_in", "success_metric",
    ),
}


# Refuse-to-suggest is per-field via template's `ai_can_suggest: false`.
# The Suggester checks that flag before calling the LLM.


# Fallback dep list when target has no entry in DEPENDENCY_MAP.
FALLBACK_DEPS: tuple[str, ...] = (
    "problem_statement",
    "primary_user",
    "scope_in",
    "deployment_target",
)


def resolve_dependencies(target_field_id: str) -> tuple[str, ...]:
    """Return ordered dep list for the target field, falling back to defaults."""
    return DEPENDENCY_MAP.get(target_field_id, FALLBACK_DEPS)
