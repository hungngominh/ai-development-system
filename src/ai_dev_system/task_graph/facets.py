"""Per-task engineering facets for atomic implementation tasks.

Runs after `generate_task_graph` (Phase B): for each coding task, an LLM fills
8 facets so the executing agent receives concrete Input/Auth/Business-rule/
Database/Response/Error/NFR/Test detail instead of guessing. Reviewed at Gate 2.

LLM interface is `complete(system, user)` (the working real-client shape — the
sibling enricher's single-arg `complete(prompt)` silently fails on real clients).
Resilient: any failure yields all-`needs_human` facets; never raises.
"""
from __future__ import annotations

import json
import os

SPEC_FACET_KEYS: tuple[str, ...] = (
    "input", "auth_permission", "business_rule", "database",
    "response", "error_cases", "non_functional", "test_cases",
    "validation_rules", "api_endpoints", "security_rules",
    "concurrency_rules", "logging_audit",
)

EXEC_FACET_KEYS: tuple[str, ...] = (
    "impl_document", "api_review", "db_review",
    "business_rule_mapping", "test_evidence", "deployment_note", "change_log",
)

FACET_KEYS: tuple[str, ...] = SPEC_FACET_KEYS + EXEC_FACET_KEYS

FACET_STAGE: dict[str, str] = {
    **{k: "spec" for k in SPEC_FACET_KEYS},
    **{k: "exec" for k in EXEC_FACET_KEYS},
}

# Human-readable intent per facet — drives the prompt and is stable doc.
FACET_DEFINITIONS: dict[str, str] = {
    "input": "Input data/params/artifacts: shape, source, validity constraints.",
    "auth_permission": "Who may run this; authn/authz required; permission boundaries.",
    "business_rule": "Domain logic/constraints for this task (flavored by the product vertical).",
    "database": "Schema/table/migration changes; query patterns; integrity.",
    "response": "Output/return shape: structure, status codes, format.",
    "error_cases": "Known failure modes and how each is handled (flavored by the vertical).",
    "non_functional": "Task-level performance/security/logging/reliability.",
    "test_cases": "Concrete test scenarios (unit/integration) for this task.",
    "validation_rules": "Input validation constraints, required fields, format rules, allowed values.",
    "api_endpoints": "REST endpoints this task creates or modifies: method, path, purpose.",
    "security_rules": "Rate limits, HTTPS, CSRF protection, data sanitisation, OWASP mitigations.",
    "concurrency_rules": "Race conditions, locking strategy, idempotency, retry behaviour.",
    "logging_audit": "What must be logged, at what level, and what audit trail is required.",
    "impl_document": "Developer implementation notes: approach taken, key decisions, gotchas.",
    "api_review": "API review notes: contract changes, versioning impact, consumer impact.",
    "db_review": "Database review notes: migration steps, rollback plan, index strategy.",
    "business_rule_mapping": "Map of business rules to code locations (file:function).",
    "test_evidence": "Test run evidence: passed/failed counts, coverage, CI link.",
    "deployment_note": "Deployment steps, env vars, feature flags, rollout order.",
    "change_log": "Summary of changes made in this task for the changelog.",
}

_VALID_STATUS = {"filled", "needs_human", "na"}
_DISABLE_ENV = "AI_DEV_DISABLE_TASK_FACETS"


def is_implementation_task(task: dict) -> bool:
    return task.get("execution_type") == "atomic" and task.get("type") == "coding"


def _needs_human() -> dict:
    return {"status": "needs_human", "content": "", "reason": ""}


def _all_needs_human() -> dict[str, dict]:
    result = {k: _needs_human() for k in SPEC_FACET_KEYS}
    for k in EXEC_FACET_KEYS:
        result[k] = {"status": "na", "content": "", "reason": "exec-time — fill after implementation"}
    return result


def _coerce_facet(raw) -> dict:
    if not isinstance(raw, dict):
        return _needs_human()
    status = raw.get("status")
    if status not in _VALID_STATUS:
        return _needs_human()
    result = {
        "status": status,
        "content": str(raw.get("content") or ""),
        "reason": str(raw.get("reason") or ""),
    }
    reasoning = str(raw.get("reasoning") or "").strip()
    if reasoning:
        result["reasoning"] = reasoning
    return result


def _build_facet_prompt(task: dict, spec_content: dict[str, str], profile: dict | None):
    facet_lines = "\n".join(f"- {k}: {FACET_DEFINITIONS[k]}" for k in SPEC_FACET_KEYS)
    system = (
        "You are a senior engineer detailing one implementation task. "
        "Before filling each facet, briefly consider three engineering lenses:\n"
        "• Developer — what must be built and how\n"
        "• QA / Security — edge cases, auth risks, error modes\n"
        "• Data — schema implications, query patterns, integrity\n\n"
        "For each of the 13 engineering facets, write a concrete task-level detail, OR "
        'mark it "na" (with a reason) when irrelevant, OR "needs_human" when you cannot '
        "determine it from the given context.\n"
        "Return ONLY a JSON object keyed by the 13 facet names; each value is:\n"
        '{"status": "filled"|"na"|"needs_human", "content": "...", "reason": "...", '
        '"reasoning": "one sentence summarising the key insight from the lenses above"}.\n'
        "Facets:\n" + facet_lines
    )
    # Only the most relevant project sections, truncated.
    sections = []
    for name in ("functional.md", "design.md", "non-functional.md", "acceptance-criteria.md"):
        body = (spec_content or {}).get(name, "")
        if body:
            sections.append(f"### {name}\n{body[:1200]}")
    profile_block = ""
    if profile and profile.get("key_dimensions"):
        profile_block = (
            "\nPRODUCT VERTICAL (flavor business_rule & error_cases accordingly):\n"
            f"- vertical: {profile.get('vertical', '')}\n"
            f"- key dimensions: {'; '.join(profile.get('key_dimensions', []))}\n"
        )
    user = (
        f"# Task {task.get('id', '?')}\n"
        f"objective: {task.get('objective', '')}\n"
        f"description: {task.get('description', '')}\n"
        f"inputs: {task.get('required_inputs', [])}\n"
        f"outputs: {task.get('expected_outputs', [])}\n"
        f"{profile_block}\n"
        "## Project context\n" + ("\n\n".join(sections) if sections else "(none)")
    )
    return system, user


def generate_task_facets(task: dict, spec_content: dict[str, str], profile: dict | None, llm) -> dict[str, dict]:
    """Fill the 13 spec facets for one task. Never raises; failures → all needs_human."""
    system, user = _build_facet_prompt(task, spec_content, profile)
    try:
        raw = llm.complete(system=system, user=user)
        data = json.loads(raw)
    except Exception:
        return _all_needs_human()
    if not isinstance(data, dict):
        return _all_needs_human()
    result = {k: _coerce_facet(data.get(k)) for k in SPEC_FACET_KEYS}
    for k in EXEC_FACET_KEYS:
        result[k] = {"status": "na", "content": "", "reason": "exec-time — fill after implementation"}
    return result


def generate_task_facets_for_graph(tasks, spec_content, profile, llm):
    """Attach `task['facets']` to each atomic coding task. Honors kill-switch."""
    if os.environ.get(_DISABLE_ENV) == "1":
        return tasks
    for task in tasks:
        if is_implementation_task(task):
            task["facets"] = generate_task_facets(task, spec_content, profile, llm)
    return tasks
