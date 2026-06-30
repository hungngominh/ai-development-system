# tests/unit/task_graph/test_clarify_questions.py
from ai_dev_system.task_graph.clarify_questions import (
    find_blocking, synthesize_questions, format_questions,
)


def test_find_blocking_selects_error_findings_and_needs_human_facets():
    spec = {
        "findings": [
            {"section": "business_rule", "severity": "error", "message": "GUID vs PK?"},
            {"section": "test_cases", "severity": "warning", "message": "minor"},
        ],
        "facets": {
            "security_rules": {"status": "needs_human", "content": "enumeration risk"},
            "input": {"status": "filled", "content": "ok"},
        },
    }
    blocking = find_blocking(spec)
    keys = {(b["kind"], b["key"]) for b in blocking}
    assert ("finding", "business_rule") in keys
    assert ("facet", "security_rules") in keys
    assert ("finding", "test_cases") not in keys      # warning excluded
    assert ("facet", "input") not in keys             # filled excluded


def test_find_blocking_empty_when_clean():
    assert find_blocking({"findings": [], "facets": {"input": {"status": "filled"}}}) == []
    assert find_blocking({}) == []                     # missing keys tolerated


def test_synthesize_questions_uses_llm_json_array():
    class FakeLLM:
        def complete(self, *, system, user):
            return '```json\n["OwnerId nên là GUID thật hay ID số?"]\n```'
    qs = synthesize_questions(
        [{"kind": "finding", "key": "business_rule", "message": "GUID vs PK"}],
        idea="add OwnerId", llm=FakeLLM(),
    )
    assert qs == ["OwnerId nên là GUID thật hay ID số?"]


def test_synthesize_questions_falls_back_on_llm_error():
    class BoomLLM:
        def complete(self, *, system, user):
            raise RuntimeError("llm down")
    qs = synthesize_questions(
        [{"kind": "finding", "key": "business_rule", "message": "GUID vs PK contradiction"}],
        idea="x", llm=BoomLLM(),
    )
    assert qs and "GUID vs PK contradiction" in qs[0]


def test_synthesize_questions_fallback_when_llm_none():
    qs = synthesize_questions(
        [{"kind": "facet", "key": "security_rules", "message": "enumeration risk"}],
        idea="x", llm=None,
    )
    assert qs == ["enumeration risk"]


def test_synthesize_questions_empty_for_no_blocking():
    assert synthesize_questions([], idea="x", llm=None) == []


def test_format_questions_numbers_and_frames():
    out = format_questions(["A?", "B?"])
    assert "1. A?" in out and "2. B?" in out
