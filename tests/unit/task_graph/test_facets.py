import json

from ai_dev_system.task_graph.facets import (
    FACET_KEYS,
    is_implementation_task,
    generate_task_facets,
    generate_task_facets_for_graph,
)


class _FakeLLM:
    """complete(system, user) -> fixed response; records the system prompt."""
    def __init__(self, response: str):
        self.response = response
        self.system_seen = None
    def complete(self, system: str, user: str) -> str:
        self.system_seen = system
        return self.response


class _RaisingLLM:
    def complete(self, system: str, user: str) -> str:
        raise RuntimeError("llm down")


def _impl_task(tid="TASK-IMPL"):
    return {"id": tid, "execution_type": "atomic", "type": "coding",
            "objective": "build the thing", "description": "...",
            "required_inputs": ["design_doc"], "expected_outputs": ["implementation"]}


def _all_filled_response():
    return json.dumps({k: {"status": "filled", "content": f"{k} detail", "reason": ""}
                       for k in FACET_KEYS})


def test_is_implementation_task_only_coding_atomic():
    assert is_implementation_task(_impl_task()) is True
    assert is_implementation_task({"execution_type": "atomic", "type": "design"}) is False
    assert is_implementation_task({"execution_type": "composite", "type": "coding"}) is False


def test_generate_returns_all_eight_facets_filled():
    facets = generate_task_facets(_impl_task(), {"functional.md": "f"}, None, _FakeLLM(_all_filled_response()))
    assert set(facets.keys()) == set(FACET_KEYS)
    assert list(facets.keys()) == list(FACET_KEYS)
    assert facets["database"]["status"] == "filled"
    assert facets["database"]["content"] == "database detail"


def test_generate_na_and_needs_human_pass_through():
    resp = json.dumps({
        **{k: {"status": "filled", "content": "x", "reason": ""} for k in FACET_KEYS},
        "database": {"status": "na", "content": "", "reason": "no persistence"},
        "auth_permission": {"status": "needs_human", "content": "", "reason": ""},
    })
    facets = generate_task_facets(_impl_task(), {}, None, _FakeLLM(resp))
    assert facets["database"] == {"status": "na", "content": "", "reason": "no persistence"}
    assert facets["auth_permission"]["status"] == "needs_human"


def test_generate_resilient_on_llm_error_all_needs_human():
    facets = generate_task_facets(_impl_task(), {}, None, _RaisingLLM())
    assert set(facets.keys()) == set(FACET_KEYS)
    assert all(facets[k]["status"] == "needs_human" for k in FACET_KEYS)


def test_generate_resilient_on_non_json():
    facets = generate_task_facets(_impl_task(), {}, None, _FakeLLM("not json"))
    assert all(facets[k]["status"] == "needs_human" for k in FACET_KEYS)


def test_missing_key_in_response_becomes_needs_human():
    resp = json.dumps({"input": {"status": "filled", "content": "c", "reason": ""}})
    facets = generate_task_facets(_impl_task(), {}, None, _FakeLLM(resp))
    assert facets["input"]["status"] == "filled"
    assert facets["response"]["status"] == "needs_human"  # absent → needs_human


def test_system_prompt_avoids_stub_router_substrings():
    llm = _FakeLLM(_all_filled_response())
    generate_task_facets(_impl_task(), {}, None, llm)
    low = llm.system_seen.lower()
    for banned in ("question", "generate", "moderator", "synthesis", "finalize", "spec"):
        assert banned not in low, f"system prompt must avoid {banned!r}"


def test_for_graph_only_attaches_to_impl_tasks():
    tasks = [
        _impl_task("TASK-IMPL"),
        {"id": "TASK-DESIGN", "execution_type": "atomic", "type": "design"},
    ]
    generate_task_facets_for_graph(tasks, {}, None, _FakeLLM(_all_filled_response()))
    assert "facets" in tasks[0]
    assert "facets" not in tasks[1]


def test_invalid_status_coerces_to_needs_human():
    resp = json.dumps({
        **{k: {"status": "filled", "content": "x", "reason": ""} for k in FACET_KEYS},
        "input": {"status": "bogus", "content": "x", "reason": ""},
    })
    facets = generate_task_facets(_impl_task(), {}, None, _FakeLLM(resp))
    assert facets["input"] == {"status": "needs_human", "content": "", "reason": ""}


def test_for_graph_kill_switch(monkeypatch):
    monkeypatch.setenv("AI_DEV_DISABLE_TASK_FACETS", "1")
    tasks = [_impl_task()]
    generate_task_facets_for_graph(tasks, {}, None, _FakeLLM(_all_filled_response()))
    assert "facets" not in tasks[0]
