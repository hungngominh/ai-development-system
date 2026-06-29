# tests/unit/agents/test_test_author_agent.py
from unittest.mock import patch

from ai_dev_system.agents.test_author_agent import (
    build_test_source, _build_test_prompt, _build_test_fix_prompt,
    TestAuthorAgent,
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


# ── TestAuthorAgent.run failure-return ────────────────────────────────────────

def test_run_returns_error_when_review_stays_blocking_after_budget(tmp_path, monkeypatch):
    """TestAuthorAgent.run must return AgentResult with non-None error when the
    test review stays blocking after the repair budget is exhausted.

    Mechanism: initial claude run succeeds; TestReviewAgent.review always returns
    a blocking verdict (fail, tests_red=False); EXEC_TEST_REVIEW_MAX_ROUNDS=0 so
    there is zero repair budget — the flagged status propagates immediately.
    """
    from ai_dev_system.agents.repo_branch_agent import _ClaudeRun
    from ai_dev_system.agents.test_review_agent import TestReviewVerdict

    monkeypatch.setenv("EXEC_TEST_REVIEW_MAX_ROUNDS", "0")

    # Successful initial claude invocation (writes tests, returns code 0)
    ok_run = _ClaudeRun(
        returncode=0, stdout='{"type":"result","result":"tests committed"}',
        stderr="", result_event={"type": "result", "result": "tests committed"},
        subtype="success",
    )
    # Blocking test-review verdict every time it is called
    blocking_verdict = TestReviewVerdict(verdict="fail", tests_red=False, findings=[])

    with patch("ai_dev_system.agents.test_author_agent._invoke_claude", return_value=ok_run), \
         patch("ai_dev_system.agents.test_review_agent.TestReviewAgent.review",
               return_value=blocking_verdict), \
         patch("ai_dev_system.agents.test_author_agent._git") as mock_git, \
         patch("ai_dev_system.llm_factory.ClaudeCodeLLMClient._resolve_claude_cmd",
               return_value="claude"):
        mock_git.return_value = type("P", (), {"stdout": "(no diff)", "returncode": 0})()
        agent = TestAuthorAgent(str(tmp_path), "ai-dev/task-x", "main")
        result = agent.run("TASK-TEST", str(tmp_path / "out"), context=_ctx())

    assert result.success is False
    assert result.error is not None
    assert "flagged" in result.error.lower() or "review" in result.error.lower()


def test_review_repair_fix_round_resolves_model_effort(tmp_path, monkeypatch):
    """Regression: _review_and_repair referenced model/effort that were only
    defined in run(), so a fix round (max_rounds>=1 + blocking review) raised
    NameError. The fix round must actually run and forward model/effort to
    _invoke_claude — never raise."""
    from ai_dev_system.agents.repo_branch_agent import _ClaudeRun
    from ai_dev_system.agents.test_review_agent import TestReviewVerdict

    monkeypatch.setenv("EXEC_TEST_REVIEW_MAX_ROUNDS", "1")

    ok_run = _ClaudeRun(
        returncode=0, stdout='{"type":"result","result":"ok"}', stderr="",
        result_event={"type": "result", "result": "ok"}, subtype="success",
    )
    blocking = TestReviewVerdict(verdict="fail", tests_red=False, findings=[])
    calls = []

    def _capture(*args, **kwargs):
        calls.append(kwargs)
        return ok_run

    with patch("ai_dev_system.agents.test_author_agent._invoke_claude", side_effect=_capture), \
         patch("ai_dev_system.agents.test_review_agent.TestReviewAgent.review",
               return_value=blocking), \
         patch("ai_dev_system.agents.test_author_agent._git") as mock_git, \
         patch("ai_dev_system.llm_factory.ClaudeCodeLLMClient._resolve_claude_cmd",
               return_value="claude"):
        mock_git.return_value = type("P", (), {"stdout": "(no diff)", "returncode": 0})()
        agent = TestAuthorAgent(str(tmp_path), "ai-dev/task-x", "main")
        result = agent.run("TASK-TEST", str(tmp_path / "out"), context=_ctx())

    # Initial run + exactly one fix round both reached _invoke_claude (no NameError).
    assert len(calls) == 2
    # The fix round forwarded model/effort (the bug: these were undefined here).
    assert "model" in calls[1] and "effort" in calls[1]
    # Review stayed blocking after the single repair round → flagged.
    assert result.success is False
