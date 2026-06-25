import json

from ai_dev_system.task_graph.facets import (
    FACET_KEYS,
    SPEC_FACET_KEYS,
    EXEC_FACET_KEYS,
    FACET_STAGE,
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
                       for k in SPEC_FACET_KEYS})


def test_is_implementation_task_only_coding_atomic():
    assert is_implementation_task(_impl_task()) is True
    assert is_implementation_task({"execution_type": "atomic", "type": "design"}) is False
    assert is_implementation_task({"execution_type": "composite", "type": "coding"}) is False


def test_generate_returns_all_facets_spec_filled_exec_na():
    facets = generate_task_facets(_impl_task(), {"functional.md": "f"}, None, _FakeLLM(_all_filled_response()))
    assert set(facets.keys()) == set(FACET_KEYS)
    assert list(facets.keys()) == list(FACET_KEYS)
    assert facets["database"]["status"] == "filled"
    assert facets["database"]["content"] == "database detail"
    for k in EXEC_FACET_KEYS:
        assert facets[k]["status"] == "na"
        assert "exec-time" in facets[k]["reason"]


def test_generate_na_and_needs_human_pass_through():
    resp = json.dumps({
        **{k: {"status": "filled", "content": "x", "reason": ""} for k in FACET_KEYS},
        "database": {"status": "na", "content": "", "reason": "no persistence"},
        "auth_permission": {"status": "needs_human", "content": "", "reason": ""},
    })
    facets = generate_task_facets(_impl_task(), {}, None, _FakeLLM(resp))
    assert facets["database"] == {"status": "na", "content": "", "reason": "no persistence"}
    assert facets["auth_permission"]["status"] == "needs_human"


def test_generate_resilient_on_llm_error_spec_needs_human_exec_na():
    facets = generate_task_facets(_impl_task(), {}, None, _RaisingLLM())
    assert set(facets.keys()) == set(FACET_KEYS)
    assert all(facets[k]["status"] == "needs_human" for k in SPEC_FACET_KEYS)
    assert all(facets[k]["status"] == "na" for k in EXEC_FACET_KEYS)


def test_generate_resilient_on_non_json():
    facets = generate_task_facets(_impl_task(), {}, None, _FakeLLM("not json"))
    assert all(facets[k]["status"] == "needs_human" for k in SPEC_FACET_KEYS)
    assert all(facets[k]["status"] == "na" for k in EXEC_FACET_KEYS)


def test_spec_exec_keys_disjoint_and_union_equals_facet_keys():
    assert set(SPEC_FACET_KEYS) & set(EXEC_FACET_KEYS) == set()
    assert set(SPEC_FACET_KEYS) | set(EXEC_FACET_KEYS) == set(FACET_KEYS)
    assert len(SPEC_FACET_KEYS) == 13
    assert len(EXEC_FACET_KEYS) == 7
    assert len(FACET_KEYS) == 20


def test_facet_stage_covers_all_keys():
    assert set(FACET_STAGE.keys()) == set(FACET_KEYS)
    assert all(FACET_STAGE[k] == "spec" for k in SPEC_FACET_KEYS)
    assert all(FACET_STAGE[k] == "exec" for k in EXEC_FACET_KEYS)


def test_exec_facets_always_na_after_generate():
    facets = generate_task_facets(_impl_task(), {}, None, _FakeLLM(_all_filled_response()))
    for k in EXEC_FACET_KEYS:
        assert facets[k] == {"status": "na", "content": "", "reason": "exec-time — fill after implementation"}


def test_all_facet_keys_have_definitions():
    from ai_dev_system.task_graph.facets import FACET_DEFINITIONS
    for k in FACET_KEYS:
        assert k in FACET_DEFINITIONS, f"missing definition for {k!r}"


def test_build_single_task_has_out_of_scope():
    from ai_dev_system.task_graph.single_task import build_single_task
    task = build_single_task("add CSV import")
    assert "out_of_scope" in task
    assert task["out_of_scope"] == ""


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


# ── Phase A: multi-perspective reasoning ──────────────────────────────────────

def _all_filled_with_reasoning():
    return json.dumps({
        k: {"status": "filled", "content": f"{k} detail", "reason": "",
            "reasoning": f"Dev: build it. QA: test it. Security: secure it. — {k}"}
        for k in SPEC_FACET_KEYS
    })


def test_generate_preserves_reasoning_field_when_llm_returns_it():
    facets = generate_task_facets(_impl_task(), {}, None, _FakeLLM(_all_filled_with_reasoning()))
    assert facets["input"]["reasoning"] == "Dev: build it. QA: test it. Security: secure it. — input"
    assert facets["database"]["reasoning"] == "Dev: build it. QA: test it. Security: secure it. — database"


def test_generate_reasoning_defaults_to_empty_string_when_absent():
    facets = generate_task_facets(_impl_task(), {}, None, _FakeLLM(_all_filled_response()))
    for k in FACET_KEYS:
        assert facets[k].get("reasoning", "") == ""


def test_needs_human_facet_has_empty_reasoning():
    facets = generate_task_facets(_impl_task(), {}, None, _RaisingLLM())
    for k in FACET_KEYS:
        assert facets[k].get("reasoning", "") == ""


def test_prompt_requests_multi_perspective_reasoning():
    from ai_dev_system.task_graph.facets import _build_facet_prompt
    system, _ = _build_facet_prompt(_impl_task(), {}, None)
    low = system.lower()
    assert "developer" in low or "qa" in low or "security" in low or "lenses" in low


def test_prompt_requests_reasoning_field_in_output():
    from ai_dev_system.task_graph.facets import _build_facet_prompt
    system, _ = _build_facet_prompt(_impl_task(), {}, None)
    assert "reasoning" in system
