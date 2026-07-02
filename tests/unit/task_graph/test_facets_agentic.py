import json

import pytest

from ai_dev_system.agents.repo_branch_agent import _ClaudeRun
from ai_dev_system.task_graph.facets_agentic import (
    generate_task_facets_agentic, _spec_max_turns, _spec_idle_timeout,
    _spec_hard_timeout, _READONLY_FLAGS,
)
from ai_dev_system.task_graph.facets import FACET_KEYS, SPEC_FACET_KEYS, EXEC_FACET_KEYS


def _task():
    return {"id": "TASK-ADHOC", "objective": "add CSV import", "description": "...",
            "type": "coding", "execution_type": "atomic",
            "required_inputs": [], "expected_outputs": []}


def _ok_inner():
    # LLM only returns spec facets (realistic)
    return json.dumps({k: {"status": "filled", "content": f"{k} c", "reason": ""}
                       for k in SPEC_FACET_KEYS})


def _ok_run(inner: str, subtype="success") -> _ClaudeRun:
    return _ClaudeRun(returncode=0, stdout="", stderr="",
                      result_event={"type": "result", "subtype": subtype, "result": inner},
                      subtype=subtype)


class _FakeInvoke:
    def __init__(self, run): self.run = run; self.calls = []
    def __call__(self, *a, **kw): self.calls.append((a, kw)); return self.run


# ── happy path ──────────────────────────────────────────────────────────────

def test_happy_path_parses_facets(tmp_path):
    inv = _FakeInvoke(_ok_run(_ok_inner()))
    facets = generate_task_facets_agentic(_task(), str(tmp_path), invoke=inv)
    assert set(facets.keys()) == set(FACET_KEYS)
    assert facets["database"]["status"] == "filled"


def test_missing_repo_path_raises_without_running():
    inv = _FakeInvoke(_ok_run(_ok_inner()))
    with pytest.raises(ValueError, match="repo_path"):
        generate_task_facets_agentic(_task(), "/no/such/dir", invoke=inv)
    assert inv.calls == []  # never called invoke


def test_nonzero_exit_raises(tmp_path):
    timed = _ClaudeRun(returncode=1, stdout="", stderr="boom", result_event=None, subtype="")
    with pytest.raises(RuntimeError, match="code 1"):
        generate_task_facets_agentic(_task(), str(tmp_path), invoke=_FakeInvoke(timed))


def test_non_json_wrapper_raises(tmp_path):
    bad = _ClaudeRun(returncode=0, stdout="not json at all", stderr="",
                     result_event=None, subtype="")
    with pytest.raises(Exception):
        generate_task_facets_agentic(_task(), str(tmp_path), invoke=_FakeInvoke(bad))


def test_inner_non_json_raises(tmp_path):
    # inner is prose, not JSON
    inv = _FakeInvoke(_ok_run("the database uses postgres"))
    with pytest.raises(Exception):
        generate_task_facets_agentic(_task(), str(tmp_path), invoke=inv)


def test_missing_facet_key_becomes_needs_human(tmp_path):
    inner = json.dumps({"input": {"status": "filled", "content": "c", "reason": ""}})
    inv = _FakeInvoke(_ok_run(inner))
    facets = generate_task_facets_agentic(_task(), str(tmp_path), invoke=inv)
    assert facets["input"]["status"] == "filled"
    # Missing spec keys → needs_human; exec keys → na
    for k in SPEC_FACET_KEYS:
        if k != "input":
            assert facets[k]["status"] == "needs_human"
    for k in EXEC_FACET_KEYS:
        assert facets[k]["status"] == "na"


def test_exec_facets_are_na_in_agentic_result(tmp_path):
    inv = _FakeInvoke(_ok_run(_ok_inner()))
    facets = generate_task_facets_agentic(_task(), str(tmp_path), invoke=inv)
    assert set(facets.keys()) == set(FACET_KEYS)
    for k in EXEC_FACET_KEYS:
        assert facets[k]["status"] == "na"
        assert "exec-time" in facets[k]["reason"]


def test_prompt_mentions_13_spec_facets(tmp_path):
    inv = _FakeInvoke(_ok_run(_ok_inner()))
    generate_task_facets_agentic(_task(), str(tmp_path), invoke=inv)
    # prompt is the 3rd positional arg (index 2)
    a, kw = inv.calls[0]
    assert "13" in a[2]


