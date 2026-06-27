"""Tests for RepoBranchAgent."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from ai_dev_system.agents.repo_branch_agent import (
    _build_execution_prompt,
    _DEFAULT_MAX_TURNS,
    _extract_result_event,
    _extract_summary,
    _max_turns,
    RepoBranchAgent,
)
from ai_dev_system.agents.review_agent import _build_review_prompt


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


@pytest.fixture(autouse=True)
def _disable_review_gate(monkeypatch):
    """These tests exercise the implementer in isolation. The review gate (ON by
    default) is covered separately in test_repo_branch_review_gate.py."""
    monkeypatch.setenv("EXEC_REVIEW_GATE", "0")


# ── _build_execution_prompt ────────────────────────────────────────────────────

def test_prompt_contains_objective():
    prompt = _build_execution_prompt(_ctx())
    assert "Add login" in prompt


def test_impl_prompt_does_not_ask_to_write_tests():
    from ai_dev_system.agents.repo_branch_agent import _build_execution_prompt
    p = _build_execution_prompt(_ctx())
    low = p.lower()
    assert "write tests" not in low
    assert "tests already exist" in low
    assert "weaken" in low  # must forbid weakening tests


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

    def _fake_popen(cmd, **kw):
        class FakePopen:
            returncode = 0
            stdout = iter([json.dumps({"type": "result", "result": "Done — committed changes."}) + "\n"])
            stderr = iter([])
            def wait(self, timeout=None):
                self.returncode = 0
        return FakePopen()

    with patch("ai_dev_system.agents.repo_branch_agent.ClaudeCodeLLMClient._resolve_claude_cmd",
               return_value="claude"), \
         patch("ai_dev_system.agents.repo_branch_agent.subprocess.Popen", side_effect=_fake_popen), \
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

    def _fake_popen(cmd, **kw):
        class FakePopen:
            returncode = 1
            stdout = iter([])
            stderr = iter(["auth error\n"])
            def wait(self, timeout=None):
                self.returncode = 1
        return FakePopen()

    with patch("ai_dev_system.agents.repo_branch_agent.ClaudeCodeLLMClient._resolve_claude_cmd",
               return_value="claude"), \
         patch("ai_dev_system.agents.repo_branch_agent.subprocess.Popen", side_effect=_fake_popen), \
         patch("ai_dev_system.agents.repo_branch_agent.subprocess.run", side_effect=_fake_run):
        result = agent.run("TASK-ADHOC", out, context=_ctx())

    assert not result.success
    assert "auth error" in (result.error or "")
    assert (Path(out) / "diff.txt").exists()


# ── _max_turns / turn budget ───────────────────────────────────────────────────

def test_max_turns_defaults_to_100(monkeypatch):
    monkeypatch.delenv("EXEC_MAX_TURNS", raising=False)
    assert _max_turns() == _DEFAULT_MAX_TURNS == 100


def test_max_turns_reads_env(monkeypatch):
    monkeypatch.setenv("EXEC_MAX_TURNS", "250")
    assert _max_turns() == 250


@pytest.mark.parametrize("bad", ["", "abc", "0", "-5"])
def test_max_turns_falls_back_on_invalid_env(monkeypatch, bad):
    monkeypatch.setenv("EXEC_MAX_TURNS", bad)
    assert _max_turns() == _DEFAULT_MAX_TURNS


def test_run_passes_configured_max_turns_to_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("EXEC_MAX_TURNS", "77")
    agent = RepoBranchAgent(str(tmp_path), "ai-dev/task-abc", "main")
    out = str(tmp_path / "out")
    captured: dict = {}

    def _fake_run(cmd, **kw):
        return _diff_proc()

    def _fake_popen(cmd, **kw):
        captured["cmd"] = cmd

        class FakePopen:
            returncode = 0
            stdout = iter([json.dumps({"type": "result", "result": "Done."}) + "\n"])
            stderr = iter([])

            def wait(self, timeout=None):
                self.returncode = 0

        return FakePopen()

    with patch("ai_dev_system.agents.repo_branch_agent.ClaudeCodeLLMClient._resolve_claude_cmd",
               return_value="claude"), \
         patch("ai_dev_system.agents.repo_branch_agent.subprocess.Popen", side_effect=_fake_popen), \
         patch("ai_dev_system.agents.repo_branch_agent.subprocess.run", side_effect=_fake_run):
        agent.run("TASK-ADHOC", out, context=_ctx())

    cmd = captured["cmd"]
    assert "--max-turns" in cmd
    assert cmd[cmd.index("--max-turns") + 1] == "77"


# ── error_max_turns handling ─────────────────────────────────────────────────────

def test_max_turns_subtype_yields_clear_error(tmp_path, monkeypatch):
    monkeypatch.setenv("EXEC_MAX_TURNS", "30")
    agent = RepoBranchAgent(str(tmp_path), "ai-dev/task-abc", "main")
    out = str(tmp_path / "out")

    def _fake_run(cmd, **kw):
        return _diff_proc()

    def _fake_popen(cmd, **kw):
        class FakePopen:
            returncode = 1
            # error_max_turns result event has empty result text
            stdout = iter([json.dumps({"type": "result", "subtype": "error_max_turns",
                                       "result": ""}) + "\n"])
            stderr = iter([])

            def wait(self, timeout=None):
                self.returncode = 1

        return FakePopen()

    with patch("ai_dev_system.agents.repo_branch_agent.ClaudeCodeLLMClient._resolve_claude_cmd",
               return_value="claude"), \
         patch("ai_dev_system.agents.repo_branch_agent.subprocess.Popen", side_effect=_fake_popen), \
         patch("ai_dev_system.agents.repo_branch_agent.subprocess.run", side_effect=_fake_run):
        result = agent.run("TASK-ADHOC", out, context=_ctx())

    assert not result.success
    assert "30-turn limit" in (result.error or "")
    assert "EXEC_MAX_TURNS" in (result.error or "")
    # summary is diagnosable, not a bare "claude exit=1"
    summary = (Path(out) / "summary.txt").read_text(encoding="utf-8")
    assert "error_max_turns" in summary


def test_extract_result_event_picks_last_result():
    stdout = "\n".join([
        json.dumps({"type": "tool_use", "name": "Read"}),
        json.dumps({"type": "result", "subtype": "success", "result": "ok"}),
    ])
    ev = _extract_result_event(stdout)
    assert ev is not None and ev["subtype"] == "success"


def test_extract_summary_falls_back_to_subtype():
    ev = {"type": "result", "subtype": "error_max_turns", "result": ""}
    assert "error_max_turns" in _extract_summary(ev, 1, 0)


def test_extract_summary_no_event():
    assert _extract_summary(None, 1, 123) == "claude exit=1, stdout=123B"


def test_agent_creates_output_dir_if_missing(tmp_path):
    agent = RepoBranchAgent(str(tmp_path), "ai-dev/task-abc", "main")
    deep_out = str(tmp_path / "a" / "b" / "c")

    def _fake_run(cmd, **kw):
        if cmd[0] == "git":
            return _diff_proc()
        return _ok_proc()

    def _fake_popen(cmd, **kw):
        class FakePopen:
            returncode = 0
            stdout = iter([json.dumps({"type": "result", "result": "Done — committed changes."}) + "\n"])
            stderr = iter([])
            def wait(self, timeout=None):
                self.returncode = 0
        return FakePopen()

    with patch("ai_dev_system.agents.repo_branch_agent.ClaudeCodeLLMClient._resolve_claude_cmd",
               return_value="claude"), \
         patch("ai_dev_system.agents.repo_branch_agent.subprocess.Popen", side_effect=_fake_popen), \
         patch("ai_dev_system.agents.repo_branch_agent.subprocess.run", side_effect=_fake_run):
        result = agent.run("TASK-ADHOC", deep_out, context=_ctx())

    assert Path(deep_out).exists()


# ── tdd_tests_authored flag scopes the weakening block ─────────────────────────

def test_review_prompt_no_weakening_block_without_tdd_flag():
    """When tdd_tests_authored is False/absent, reviewer prompt has NO weakening block."""
    p = _build_review_prompt("main", "Add login", test_spec="")
    assert "Test integrity" not in p
    assert "weaken" not in p.lower()


def test_review_prompt_weakening_block_present_with_tdd_flag():
    """When tdd_tests_authored True and a real test_spec exists, weakening block appears."""
    p = _build_review_prompt("main", "Add login", test_spec="AC-1: returns 401 on bad creds")
    assert "Test integrity" in p
    assert "AC-1: returns 401 on bad creds" in p
    assert "weaken" in p.lower()
