"""Tests for ReviewAgent (executor review gate reviewer)."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from ai_dev_system.agents.repo_branch_agent import _ClaudeRun
from ai_dev_system.agents.review_agent import (
    ReviewAgent,
    ReviewVerdict,
    _build_review_prompt,
    _parse_verdict,
)


# ── _build_review_prompt ──────────────────────────────────────────────────────

def test_review_prompt_includes_weakening_check_when_test_spec_given():
    p = _build_review_prompt("main", "Add login", test_spec="AC-1: returns 401")
    assert "AC-1: returns 401" in p
    assert "weaken" in p.lower()


def test_review_prompt_omits_weakening_block_without_test_spec():
    p = _build_review_prompt("main", "Add login", test_spec="")
    assert "AC-1" not in p
    assert "Test integrity" not in p
    assert "weaken" not in p.lower()


# ── _parse_verdict ──────────────────────────────────────────────────────────────

def test_parse_clean_json():
    raw = json.dumps({"verdict": "pass", "tests_ran": True, "tests_passed": True,
                      "findings": []})
    v = _parse_verdict(raw)
    assert v.verdict == "pass" and v.tests_passed and v.findings == []


def test_parse_tolerates_surrounding_prose():
    raw = ("Here is my review:\n```json\n"
           '{"verdict":"fail","tests_ran":true,"tests_passed":false,'
           '"findings":[{"severity":"high","file":"a.py","line":3,"issue":"x"}]}'
           "\n```\nDone.")
    v = _parse_verdict(raw)
    assert v.verdict == "fail"
    assert v.findings[0]["severity"] == "high"


@pytest.mark.parametrize("raw", ["", "not json at all", "{broken", "[1,2,3]"])
def test_parse_garbage_is_inconclusive(raw):
    assert _parse_verdict(raw).verdict == "inconclusive"


# ── is_blocking ───────────────────────────────────────────────────────────────

def test_inconclusive_never_blocks():
    assert ReviewVerdict(verdict="inconclusive").is_blocking() is False


def test_failed_tests_block():
    assert ReviewVerdict(verdict="pass", tests_ran=True, tests_passed=False).is_blocking()


def test_high_severity_finding_blocks_even_if_verdict_pass():
    v = ReviewVerdict(verdict="pass", tests_ran=True, tests_passed=True,
                      findings=[{"severity": "critical", "issue": "x"}])
    assert v.is_blocking()


def test_clean_pass_does_not_block():
    v = ReviewVerdict(verdict="pass", tests_ran=True, tests_passed=True, findings=[])
    assert v.is_blocking() is False


def test_no_tests_but_clean_does_not_block():
    # A repo with no test suite should not be blocked forever on tests_passed.
    v = ReviewVerdict(verdict="pass", tests_ran=False, tests_passed=False, findings=[])
    assert v.is_blocking() is False


# ── ReviewAgent.review ────────────────────────────────────────────────────────

def _run_with_result(result_text: str) -> _ClaudeRun:
    return _ClaudeRun(
        returncode=0, stdout="", stderr="",
        result_event={"type": "result", "result": result_text}, subtype="",
    )


def test_review_parses_verdict_from_claude(tmp_path):
    payload = json.dumps({"verdict": "pass", "tests_ran": True, "tests_passed": True,
                          "findings": []})
    with patch("ai_dev_system.agents.review_agent.ClaudeCodeLLMClient._resolve_claude_cmd",
               return_value="claude"), \
         patch("ai_dev_system.agents.review_agent._invoke_claude",
               return_value=_run_with_result(payload)):
        v = ReviewAgent(str(tmp_path), "main").review(objective="do x")
    assert v.verdict == "pass" and not v.is_blocking()


def test_review_timeout_is_inconclusive(tmp_path):
    timed = _ClaudeRun(returncode=-1, stdout="", stderr="", result_event=None,
                       subtype="", timed_out=True)
    with patch("ai_dev_system.agents.review_agent.ClaudeCodeLLMClient._resolve_claude_cmd",
               return_value="claude"), \
         patch("ai_dev_system.agents.review_agent._invoke_claude", return_value=timed):
        v = ReviewAgent(str(tmp_path), "main").review()
    assert v.verdict == "inconclusive" and v.is_blocking() is False


def test_review_no_claude_cli_is_inconclusive(tmp_path):
    with patch("ai_dev_system.agents.review_agent.ClaudeCodeLLMClient._resolve_claude_cmd",
               side_effect=RuntimeError("not found")):
        v = ReviewAgent(str(tmp_path), "main").review()
    assert v.verdict == "inconclusive"
