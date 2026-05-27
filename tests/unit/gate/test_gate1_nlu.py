"""Unit tests for Gate 1 NLU fallback (G9).

Tests cover:
- llm_parse returns ParseResult with correct action_type from LLM JSON
- llm_parse handles LLM returning invalid JSON → unknown
- llm_parse handles LLM raising exception → unknown
- llm_parse handles unknown action_type from LLM → unknown
- llm_parse normalises QID target to uppercase
- parse_user_input uses regex path when llm_client is None (no regression)
- parse_user_input calls llm_parse when regex doesn't match + llm_client provided
- parse_user_input does NOT call llm_client when regex already matches
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from ai_dev_system.gate.gate1_review.nlu import llm_parse
from ai_dev_system.gate.gate1_review.parser import ParseResult, parse_user_input


# ---- helpers ----


def _stub_llm(response: str) -> MagicMock:
    client = MagicMock()
    client.complete.return_value = response
    return client


def _llm_json(**kwargs) -> str:
    base = {"action_type": "unknown", "target": None, "choice": None, "payload": None}
    base.update(kwargs)
    return json.dumps(base)


# ---- llm_parse direct ----


def test_llm_parse_answer_agent_a():
    llm = _stub_llm(_llm_json(action_type="answer", target="Q1", choice="agent_a"))
    result = llm_parse("Q1 tôi đồng ý với A", llm)
    assert result.action_type == "answer"
    assert result.target == "Q1"
    assert result.choice == "agent_a"


def test_llm_parse_confirm():
    llm = _stub_llm(_llm_json(action_type="confirm"))
    result = llm_parse("Xong rồi, ghi lại đi", llm)
    assert result.action_type == "confirm"


def test_llm_parse_abort():
    llm = _stub_llm(_llm_json(action_type="abort"))
    result = llm_parse("thôi bỏ đi", llm)
    assert result.action_type == "abort"


def test_llm_parse_edit_brief():
    llm = _stub_llm(_llm_json(action_type="edit_brief", target="scope_in", payload="reporting"))
    result = llm_parse("thêm reporting vào scope_in", llm)
    assert result.action_type == "edit_brief"
    assert result.payload == "reporting"


def test_llm_parse_override_with_payload():
    llm = _stub_llm(_llm_json(action_type="answer", target="Q3", choice="override", payload="Use Redis"))
    result = llm_parse("Q3 tôi muốn dùng Redis thay vì Kafka", llm)
    assert result.choice == "override"
    assert result.payload == "Use Redis"


def test_llm_parse_target_uppercased():
    llm = _stub_llm(_llm_json(action_type="answer", target="q5", choice="moderator"))
    result = llm_parse("q5 ok với moderator", llm)
    assert result.target == "Q5"


def test_llm_parse_invalid_json_returns_unknown():
    llm = _stub_llm("not json at all")
    result = llm_parse("gibberish", llm)
    assert result.action_type == "unknown"
    assert not result.accepted


def test_llm_parse_llm_raises_returns_unknown():
    llm = MagicMock()
    llm.complete.side_effect = RuntimeError("LLM down")
    result = llm_parse("some text", llm)
    assert result.action_type == "unknown"
    assert not result.accepted


def test_llm_parse_invalid_action_type_returns_unknown():
    llm = _stub_llm(_llm_json(action_type="do_something_weird"))
    result = llm_parse("something", llm)
    assert result.action_type == "unknown"


def test_llm_parse_unknown_from_llm_returns_unknown():
    llm = _stub_llm(_llm_json(action_type="unknown"))
    result = llm_parse("???", llm)
    assert result.action_type == "unknown"
    assert not result.accepted


def test_llm_parse_invalid_choice_set_to_none():
    llm = _stub_llm(_llm_json(action_type="answer", target="Q1", choice="bad_choice"))
    result = llm_parse("something", llm)
    # Invalid choice → set to None but action_type passes through as answer
    assert result.action_type == "answer"
    assert result.choice is None


# ---- parse_user_input integration ----


def test_parse_no_llm_returns_unknown_on_no_match():
    result = parse_user_input("???totally ambiguous???")
    assert result.action_type == "unknown"
    assert not result.accepted


def test_parse_with_llm_calls_nlu_on_no_match():
    llm = _stub_llm(_llm_json(action_type="confirm"))
    result = parse_user_input("ghi artifacts đi", llm_client=llm)
    assert result.action_type == "confirm"
    assert llm.complete.called


def test_parse_with_llm_skips_nlu_when_regex_matches():
    llm = MagicMock()
    result = parse_user_input("confirm", llm_client=llm)
    assert result.action_type == "confirm"
    assert not llm.complete.called  # regex matched — no LLM call


def test_parse_with_llm_skips_nlu_for_agent_a_choice():
    llm = MagicMock()
    result = parse_user_input("Q1 chọn A", llm_client=llm)
    assert result.action_type == "answer"
    assert result.choice == "agent_a"
    assert not llm.complete.called
