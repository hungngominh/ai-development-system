import json
from ai_dev_system.task_graph.skeleton import build_skeleton
from ai_dev_system.task_graph.enricher import enrich_task, enrich_all, ENRICHABLE_FIELDS


class MockLLM:
    """Real client shape: complete(system, user). Records (system, user) per call."""
    def __init__(self, response: str):
        self.response = response
        self.calls = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.response


def test_enrich_task_works_with_system_user_client():
    """Regression: enricher must call complete(system, user), the real-client
    shape. Before the fix it called complete(prompt) → TypeError → silent no-op."""
    task = build_skeleton()[0]
    llm = MockLLM(json.dumps({
        "title": "Parse forum spec", "objective": "o", "description": "d",
        "done_definition": "dd", "verification_steps": ["s"],
    }))
    result = enrich_task(task, {"functional.md": "f"}, llm)
    assert result["llm_enriched"] is True
    assert result["title"] == "Parse forum spec"
    # complete was called with two args (system, user)
    assert len(llm.calls) == 1 and len(llm.calls[0]) == 2


def test_enrich_system_prompt_avoids_stub_router_substrings():
    """The system prompt must avoid StubDebateLLMClient's routing substrings so
    that under the stub it falls through to a non-JSON default → no-op."""
    from ai_dev_system.task_graph.enricher import _ENRICH_SYSTEM
    low = _ENRICH_SYSTEM.lower()
    for banned in ("question", "generate", "moderator", "synthesis", "finalize", "spec"):
        assert banned not in low, f"system prompt must avoid {banned!r}"


def test_enrich_task_noop_under_debate_stub():
    """Backward-compat: under the real debate stub, enrichment stays a no-op
    (stub returns non-JSON default → resilient fallback)."""
    from ai_dev_system.debate.llm import StubDebateLLMClient
    task = build_skeleton()[0]
    original_title = task["title"]
    result = enrich_task(task, {"functional.md": "f"}, StubDebateLLMClient())
    assert result["llm_enriched"] is False
    assert result["title"] == original_title


def test_enrich_task_fills_content():
    task = build_skeleton()[0]
    llm = MockLLM(json.dumps({
        "title": "Parse forum spec",
        "objective": "Extract forum requirements",
        "description": "Detailed parsing",
        "done_definition": "All parsed",
        "verification_steps": ["Check A", "Check B"],
    }))
    spec = {"problem.md": "forum problem", "requirements.md": "forum reqs", "constraints.md": "pg"}
    result = enrich_task(task, spec, llm)
    assert result["title"] == "Parse forum spec"
    assert result["llm_enriched"] is True
    assert result["enriched_by"] == "llm"
    assert len(llm.calls) == 1


def test_enrich_task_rejects_structure_fields():
    task = build_skeleton()[0]
    llm = MockLLM(json.dumps({
        "title": "New title",
        "id": "HACKED",
        "deps": ["HACKED"],
    }))
    spec = {"problem.md": "x"}
    result = enrich_task(task, spec, llm)
    assert result["id"] == "TASK-PARSE"
    assert result["deps"] == []


def test_enrich_task_fallback_on_error():
    task = build_skeleton()[0]
    original_title = task["title"]
    llm = MockLLM("not valid json {{{{")
    spec = {"problem.md": "x"}
    result = enrich_task(task, spec, llm)
    assert result["title"] == original_title
    assert result["llm_enriched"] is False


def test_enrich_all_skips_composite():
    graph = build_skeleton()
    graph[2]["execution_type"] = "composite"
    llm = MockLLM(json.dumps({"title": "enriched"}))
    spec = {"problem.md": "x"}
    enrich_all(graph, spec, llm)
    assert graph[2]["llm_enriched"] is False
    assert graph[0]["llm_enriched"] is True


def test_enrich_all_noop_without_llm():
    graph = build_skeleton()
    result = enrich_all(graph, {}, None)
    assert all(t["llm_enriched"] is False for t in result)
