"""Suggester unit tests — Stub LLM, no network."""
from __future__ import annotations

import json

import pytest

from ai_dev_system.intake.engine import FieldAnswer, new_state, step
from ai_dev_system.intake.suggest import (
    SuggestParseError,
    SuggestRefusedError,
    Suggester,
    _stable_hash_brief,
)
from ai_dev_system.intake.template import load_template


# ---------------------------------------------------------------------------
# Stub LLM
# ---------------------------------------------------------------------------

class StubLLM:
    """Returns whatever JSON it's preloaded with. Records last (system, user)."""
    def __init__(self, replies: list[str] | None = None):
        self.replies = list(replies or [])
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if not self.replies:
            raise AssertionError("StubLLM out of replies")
        return self.replies.pop(0)


@pytest.fixture
def tpl():
    return load_template("generic_v1")


# ---------------------------------------------------------------------------
# Basic propose() flow
# ---------------------------------------------------------------------------

def test_propose_refuses_when_template_disallows(tpl):
    """problem_statement has ai_can_suggest: false → SuggestRefusedError."""
    sug = Suggester(StubLLM([]))
    with pytest.raises(SuggestRefusedError):
        sug.propose(tpl, tpl.field_by_id("problem_statement"), {})


def test_propose_text_field_parses_and_returns_proposal(tpl):
    llm = StubLLM(['{"suggestion": "Azure AD SSO", "rationale": "Phổ biến và đã có"}'])
    sug = Suggester(llm)
    p = sug.propose(tpl, tpl.field_by_id("existing_auth"), {})
    assert p.suggestion == "Azure AD SSO"
    assert "Phổ biến" in p.rationale
    assert p.cache_hit is False


def test_propose_list_str_accepts_json_array(tpl):
    llm = StubLLM(['{"suggestion": ["en", "vi"], "rationale": "Hai ngôn ngữ chính"}'])
    sug = Suggester(llm)
    p = sug.propose(tpl, tpl.field_by_id("user_languages"), {})
    assert p.suggestion == ["en", "vi"]


def test_propose_list_str_tolerates_comma_string(tpl):
    """Some LLMs return 'a, b' for a list field; we coerce."""
    llm = StubLLM(['{"suggestion": "en, vi", "rationale": "..."}'])
    sug = Suggester(llm)
    p = sug.propose(tpl, tpl.field_by_id("user_languages"), {})
    assert p.suggestion == ["en", "vi"]


def test_propose_enum_validates_options(tpl):
    llm = StubLLM(['{"suggestion": "greenfield", "rationale": "Mới"}'])
    sug = Suggester(llm)
    p = sug.propose(tpl, tpl.field_by_id("greenfield_or_brownfield"), {})
    assert p.suggestion == "greenfield"


def test_propose_enum_rejects_unknown_option(tpl):
    llm = StubLLM(['{"suggestion": "purple", "rationale": "..."}'])
    sug = Suggester(llm)
    with pytest.raises(SuggestParseError, match="not in options"):
        sug.propose(tpl, tpl.field_by_id("greenfield_or_brownfield"), {})


def test_propose_handles_null_suggestion(tpl):
    """LLM may say 'I cannot suggest' → suggestion=null, still valid."""
    llm = StubLLM(['{"suggestion": null, "rationale": "Cần thêm context"}'])
    sug = Suggester(llm)
    p = sug.propose(tpl, tpl.field_by_id("existing_auth"), {})
    assert p.suggestion is None


def test_propose_strips_markdown_fence(tpl):
    llm = StubLLM(['```json\n{"suggestion": "x", "rationale": "y"}\n```'])
    sug = Suggester(llm)
    p = sug.propose(tpl, tpl.field_by_id("existing_auth"), {})
    assert p.suggestion == "x"


def test_propose_rejects_missing_keys(tpl):
    llm = StubLLM(['{"suggestion": "x"}'])  # no rationale
    sug = Suggester(llm)
    with pytest.raises(SuggestParseError, match="missing keys"):
        sug.propose(tpl, tpl.field_by_id("existing_auth"), {})


def test_propose_rejects_non_json(tpl):
    llm = StubLLM(["I think Azure AD is good."])
    sug = Suggester(llm)
    with pytest.raises(SuggestParseError, match="non-JSON"):
        sug.propose(tpl, tpl.field_by_id("existing_auth"), {})


