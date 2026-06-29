"""Closed-loop proof: a project-tier lesson learned on run 1 is injected on run 2."""
from __future__ import annotations

import json
from unittest.mock import patch

from ai_dev_system.rules.learning import learn_from_failure
from ai_dev_system.rules.project_rules import project_rules_dir, load_project_file_rules
from ai_dev_system.agents.repo_branch_agent import RepoBranchAgent


def _ctx():
    return {"objective": "Add login", "description": "", "done_definition": "",
            "type": "coding", "facets": {}}


def test_lesson_learned_then_applied(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    rules_dir = project_rules_dir(repo)

    # ── RUN 1: a reject mints a project-tier lesson ──
    result = learn_from_failure(
        None, "run1",
        {"task_run_id": "t1", "task_type": "coding", "tags": []},
        rules_dir=rules_dir, source="gate",
        rejection_reason="endpoint skips input validation",
    )
    assert result is not None
    assert (rules_dir / "learned-coding.yaml").exists()

    # The loader sees it for a matching task.
    assert any("input validation" in r
               for r in load_project_file_rules(str(repo), {"type": "coding"}))

    # ── RUN 2: the implementer's CLI prompt carries the lesson ──
    monkeypatch.setenv("EXEC_REVIEW_GATE", "0")
    agent = RepoBranchAgent(str(repo), "ai-dev/task-xyz", "main")
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
        agent.run("TASK-XYZ", str(tmp_path / "out"), context=_ctx(), file_rules=[])

    assert "cmd" in captured, "Popen was never called — agent.run did not reach subprocess"
    assert "input validation" in captured["cmd"][2]
