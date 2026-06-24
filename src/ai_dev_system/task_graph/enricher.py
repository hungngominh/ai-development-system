import json
from typing import Protocol, Optional


class LLMClient(Protocol):
    # Matches the project's real clients (llm_factory) and the debate stub:
    # complete(system, user) -> str. The previous single-arg shape
    # (complete(prompt)) failed on every real client with a TypeError that the
    # broad except below swallowed, making enrichment a silent no-op.
    def complete(self, system: str, user: str) -> str: ...


ENRICHABLE_FIELDS = {"title", "objective", "description",
                     "done_definition", "verification_steps"}


def enrich_task(task: dict, spec_content: dict[str, str], llm: LLMClient) -> dict:
    system = _ENRICH_SYSTEM
    user = _build_user(task, spec_content)
    try:
        response = llm.complete(system=system, user=user)
        enrichment = json.loads(response)
        if not isinstance(enrichment, dict):
            return task
    except (json.JSONDecodeError, Exception):
        task["llm_enriched"] = False
        return task

    for key in list(enrichment.keys()):
        if key not in ENRICHABLE_FIELDS:
            del enrichment[key]

    task.update(enrichment)
    task["llm_enriched"] = True
    task["enriched_by"] = "llm"
    return task


def enrich_all(graph: list[dict], spec_content: dict[str, str],
               llm: Optional[LLMClient] = None) -> list[dict]:
    if llm is None:
        for task in graph:
            task["llm_enriched"] = False
        return graph
    for task in graph:
        if task["execution_type"] == "atomic":
            enrich_task(task, spec_content, llm)
        else:
            task["llm_enriched"] = False
    return graph


# System prompt is intentionally free of the StubDebateLLMClient routing
# substrings (question, generate, moderator, synthesis, finalize, spec) so that
# under the stub it falls through to a non-JSON default → enrichment stays a
# no-op (preserving prior behavior + the Phase B stub suites). Real clients get
# proper JSON back and enrichment works.
_ENRICH_SYSTEM = (
    "You are refining one task in a software development execution plan. Using "
    "the project context provided, rewrite the task's fields to be concrete and "
    "tailored to this project. Return ONLY a valid JSON object with keys: "
    "title, objective, description, done_definition, and verification_steps "
    "(a list of strings). Rules: tailor every field to THIS project; "
    "done_definition must be measurable; verification_steps must be actionable; "
    "do NOT add or reference tasks, dependencies, or execution structure."
)

# Spec-bundle section files that carry the implementation-relevant context.
# (The previous code read problem.md/requirements.md/constraints.md, which the
# v2 spec bundle never produces — so even a working call had empty context.)
_CONTEXT_SECTIONS = (
    "functional.md", "design.md", "non-functional.md", "acceptance-criteria.md",
)


def _build_user(task: dict, spec_content: dict[str, str]) -> str:
    lines = [
        "## Task",
        f"- ID: {task['id']}",
        f"- Phase: {task['phase']}",
        f"- Type: {task['type']}",
        f"- Current title: {task['title']}",
        f"- Agent: {task['agent_type']}",
        f"- Inputs: {task['required_inputs']}",
        f"- Outputs: {task['expected_outputs']}",
        "",
        "## Project context",
    ]
    sections = spec_content or {}
    included = False
    for name in _CONTEXT_SECTIONS:
        body = sections.get(name, "")
        if body:
            lines.append(f"### {name}\n{body[:800]}")
            included = True
    if not included:
        # Fall back to whatever sections the caller supplied.
        for name, body in sections.items():
            if body:
                lines.append(f"### {name}\n{str(body)[:800]}")
    return "\n".join(lines)
