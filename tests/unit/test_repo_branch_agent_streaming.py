from __future__ import annotations
import json
import subprocess
import threading
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from ai_dev_system.agents.repo_branch_agent import RepoBranchAgent, _parse_ndjson_event


def test_parse_ndjson_event_tool_use():
    line = json.dumps({"type": "tool_use", "name": "Read", "input": {"file_path": "src/foo.py"}})
    result = _parse_ndjson_event(line)
    assert result is not None
    assert "Read" in result
    assert "src/foo.py" in result


def test_parse_ndjson_event_bash():
    line = json.dumps({"type": "tool_use", "name": "Bash", "input": {"command": "pytest tests/"}})
    result = _parse_ndjson_event(line)
    assert result is not None
    assert "Bash" in result
    assert "pytest" in result


def test_parse_ndjson_event_result():
    line = json.dumps({"type": "result", "subtype": "success", "result": "Done!", "total_cost_usd": 0.05})
    result = _parse_ndjson_event(line)
    assert result is not None
    assert "done" in result.lower() or "success" in result.lower()


def test_parse_ndjson_event_unknown_returns_none():
    line = json.dumps({"type": "system", "subtype": "init"})
    assert _parse_ndjson_event(line) is None


def test_parse_ndjson_event_invalid_json_returns_none():
    assert _parse_ndjson_event("not json {") is None


def _make_fake_popen(stdout_lines: list[str], returncode: int = 0):
    """Build a fake Popen that yields lines from a list."""
    class FakePopen:
        def __init__(self):
            self.stdout = iter(stdout_lines)
            self.stderr = iter([])
            self.returncode = returncode

        def wait(self, timeout=None):
            self.returncode = returncode

    return FakePopen()


def test_streaming_writes_to_live_log(tmp_path):
    """When live_log_path is set, parsed events are written there."""
    log_file = tmp_path / "exec.log"
    agent = RepoBranchAgent(
        repo_path=str(tmp_path),
        branch_name="ai-dev/task-test",
        base_branch="master",
        live_log_path=log_file,
    )

    ndjson_lines = [
        json.dumps({"type": "tool_use", "name": "Read", "input": {"file_path": "src/x.py"}}) + "\n",
        json.dumps({"type": "result", "subtype": "success", "result": "All done"}) + "\n",
    ]
    fake_proc = _make_fake_popen(ndjson_lines, returncode=0)

    # Patch Popen and git diff
    with patch("ai_dev_system.agents.repo_branch_agent.subprocess.Popen", return_value=fake_proc), \
         patch("ai_dev_system.agents.repo_branch_agent._git") as mock_git:
        mock_git.return_value = MagicMock(stdout="", returncode=0)
        out_dir = tmp_path / "output"
        out_dir.mkdir()
        agent.run("TASK-1", str(out_dir))

    log_content = log_file.read_text(encoding="utf-8")
    assert "Read" in log_content
    assert "src/x.py" in log_content


def test_no_live_log_path_does_not_crash(tmp_path):
    """live_log_path=None (default) — agent runs normally without writing a log."""
    agent = RepoBranchAgent(
        repo_path=str(tmp_path),
        branch_name="ai-dev/task-test",
        base_branch="master",
    )
    ndjson_lines = [
        json.dumps({"type": "result", "subtype": "success", "result": "ok"}) + "\n",
    ]
    fake_proc = _make_fake_popen(ndjson_lines, returncode=0)
    with patch("ai_dev_system.agents.repo_branch_agent.subprocess.Popen", return_value=fake_proc), \
         patch("ai_dev_system.agents.repo_branch_agent._git") as mock_git:
        mock_git.return_value = MagicMock(stdout="", returncode=0)
        out_dir = tmp_path / "output"
        out_dir.mkdir()
        result = agent.run("TASK-1", str(out_dir))
    assert result.success


def test_nonzero_returncode_returns_error(tmp_path):
    agent = RepoBranchAgent(
        repo_path=str(tmp_path),
        branch_name="ai-dev/task-test",
        base_branch="master",
    )
    stderr_lines = ["error: something went wrong\n"]
    class FakePopen:
        stdout = iter([json.dumps({"type": "result", "subtype": "error", "result": "fail"}) + "\n"])
        stderr = iter(stderr_lines)
        returncode = 1
        def wait(self, timeout=None):
            self.returncode = 1

    with patch("ai_dev_system.agents.repo_branch_agent.subprocess.Popen", return_value=FakePopen()), \
         patch("ai_dev_system.agents.repo_branch_agent._git") as mock_git:
        mock_git.return_value = MagicMock(stdout="", returncode=0)
        out_dir = tmp_path / "output"
        out_dir.mkdir()
        result = agent.run("TASK-1", str(out_dir))
    assert not result.success
    assert "exit" in result.error.lower() or "1" in result.error
