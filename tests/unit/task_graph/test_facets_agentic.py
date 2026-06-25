import json
import subprocess

import pytest

from ai_dev_system.task_graph.facets_agentic import generate_task_facets_agentic, _build_command
from ai_dev_system.task_graph.facets import FACET_KEYS, SPEC_FACET_KEYS, EXEC_FACET_KEYS


def _task():
    return {"id": "TASK-ADHOC", "objective": "add CSV import", "description": "...",
            "type": "coding", "execution_type": "atomic",
            "required_inputs": [], "expected_outputs": []}


def _wrapper(inner: str):
    # mimic `claude -p --output-format json` wrapper
    return json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": inner})


def _ok_inner():
    # LLM only returns spec facets (realistic)
    return json.dumps({k: {"status": "filled", "content": f"{k} c", "reason": ""}
                       for k in SPEC_FACET_KEYS})


class _FakeRun:
    def __init__(self, completed): self.completed = completed; self.calls = []
    def __call__(self, cmd, **kw): self.calls.append((cmd, kw)); return self.completed


def _cp(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(args=["claude"], returncode=returncode, stdout=stdout, stderr=stderr)


def test_happy_path_parses_facets(tmp_path):
    run = _FakeRun(_cp(stdout=_wrapper(_ok_inner())))
    facets = generate_task_facets_agentic(_task(), str(tmp_path), run=run)
    assert set(facets.keys()) == set(FACET_KEYS)
    assert facets["database"]["status"] == "filled"


def test_command_is_read_only_and_uses_repo_cwd(tmp_path):
    run = _FakeRun(_cp(stdout=_wrapper(_ok_inner())))
    generate_task_facets_agentic(_task(), str(tmp_path), run=run)
    cmd, kw = run.calls[0]
    assert kw["cwd"] == str(tmp_path)
    assert "--permission-mode" in cmd and "bypassPermissions" in cmd
    assert "--disallowedTools" in cmd
    for banned in ("Edit", "Write", "Bash", "PowerShell", "WebFetch", "WebSearch"):
        assert banned in cmd
    assert "-p" in cmd


def test_missing_repo_path_raises_without_running():
    run = _FakeRun(_cp(stdout=_wrapper(_ok_inner())))
    with pytest.raises(ValueError, match="repo_path"):
        generate_task_facets_agentic(_task(), "/no/such/dir", run=run)
    assert run.calls == []  # never ran the subprocess


def test_nonzero_exit_raises(tmp_path):
    run = _FakeRun(_cp(stdout="", returncode=1, stderr="boom"))
    with pytest.raises(RuntimeError, match="code 1"):
        generate_task_facets_agentic(_task(), str(tmp_path), run=run)


def test_timeout_raises(tmp_path):
    def _raise(cmd, **kw): raise subprocess.TimeoutExpired(cmd, 1)
    with pytest.raises(subprocess.TimeoutExpired):
        generate_task_facets_agentic(_task(), str(tmp_path), run=_raise)


def test_non_json_wrapper_raises(tmp_path):
    run = _FakeRun(_cp(stdout="not json at all"))
    with pytest.raises(Exception):
        generate_task_facets_agentic(_task(), str(tmp_path), run=run)


def test_inner_non_json_raises(tmp_path):
    run = _FakeRun(_cp(stdout=_wrapper("the database uses postgres")))  # inner is prose, not JSON
    with pytest.raises(Exception):
        generate_task_facets_agentic(_task(), str(tmp_path), run=run)


def test_missing_facet_key_becomes_needs_human(tmp_path):
    inner = json.dumps({"input": {"status": "filled", "content": "c", "reason": ""}})
    run = _FakeRun(_cp(stdout=_wrapper(inner)))
    facets = generate_task_facets_agentic(_task(), str(tmp_path), run=run)
    assert facets["input"]["status"] == "filled"
    # Missing spec keys → needs_human; exec keys → na
    for k in SPEC_FACET_KEYS:
        if k != "input":
            assert facets[k]["status"] == "needs_human"
    for k in EXEC_FACET_KEYS:
        assert facets[k]["status"] == "na"


def test_build_command_includes_model_when_given(tmp_path):
    cmd = _build_command("claude", "PROMPT", model="opus")
    assert "--model" in cmd and "opus" in cmd
    assert cmd[0] == "claude" and "-p" in cmd


def test_extract_text_messages_fallback(tmp_path):
    inner = _ok_inner()
    wrapper = json.dumps({"messages": [{"role": "assistant", "content": inner}]})
    run = _FakeRun(_cp(stdout=wrapper))
    facets = generate_task_facets_agentic(_task(), str(tmp_path), run=run)
    # Spec facets filled by LLM; exec facets default to na
    assert all(facets[k]["status"] == "filled" for k in SPEC_FACET_KEYS)
    assert all(facets[k]["status"] == "na" for k in EXEC_FACET_KEYS)


def test_exec_facets_are_na_in_agentic_result(tmp_path):
    run = _FakeRun(_cp(stdout=_wrapper(_ok_inner())))
    facets = generate_task_facets_agentic(_task(), str(tmp_path), run=run)
    assert set(facets.keys()) == set(FACET_KEYS)
    for k in EXEC_FACET_KEYS:
        assert facets[k]["status"] == "na"
        assert "exec-time" in facets[k]["reason"]


def test_prompt_mentions_13_spec_facets(tmp_path):
    run = _FakeRun(_cp(stdout=_wrapper(_ok_inner())))
    generate_task_facets_agentic(_task(), str(tmp_path), run=run)
    prompt_arg = run.calls[0][0][run.calls[0][0].index("-p") + 1]
    assert "13" in prompt_arg