# ---------------------------------------------------------------------------
# Cache behavior (decision #1)
# ---------------------------------------------------------------------------

def test_cache_hit_skips_llm_call(tpl):
    llm = StubLLM(['{"suggestion": "Azure AD", "rationale": "phổ biến"}'])
    sug = Suggester(llm)
    fld = tpl.field_by_id("existing_auth")
    p1 = sug.propose(tpl, fld, {})
    p2 = sug.propose(tpl, fld, {})
    assert len(llm.calls) == 1
    assert p1.cache_hit is False
    assert p2.cache_hit is True
    assert p2.suggestion == p1.suggestion


def test_cache_invalidates_when_brief_changes(tpl):
    llm = StubLLM([
        '{"suggestion": "Azure AD", "rationale": "trống brief"}',
        '{"suggestion": "Okta",     "rationale": "có thông tin compliance"}',
    ])
    sug = Suggester(llm)
    fld = tpl.field_by_id("existing_auth")

    # First call with empty brief
    sug.propose(tpl, fld, {})

    # Add an answer → hash changes → new LLM call expected
    answers = {"compliance": FieldAnswer(value=["SOC2"], source="user")}
    p2 = sug.propose(tpl, fld, answers)
    assert len(llm.calls) == 2
    assert p2.suggestion == "Okta"
    assert p2.cache_hit is False


def test_stable_hash_changes_with_value():
    a = {"x": FieldAnswer(value="one", source="user")}
    b = {"x": FieldAnswer(value="two", source="user")}
    assert _stable_hash_brief(a) != _stable_hash_brief(b)


def test_stable_hash_stable_for_same_input():
    a = {"x": FieldAnswer(value="one", source="user")}
    a_copy = {"x": FieldAnswer(value="one", source="user")}
    assert _stable_hash_brief(a) == _stable_hash_brief(a_copy)


# ---------------------------------------------------------------------------
# Prompt content
# ---------------------------------------------------------------------------

def test_prompt_includes_dependency_fields(tpl):
    llm = StubLLM(['{"suggestion": "VN", "rationale": "user ở VN"}'])
    sug = Suggester(llm)
    answers = {
        "compliance": FieldAnswer(value=["VN-decree-13"], source="user"),
        "primary_user": FieldAnswer(value="VN bank employees", source="user"),
    }
    sug.propose(tpl, tpl.field_by_id("data_residency"), answers)
    (_, user_msg) = llm.calls[0]
    # Dep fields for data_residency include compliance + primary_user
    assert "compliance" in user_msg
    assert "VN-decree-13" in user_msg
    assert "primary_user" in user_msg


def test_prompt_skips_unanswered_deps(tpl):
    llm = StubLLM(['{"suggestion": "AWS", "rationale": "..."}'])
    sug = Suggester(llm)
    sug.propose(tpl, tpl.field_by_id("deployment_target"), {})
    (_, user_msg) = llm.calls[0]
    assert "chưa có field nào liên quan" in user_msg


# ---------------------------------------------------------------------------
# Engine integration: `?` → SUGGESTING → accept/reject
# ---------------------------------------------------------------------------

def _suggest_fn_factory(replies: dict[str, dict]):
    """Build a SuggestFn that returns a canned proposal per field id."""
    def fn(fld, answers):
        return replies[fld.id]
    return fn


def test_engine_question_mark_on_refused_field_stays(tpl):
    """`?` on problem_statement (ai_can_suggest=false) → reprompt with refuse."""
    state = new_state(tpl, "r1", "p1")
    # First field is problem_statement (text_long, critical, ai_can_suggest=false)
    assert tpl.fields[0].id == "problem_statement"
    result = step(tpl, state, "?", suggest_fn=_suggest_fn_factory({}))
    assert result.error == "ai_cannot_suggest"
    assert state.field_idx == 0
    assert state.stage == "ASKING"


def test_engine_question_mark_with_no_suggest_fn(tpl):
    state = new_state(tpl, "r1", "p1")
    # Move to a field where suggest IS allowed
    state.field_idx = tpl.field_index("deployment_target")
    result = step(tpl, state, "?", suggest_fn=None)
    assert result.error == "no_suggest_fn"
    assert state.stage == "ASKING"