# ── invoke kwargs assertion ──────────────────────────────────────────────────

def test_invoke_receives_flags_turns_and_timeouts(tmp_path, monkeypatch):
    monkeypatch.delenv("SPEC_MAX_TURNS", raising=False)
    monkeypatch.delenv("SPEC_IDLE_TIMEOUT", raising=False)
    monkeypatch.delenv("SPEC_HARD_TIMEOUT", raising=False)
    inv = _FakeInvoke(_ok_run(_ok_inner()))
    generate_task_facets_agentic(_task(), str(tmp_path), model="opus", invoke=inv)
    a, kw = inv.calls[0]
    assert a[3] == 40                       # max_turns positional
    assert a[4] == 3600.0                   # timeout_s (hard ceiling)
    assert kw["idle_timeout_s"] == 180.0
    assert kw["flags"] == _READONLY_FLAGS
    assert kw["model"] == "opus"


# ── resolver tests ───────────────────────────────────────────────────────────

def test_spec_max_turns_env(monkeypatch):
    monkeypatch.delenv("SPEC_MAX_TURNS", raising=False)
    assert _spec_max_turns() == 40
    monkeypatch.setenv("SPEC_MAX_TURNS", "25")
    assert _spec_max_turns() == 25
    monkeypatch.setenv("SPEC_MAX_TURNS", "zero")
    assert _spec_max_turns() == 40


def test_spec_timeout_resolvers_env(monkeypatch):
    monkeypatch.delenv("SPEC_IDLE_TIMEOUT", raising=False)
    monkeypatch.delenv("SPEC_HARD_TIMEOUT", raising=False)
    assert _spec_idle_timeout() == 180.0 and _spec_hard_timeout() == 3600.0
    monkeypatch.setenv("SPEC_IDLE_TIMEOUT", "300")
    monkeypatch.setenv("SPEC_HARD_TIMEOUT", "7200")
    assert _spec_idle_timeout() == 300.0 and _spec_hard_timeout() == 7200.0
    monkeypatch.setenv("SPEC_IDLE_TIMEOUT", "-1")
    assert _spec_idle_timeout() == 180.0


# ── timeout/readonly/streaming behavior ─────────────────────────────────────

def test_idle_timeout_raises_with_knob_name(tmp_path):
    timed = _ClaudeRun(returncode=-1, stdout="", stderr="", result_event=None,
                       subtype="", timed_out=True, timeout_kind="idle")
    with pytest.raises(RuntimeError, match="SPEC_IDLE_TIMEOUT"):
        generate_task_facets_agentic(_task(), str(tmp_path), invoke=_FakeInvoke(timed))


def test_hard_timeout_raises_with_knob_name(tmp_path):
    timed = _ClaudeRun(returncode=-1, stdout="", stderr="", result_event=None,
                       subtype="", timed_out=True, timeout_kind="hard")
    with pytest.raises(RuntimeError, match="SPEC_HARD_TIMEOUT"):
        generate_task_facets_agentic(_task(), str(tmp_path), invoke=_FakeInvoke(timed))


def test_readonly_flags_are_stream_json_and_no_write_tools():
    assert "stream-json" in _READONLY_FLAGS and "--verbose" in _READONLY_FLAGS
    assert "Edit" in _READONLY_FLAGS and "Write" in _READONLY_FLAGS  # still disallowed


# ── stdout fallback (messages shape) ────────────────────────────────────────

def test_extract_text_messages_fallback(tmp_path):
    inner = _ok_inner()
    # result_event=None forces the stdout fallback path
    stdout_wrapper = json.dumps({"messages": [{"role": "assistant", "content": inner}]})
    msg_run = _ClaudeRun(returncode=0, stdout=stdout_wrapper, stderr="",
                         result_event=None, subtype="")
    facets = generate_task_facets_agentic(_task(), str(tmp_path), invoke=_FakeInvoke(msg_run))
    # Spec facets filled by LLM; exec facets default to na
    assert all(facets[k]["status"] == "filled" for k in SPEC_FACET_KEYS)
    assert all(facets[k]["status"] == "na" for k in EXEC_FACET_KEYS)
