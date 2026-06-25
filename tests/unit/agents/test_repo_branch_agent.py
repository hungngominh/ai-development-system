"""Tests for RepoBranchAgent."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from ai_dev_system.agents.repo_branch_agent import _build_execution_prompt, RepoBranchAgent


def _ctx(
    objective: str = "Add login",
    description: str = "Implement JWT login",
    facets: dict | None = None,
) -> dict:
    return {
        "task_id": "TASK-ADHOC",
        "objective": objective,
        "description": description,
        "done_definition": "Login endpoint returns JWT",
        "facets": facets or {},
    }


# ── _build_execution_prompt ────────────────────────────────────────────────────

def test_prompt_contains_objective():
    prompt = _build_execution_prompt(_ctx())
    assert "Add login" in prompt


def test_prompt_includes_filled_facets():
    ctx = _ctx(
        facets={
            "input": {"status": "filled", "content": "POST /login {email, password}", "reason": ""},
            "auth_permission": {"status": "needs_human", "content": "", "reason": ""},
            "database": {"status": "na", "content": "", "reason": "no schema change"},
        }
    )
    prompt = _build_execution_prompt(ctx)
    assert "POST /login {email, password}" in prompt


def test_prompt_excludes_needs_human_content():
    ctx = _ctx(
        facets={
            "input": {"status": "needs_human", "content": "", "reason": ""},
        }
    )
    prompt = _build_execution_prompt(ctx)
    # Only the spec section header should appear; no facet content injected
    assert "needs_human" not in prompt


def test_prompt_excludes_na_content():
    ctx = _ctx(
        facets={
            "input": {"status": "na", "content": "", "reason": "irrelevant"},
        }
    )
    prompt = _build_execution_prompt(ctx)
    assert "irrelevant" not in prompt


# ── RepoBranchAgent.run() ──────────────────────────────────────────────────────

def _ok_proc():
    return subprocess.CompletedProcess(
        args=["claude"],
        returncode=0,
        stdout=json.dumps({"type": "result", "result": "Done — committed changes."}),
        stderr="",
    )


def _fail_proc():
    return subprocess.CompletedProcess(
        args=["claude"], returncode=1, stdout="", stderr="auth error",
    )


def _diff_proc():
    return subprocess.CompletedProcess(
        args=["git", "diff"],
        returncode=0,
        stdout="diff --git a/src/foo.py b/src/foo.py\n+def new_func(): pass\n",
        stderr="",
    )


def test_agent_writes_diff_and_summary_on_success(tmp_path):
    agent = RepoBranchAgent(str(tmp_path), "ai-dev/task-abc", "main")
    out = str(tmp_path / "out")

    calls: list = []

    def _fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[0] == "git":
            return _diff_proc()
        return _ok_proc()

    with patch("ai_dev_system.agents.repo_branch_agent.ClaudeCodeLLMClient._resolve_claude_cmd",
               return_value="claude"), \
         patch("ai_dev_system.agents.repo_branch_agent.subprocess.run", side_effect=_fake_run):
        result = agent.run("TASK-ADHOC", out, context=_ctx())

    assert result.success
    assert (Path(out) / "diff.txt").exists()
    assert (Path(out) / "summary.txt").exists()
    assert "new_func" in (Path(out) / "diff.txt").read_text(encoding="utf-8")


def test_agent_writes_diff_even_on_claude_failure(tmp_path):
    agent = RepoBranchAgent(str(tmp_path), "ai-dev/task-abc", "main")
    out = str(tmp_path / "out")

    def _fake_run(cmd, **kw):
        if cmd[0] == "git":
            return _diff_proc()
        return _fail_proc()

    with patch("ai_dev_system.agents.repo_branch_agent.ClaudeCodeLLMClient._resolve_claude_cmd",
               return_value="claude"), \
         patch("ai_dev_system.agents.repo_branch_agent.subprocess.run", side_effect=_fake_run):
        result = agent.run("TASK-ADHOC", out, context=_ctx())

    assert not result.success
    assert "auth error" in (result.error or "")
    assert (Path(out) / "diff.txt").exists()


def test_agent_creates_output_dir_if_missing(tmp_path):
    agent = RepoBranchAgent(str(tmp_path), "ai-dev/task-abc", "main")
    deep_out = str(tmp_path / "a" / "b" / "c")

    def _fake_run(cmd, **kw):
        if cmd[0] == "git":
            return _diff_proc()
        return _ok_proc()

    with patch("ai_dev_system.agents.repo_branch_agent.ClaudeCodeLLMClient._resolve_claude_cmd",
               return_value="claude"), \
         patch("ai_dev_system.agents.repo_branch_agent.subprocess.run", side_effect=_fake_run):
        result = agent.run("TASK-ADHOC", deep_out, context=_ctx())

    assert Path(deep_out).exists()
