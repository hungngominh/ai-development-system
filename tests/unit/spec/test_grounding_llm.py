"""Unit tests for SP7 — LLM G5 hallucination check (llm_grounding_check).

Tests cover:
- Normal path: LLM returns JSON with violations → mapped to GroundingViolations
- LLM returns empty violations list → empty dict returned
- LLM returns invalid JSON → empty dict (non-blocking)
- LLM raises exception → empty dict (non-blocking)
- Violations are keyed by section name
- Violation has rule="hallucination" and severity="error"
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from ai_dev_system.spec.grounding import llm_grounding_check


def _stub_llm(response: str) -> MagicMock:
    client = MagicMock()
    client.complete.return_value = response
    return client


def _llm_resp(violations: list) -> str:
    return json.dumps({"violations": violations})


_BRIEF = {"problem_statement": "Build a forum", "scope_in": ["voting", "comments"]}
_DECISIONS = [{"rationale": "Use PostgreSQL"}]
_SECTIONS = {"functional": "Users can vote. Redis cache at 10ms.", "proposal": "Forum app."}


def test_normal_violation_returned():
    llm = _stub_llm(_llm_resp([
        {"section": "functional", "claim": "Redis cache at 10ms", "issue": "Redis not in brief"}
    ]))
    result = llm_grounding_check(_SECTIONS, _BRIEF, _DECISIONS, llm)
    assert "functional" in result
    viols = result["functional"]
    assert len(viols) == 1
    assert viols[0].rule == "hallucination"
    assert viols[0].severity == "error"
    assert "Redis cache at 10ms" in viols[0].message


def test_empty_violations_returns_empty_dict():
    llm = _stub_llm(_llm_resp([]))
    result = llm_grounding_check(_SECTIONS, _BRIEF, _DECISIONS, llm)
    assert result == {}


def test_invalid_json_returns_empty_dict():
    llm = _stub_llm("not json at all")
    result = llm_grounding_check(_SECTIONS, _BRIEF, _DECISIONS, llm)
    assert result == {}


def test_llm_raises_returns_empty_dict():
    llm = MagicMock()
    llm.complete.side_effect = RuntimeError("LLM down")
    result = llm_grounding_check(_SECTIONS, _BRIEF, _DECISIONS, llm)
    assert result == {}


def test_multiple_violations_different_sections():
    llm = _stub_llm(_llm_resp([
        {"section": "functional", "claim": "Redis", "issue": "not in brief"},
        {"section": "proposal", "claim": "AWS Lambda", "issue": "not in decisions"},
        {"section": "functional", "claim": "10ms SLA", "issue": "no number in brief"},
    ]))
    result = llm_grounding_check(_SECTIONS, _BRIEF, _DECISIONS, llm)
    assert len(result["functional"]) == 2
    assert len(result["proposal"]) == 1


def test_missing_section_key_uses_unknown():
    llm = _stub_llm(_llm_resp([
        {"claim": "something", "issue": "bad"}
    ]))
    # Missing "section" key → defaults to "unknown"
    result = llm_grounding_check(_SECTIONS, _BRIEF, _DECISIONS, llm)
    assert "unknown" in result


def test_llm_called_with_system_prompt():
    llm = _stub_llm(_llm_resp([]))
    llm_grounding_check(_SECTIONS, _BRIEF, _DECISIONS, llm)
    assert llm.complete.called
    call_kwargs = llm.complete.call_args
    system_arg = call_kwargs[1].get("system") or call_kwargs[0][0]
    assert "hallucination" in system_arg.lower() or "auditing" in system_arg.lower()


def test_empty_decisions_handled():
    llm = _stub_llm(_llm_resp([]))
    result = llm_grounding_check(_SECTIONS, _BRIEF, [], llm)
    assert result == {}


def test_violation_message_contains_claim_and_issue():
    llm = _stub_llm(_llm_resp([
        {"section": "design", "claim": "Kubernetes cluster", "issue": "deployment target is Docker only"}
    ]))
    result = llm_grounding_check(_SECTIONS, _BRIEF, _DECISIONS, llm)
    msg = result["design"][0].message
    assert "Kubernetes cluster" in msg
    assert "Docker only" in msg
