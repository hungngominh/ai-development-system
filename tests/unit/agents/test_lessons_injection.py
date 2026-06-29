"""Learned lessons (file_rules) are rendered into agent prompts (closes the
open learning loop where RepoBranchAgent/TestAuthorAgent dropped file_rules)."""
from __future__ import annotations

import json
from unittest.mock import patch

from ai_dev_system.agents.repo_branch_agent import (
    RepoBranchAgent,
    _format_lessons,
    _build_execution_prompt,
)
from ai_dev_system.agents.test_author_agent import _build_test_prompt


def _ctx() -> dict:
    return {
        "objective": "Add login",
        "description": "JWT login",
        "done_definition": "returns JWT",
        "type": "coding",
        "facets": {},
    }


def test_format_lessons_empty_is_blank():
    assert _format_lessons([]) == ""
    assert _format_lessons(None) == ""


def test_format_lessons_renders_block():
    block = _format_lessons(["Run migrations before integration tests"])
    assert "LESSONS FROM PAST FAILURES" in block
    assert "Run migrations before integration tests" in block
    assert "- Run migrations before integration tests" in block


def test_execution_prompt_includes_lessons():
    p = _build_execution_prompt(_ctx(), ["Always validate the email field"])
    assert "LESSONS FROM PAST FAILURES" in p
    assert "Always validate the email field" in p


def test_execution_prompt_without_lessons_has_no_block():
    p = _build_execution_prompt(_ctx())
    assert "LESSONS FROM PAST FAILURES" not in p


def test_test_prompt_includes_lessons():
    p = _build_test_prompt(_ctx(), ["Cover the 401 path"])
    assert "LESSONS FROM PAST FAILURES" in p
    assert "Cover the 401 path" in p


def _capture_prompt_run(agent, monkeypatch, file_rules):
    monkeypatch.setenv("EXEC_REVIEW_GATE", "0")  # implementer in isolation
    captured = {}

    def _fake_popen(cmd, **kw):
        captured["cmd"] = cmd

        class FakePopen:
            returncode = 0
            stdout = iter([json.dumps({"type": "result", "result": "Done."}) + "\n"])
            stderr = iter([])

            def wait(self, timeout=None):
                self.returncode = 0

        return FakePopen()

    def _fake_run(cmd, **kw):
        import subprocess
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="(no diff)", stderr="")

    with patch("ai_dev_system.agents.repo_branch_agent.ClaudeCodeLLMClient._resolve_claude_cmd",
               return_value="claude"), \
         patch("ai_dev_system.agents.repo_branch_agent.subprocess.Popen", side_effect=_fake_popen), \
         patch("ai_dev_system.agents.repo_branch_agent.subprocess.run", side_effect=_fake_run):
        agent.run("TASK-ADHOC", str(agent.repo_path) + "/out", context=_ctx(), file_rules=file_rules)
    return captured["cmd"]


def test_repo_branch_run_puts_lesson_in_cli_prompt(tmp_path, monkeypatch):
    agent = RepoBranchAgent(str(tmp_path), "ai-dev/task-abc", "main")
    cmd = _capture_prompt_run(agent, monkeypatch, ["Never log secrets"])
    # cmd == [claude, "-p", PROMPT, ...]; the prompt carries the lesson.
    assert "Never log secrets" in cmd[2]
