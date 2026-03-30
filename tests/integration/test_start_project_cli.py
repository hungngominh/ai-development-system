"""Integration tests: invoke start_project.py via subprocess."""
import json
import os
import subprocess
import sys

import pytest


def _run_cli(idea: str, project_name: str, constraints: str = "", env_override: dict = None):
    env = os.environ.copy()
    env["AI_DEV_STUB_LLM"] = "1"   # dùng StubDebateLLMClient
    if env_override:
        env.update(env_override)
    return subprocess.run(
        [
            sys.executable, "-m", "ai_dev_system.cli.start_project",
            "--project-name", project_name,
            "--idea", idea,
            "--constraints", constraints,
        ],
        capture_output=True, text=True, env=env,
    )


@pytest.mark.integration
def test_happy_path_exit_0(config):
    result = _run_cli("Build a forum for knowledge sharing", "forum-test")
    assert result.returncode == 0, f"stderr: {result.stderr}"


@pytest.mark.integration
def test_stdout_is_valid_json(config):
    result = _run_cli("Build a forum for knowledge sharing", "forum-test")
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["status"] == "PAUSED_AT_GATE_1"
    assert "run_id" in data
    assert "project_id" in data


@pytest.mark.integration
def test_count_invariant(config):
    """questions_count == escalated + resolved + optional."""
    result = _run_cli("Build a forum for knowledge sharing", "forum-test")
    data = json.loads(result.stdout)
    assert (
        data["questions_count"]
        == data["escalated_count"] + data["resolved_count"] + data["optional_count"]
    )


@pytest.mark.integration
def test_stderr_has_progress_stdout_only_json(config):
    result = _run_cli("Build a task manager", "task-mgr-test")
    assert result.returncode == 0
    # stderr phải có progress lines
    assert "[Phase" in result.stderr
    # stdout phải chỉ có đúng 1 JSON object (không có trailing garbage)
    data = json.loads(result.stdout.strip())
    assert isinstance(data, dict)


@pytest.mark.integration
def test_constraints_appended_to_idea(config):
    """Chạy với constraints — chỉ cần không crash và trả về valid JSON."""
    result = _run_cli(
        "Build a forum",
        "forum-constraint-test",
        constraints="Python only, no cloud",
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)["status"] == "PAUSED_AT_GATE_1"


@pytest.mark.integration
def test_idempotent_project_id(config):
    """Cùng project name → cùng project_id trong cả 2 lần chạy."""
    r1 = _run_cli("Build a forum", "same-project-name")
    r2 = _run_cli("Build something else", "same-project-name")
    assert r1.returncode == 0 and r2.returncode == 0
    id1 = json.loads(r1.stdout)["project_id"]
    id2 = json.loads(r2.stdout)["project_id"]
    assert id1 == id2