def test_engine_question_mark_enters_suggesting(tpl):
    state = new_state(tpl, "r1", "p1")
    state.field_idx = tpl.field_index("deployment_target")
    fn = _suggest_fn_factory({
        "deployment_target": {"suggestion": "AWS", "rationale": "ECS hợp lý"},
    })
    result = step(tpl, state, "?", suggest_fn=fn)
    assert state.stage == "SUGGESTING"
    assert state.pending_suggestion["suggestion"] == "AWS"
    assert "AWS" in result.prompt
    assert "ECS hợp lý" in result.prompt
    assert result.suggest_called is True


def test_engine_suggest_accept_a_records_ai_confirmed(tpl):
    state = new_state(tpl, "r1", "p1")
    state.field_idx = tpl.field_index("deployment_target")
    fn = _suggest_fn_factory({"deployment_target": {"suggestion": "AWS", "rationale": "x"}})
    step(tpl, state, "?", suggest_fn=fn)
    assert state.stage == "SUGGESTING"

    result = step(tpl, state, "a", suggest_fn=fn)
    ans = state.answers["deployment_target"]
    assert ans.value == "AWS"
    assert ans.source == "ai_suggested_confirmed"
    assert ans.rationale == "x"
    assert state.stage == "ASKING"
    assert state.pending_suggestion is None


def test_engine_suggest_reject_c_marks_skipped(tpl):
    state = new_state(tpl, "r1", "p1")
    state.field_idx = tpl.field_index("deployment_target")
    fn = _suggest_fn_factory({"deployment_target": {"suggestion": "AWS", "rationale": "x"}})
    step(tpl, state, "?", suggest_fn=fn)
    step(tpl, state, "c", suggest_fn=fn)
    ans = state.answers["deployment_target"]
    assert ans.source == "skipped"
    assert state.stage == "ASKING"


def test_engine_suggest_replace_b_text_records_user(tpl):
    state = new_state(tpl, "r1", "p1")
    state.field_idx = tpl.field_index("deployment_target")
    fn = _suggest_fn_factory({"deployment_target": {"suggestion": "AWS", "rationale": "x"}})
    step(tpl, state, "?", suggest_fn=fn)
    step(tpl, state, "b on-prem k8s", suggest_fn=fn)
    ans = state.answers["deployment_target"]
    assert ans.value == "on-prem k8s"
    assert ans.source == "user"


def test_engine_suggest_back_cancels(tpl):
    state = new_state(tpl, "r1", "p1")
    state.field_idx = tpl.field_index("deployment_target")
    fn = _suggest_fn_factory({"deployment_target": {"suggestion": "AWS", "rationale": "x"}})
    step(tpl, state, "?", suggest_fn=fn)
    assert state.stage == "SUGGESTING"
    result = step(tpl, state, "back", suggest_fn=fn)
    assert state.stage == "ASKING"
    assert state.pending_suggestion is None
    assert state.field_idx == tpl.field_index("deployment_target")  # same field


def test_engine_suggest_null_value_cant_accept(tpl):
    """If LLM proposed null, `a` is rejected — user must `b <text>` or `c`."""
    state = new_state(tpl, "r1", "p1")
    state.field_idx = tpl.field_index("deployment_target")
    fn = _suggest_fn_factory({"deployment_target": {"suggestion": None, "rationale": "thiếu context"}})
    step(tpl, state, "?", suggest_fn=fn)
    result = step(tpl, state, "a", suggest_fn=fn)
    assert result.error == "suggestion_was_null"
    assert state.stage == "SUGGESTING"


def test_engine_suggest_regenerate_with_question_mark(tpl):
    """Typing `?` again while SUGGESTING re-calls suggest_fn."""
    calls = []

    def fn(fld, answers):
        calls.append(fld.id)
        return {"suggestion": f"call-{len(calls)}", "rationale": "x"}

    state = new_state(tpl, "r1", "p1")
    state.field_idx = tpl.field_index("deployment_target")
    step(tpl, state, "?", suggest_fn=fn)
    step(tpl, state, "?", suggest_fn=fn)
    assert len(calls) == 2
    assert state.pending_suggestion["suggestion"] == "call-2"


def test_engine_suggest_fn_exception_falls_back(tpl):
    """LLM error → stays in ASKING with helpful message, no crash."""
    def broken(fld, answers):
        raise RuntimeError("API down")

    state = new_state(tpl, "r1", "p1")
    state.field_idx = tpl.field_index("deployment_target")
    result = step(tpl, state, "?", suggest_fn=broken)
    assert result.error == "suggest_failed"
    assert state.stage == "ASKING"
    assert "API down" in result.prompt
