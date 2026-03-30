import json
from ai_dev_system.task_graph.skeleton import build_skeleton
from ai_dev_system.task_graph.enricher import enrich_task, enrich_all, ENRICHABLE_FIELDS


class MockLLM:
    def __init__(self, response: str):
        self.response = response
        self.calls = []

    def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        return self.response


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
