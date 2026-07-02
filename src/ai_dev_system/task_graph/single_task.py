"""Standalone single-task spec: free text → minimal coding task → 8 facets.

Reuses the slice-1 facet engine (`task_graph.facets`). No project context, no
task graph, no execution — just enough to produce a facet-complete TaskSpec for
one ad-hoc task. One LLM call (the facets).
"""
from __future__ import annotations

from ai_dev_system.task_graph.facets import generate_task_facets
from ai_dev_system.task_graph.facets_agentic import generate_task_facets_agentic
from ai_dev_system.spec.self_review import self_review, self_review_enabled

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
                     repo_path: str | None = None, log=None,
                     live_log_path=None) -> dict:
    """-> {"task": <task with .facets>, "facets": <8-facet dict>, "findings": [...]}.

    repo_path set → agentic, repo-grounded facets (llm unused for facets).
    else → text/spec facets via `llm` (slice-2 path).
    log: optional callable(str) forwarded to generate_task_facets_agentic.
    live_log_path: NDJSON tool events are appended here (the spec .log file).

    llm=None (agentic path): if self_review_enabled(), a critic client is built
    lazily via make_llm_client("critic"). Any exception during critic-client build
    is swallowed and findings stays [] — the critic must never break spec generation.
    """
    task = build_single_task(idea, title=title)
    if repo_path:
        facets = generate_task_facets_agentic(task, repo_path, log=log, live_log_path=live_log_path)
    else:
        facets = generate_task_facets(task, {}, None, llm)
    task["facets"] = facets

    findings = []
    if self_review_enabled():
        # Resolve the critic LLM client:
        # - text path: reuse the same llm already provided
        # - agentic path (llm is None): build a dedicated critic client lazily
        critic_llm = llm
        if critic_llm is None:
            try:
                from ai_dev_system.llm_factory import make_llm_client
                critic_llm = make_llm_client("critic")
            except Exception:  # noqa: BLE001 — non-blocking; critic failure must not break spec
                critic_llm = None
        if critic_llm is not None:
            # M2: include task objective so the critic has scope context for
            # the scope_decomposition dimension (not just the raw facets dict).
            critic_payload = {"objective": task.get("objective"), "facets": facets}
            findings = self_review(critic_payload, "single_task", critic_llm)

    return {"task": task, "facets": facets, "findings": [f.__dict__ for f in findings]}
