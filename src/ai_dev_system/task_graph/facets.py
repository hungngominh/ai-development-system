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

FACET_KEYS: tuple[str, ...] = (
    "input", "auth_permission", "business_rule", "database",
    "response", "error_cases", "non_functional", "test_cases",
)

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
}

_VALID_STATUS = {"filled", "needs_human", "na"}
_DISABLE_ENV = "AI_DEV_DISABLE_TASK_FACETS"


def is_implementation_task(task: dict) -> bool:
    return task.get("execution_type") == "atomic" and task.get("type") == "coding"


def _needs_human() -> dict:
    return {"status": "needs_human", "content": "", "reason": ""}


def _all_needs_human() -> dict[str, dict]:
    return {k: _needs_human() for k in FACET_KEYS}


def _coerce_facet(raw) -> dict:
    if not isinstance(raw, dict):
        return _needs_human()
    status = raw.get("status")
    if status not in _VALID_STATUS:
        return _needs_human()
    return {
        "status": status,
        "content": str(raw.get("content") or ""),
        "reason": str(raw.get("reason") or ""),
    }


def _build_facet_prompt(task: dict, spec_content: dict[str, str], profile: dict | None):
    facet_lines = "\n".join(f"- {k}: {FACET_DEFINITIONS[k]}" for k in FACET_KEYS)
    system = (
        "You are a senior engineer detailing one implementation task. For each of "
        "the 8 engineering facets below, write a concrete task-level detail, OR mark "
        'it "na" (with a reason) when irrelevant, OR "needs_human" when you cannot '
        "determine it from the given context. Return ONLY a JSON object keyed by the "
        "8 facet names; each value is "
        '{"status": "filled"|"na"|"needs_human", "content": "...", "reason": "..."}.\n'
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
    """Fill the 8 facets for one task. Never raises; failures → all needs_human."""
    system, user = _build_facet_prompt(task, spec_content, profile)
    try:
        raw = llm.complete(system=system, user=user)
        data = json.loads(raw)
    except Exception:
        return _all_needs_human()
    if not isinstance(data, dict):
        return _all_needs_human()
    return {k: _coerce_facet(data.get(k)) for k in FACET_KEYS}


def generate_task_facets_for_graph(tasks, spec_content, profile, llm):
    """Attach `task['facets']` to each atomic coding task. Honors kill-switch."""
    if os.environ.get(_DISABLE_ENV) == "1":
        return tasks
    for task in tasks:
        if is_implementation_task(task):
            task["facets"] = generate_task_facets(task, spec_content, profile, llm)
    return tasks
