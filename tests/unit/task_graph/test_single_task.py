import json

from ai_dev_system.task_graph.single_task import build_single_task, spec_single_task
from ai_dev_system.task_graph.facets import FACET_KEYS, is_implementation_task
from ai_dev_system.debate.llm import StubDebateLLMClient


class _FakeLLM:
    def __init__(self, response): self.response = response
    def complete(self, system, user): return self.response


def _all_filled():
    return json.dumps({k: {"status": "filled", "content": f"{k} c", "reason": ""} for k in FACET_KEYS})


def test_build_single_task_is_minimal_coding_task():
    t = build_single_task("build a CSV importer")
    assert t["type"] == "coding" and t["execution_type"] == "atomic"
    assert t["objective"] == "build a CSV importer"
    assert is_implementation_task(t) is True


def test_build_single_task_title_derives_from_idea_when_absent():
    t = build_single_task("build a CSV importer")
    assert t["title"]  # non-empty
    t2 = build_single_task("x", title="My Task")
    assert t2["title"] == "My Task"


def test_spec_single_task_returns_task_and_eight_facets():
    result = spec_single_task("build a CSV importer", _FakeLLM(_all_filled()))
    assert set(result["facets"].keys()) == set(FACET_KEYS)
    assert result["task"]["facets"]["database"]["status"] == "filled"
    assert result["task"]["objective"] == "build a CSV importer"


def test_spec_single_task_stub_yields_all_needs_human():
    result = spec_single_task("build a CSV importer", StubDebateLLMClient())
    assert all(result["facets"][k]["status"] == "needs_human" for k in FACET_KEYS)
