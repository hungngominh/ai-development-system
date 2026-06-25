"""Standalone single-task spec: free text → minimal coding task → 8 facets.

Reuses the slice-1 facet engine (`task_graph.facets`). No project context, no
task graph, no execution — just enough to produce a facet-complete TaskSpec for
one ad-hoc task. One LLM call (the facets).
"""
from __future__ import annotations

from ai_dev_system.task_graph.facets import generate_task_facets
from ai_dev_system.task_graph.facets_agentic import generate_task_facets_agentic

_ADHOC_ID = "TASK-ADHOC"
_TITLE_MAX = 60


def build_single_task(idea: str, *, title: str | None = None) -> dict:
    idea = (idea or "").strip()
    derived = title or (idea[:_TITLE_MAX].rstrip() + ("…" if len(idea) > _TITLE_MAX else ""))
    return {
        "id": _ADHOC_ID,
        "title": derived or "Ad-hoc task",
        "objective": idea,
        "description": idea,
        "type": "coding",
        "execution_type": "atomic",
        "required_inputs": [],
        "expected_outputs": [],
        "out_of_scope": "",
    }


def spec_single_task(idea: str, llm, *, title: str | None = None,
                     repo_path: str | None = None, log=None) -> dict:
    """-> {"task": <task with .facets>, "facets": <8-facet dict>}.

    repo_path set → agentic, repo-grounded facets (llm unused).
    else → text/spec facets via `llm` (slice-2 path).
    log: optional callable(str) forwarded to generate_task_facets_agentic.
    """
    task = build_single_task(idea, title=title)
    if repo_path:
        facets = generate_task_facets_agentic(task, repo_path, log=log)
    else:
        facets = generate_task_facets(task, {}, None, llm)
    task["facets"] = facets
    return {"task": task, "facets": facets}
