# tests/unit/agents/test_test_author_agent.py
from ai_dev_system.agents.test_author_agent import (
    build_test_source, _build_test_prompt, _build_test_fix_prompt,
)


def _ctx(objective="Add login", facets=None, acceptance_criteria=None):
    c = {
        "task_id": "TASK-TEST",
        "objective": objective,
        "description": "Implement JWT login",
        "done_definition": "Failing tests committed",
        "facets": facets or {},
    }
    if acceptance_criteria is not None:
        c["acceptance_criteria"] = acceptance_criteria
    return c


def test_source_includes_filled_test_cases_facet():
    src = build_test_source(_ctx(facets={
        "test_cases": {"status": "filled", "content": "401 on bad creds", "reason": ""},
        "input": {"status": "na", "content": "", "reason": "x"},
    }))
    assert "401 on bad creds" in src
    assert "x" not in src  # na facet excluded


def test_source_includes_acceptance_criteria_when_present():
    src = build_test_source(_ctx(acceptance_criteria="AC-1: returns JWT"))
    assert "AC-1: returns JWT" in src


def test_source_empty_when_nothing_filled():
    src = build_test_source(_ctx())
    assert src.strip() != ""  # returns a stable placeholder, never empty string


def test_prompt_says_tests_only_and_must_fail():
    p = _build_test_prompt(_ctx(facets={
        "test_cases": {"status": "filled", "content": "401 on bad creds", "reason": ""}}))
    assert "401 on bad creds" in p
    low = p.lower()
    assert "do not" in low and "implement" in low      # tests only, no implementation
    assert "fail" in low or "red" in low               # tests must be red


def test_fix_prompt_lists_findings():
    p = _build_test_fix_prompt("Add login",
                               [{"severity": "high", "file": "t.py", "line": 3, "issue": "AC-2 missing"}],
                               tests_red=True)
    assert "AC-2 missing" in p
