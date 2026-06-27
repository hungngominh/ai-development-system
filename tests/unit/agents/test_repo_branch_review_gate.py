"""Review-gate behaviour inside RepoBranchAgent.run()."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from ai_dev_system.agents.repo_branch_agent import RepoBranchAgent, _ClaudeRun
from ai_dev_system.agents.review_agent import ReviewVerdict


def _ctx():
    return {"task_id": "TASK-ADHOC", "objective": "Add login",
            "description": "d", "done_definition": "done", "facets": {}}


def _ok_run(text="done"):
    return _ClaudeRun(returncode=0, stdout="", stderr="",
                      result_event={"type": "result", "result": text}, subtype="")


def _git_diff_proc(*_a, **_k):
    return subprocess.CompletedProcess(
        args=["git"], returncode=0,
        stdout="diff --git a/x.py b/x.py\n+x\n", stderr="",
    )


def _run_agent(tmp_path, review_side_effect, monkeypatch, max_rounds=None):
    """Run RepoBranchAgent with the gate ON, mocking claude + ReviewAgent.review."""
    monkeypatch.setenv("EXEC_REVIEW_GATE", "1")
    if max_rounds is not None:
        monkeypatch.setenv("EXEC_REVIEW_MAX_ROUNDS", str(max_rounds))

    out = str(tmp_path / "out")
    agent = RepoBranchAgent(str(tmp_path), "ai-dev/task-x", "main")

    with patch("ai_dev_system.agents.repo_branch_agent.ClaudeCodeLLMClient._resolve_claude_cmd",
               return_value="claude"), \
         patch("ai_dev_system.agents.repo_branch_agent._invoke_claude",
               return_value=_ok_run()) as mock_invoke, \
         patch("ai_dev_system.agents.repo_branch_agent.subprocess.run",
               side_effect=_git_diff_proc), \
         patch("ai_dev_system.agents.review_agent.ReviewAgent.review",
               side_effect=review_side_effect):
        result = agent.run("TASK-ADHOC", out, context=_ctx())

    review = json.loads((Path(out) / "review.json").read_text(encoding="utf-8"))
    return result, review, mock_invoke


_PASS = ReviewVerdict(verdict="pass", tests_ran=True, tests_passed=True)
def _fail():
    return ReviewVerdict(verdict="fail", tests_ran=True, tests_passed=False,
                         findings=[{"severity": "high", "file": "x.py", "line": 1,
                                    "issue": "boom"}])


def test_clean_review_no_fix_round(tmp_path, monkeypatch):
    result, review, mock_invoke = _run_agent(tmp_path, [_PASS], monkeypatch)
    assert result.success
    assert review["review_status"] == "clean"
    assert review["rounds_fixed"] == 0
    # only the initial implement invocation — no fix call
    assert mock_invoke.call_count == 1


def test_fail_then_pass_triggers_one_fix(tmp_path, monkeypatch):
    result, review, mock_invoke = _run_agent(tmp_path, [_fail(), _PASS], monkeypatch)
    assert result.success
    assert review["review_status"] == "clean"
    assert review["rounds_fixed"] == 1
    # implement + exactly one fix invocation
    assert mock_invoke.call_count == 2


def test_unresolved_after_max_rounds_is_flagged(tmp_path, monkeypatch):
    result, review, mock_invoke = _run_agent(
        tmp_path, [_fail(), _fail()], monkeypatch, max_rounds=1
    )
    # implementation still "succeeds" — flagged review is surfaced, not failed
    assert result.success
    assert review["review_status"] == "flagged"
    assert review["rounds_fixed"] == 1
    assert review["findings"][0]["issue"] == "boom"
    assert mock_invoke.call_count == 2  # implement + 1 fix


def test_gate_off_skips_review(tmp_path, monkeypatch):
    monkeypatch.setenv("EXEC_REVIEW_GATE", "0")
    out = str(tmp_path / "out")
    agent = RepoBranchAgent(str(tmp_path), "ai-dev/task-x", "main")
    with patch("ai_dev_system.agents.repo_branch_agent.ClaudeCodeLLMClient._resolve_claude_cmd",
               return_value="claude"), \
         patch("ai_dev_system.agents.repo_branch_agent._invoke_claude",
               return_value=_ok_run()) as mock_invoke, \
         patch("ai_dev_system.agents.repo_branch_agent.subprocess.run",
               side_effect=_git_diff_proc), \
         patch("ai_dev_system.agents.review_agent.ReviewAgent.review") as mock_review:
        result = agent.run("TASK-ADHOC", out, context=_ctx())
    assert result.success
    assert not (Path(out) / "review.json").exists()
    mock_review.assert_not_called()
    assert mock_invoke.call_count == 1
