"""TerminalGate2IO: interactive Gate 2 review with task-graph editing.

Driven by an injected `prompt_fn` (scripted here) so no real stdin is needed.
Edits mutate a copy; `approve` validates before returning.
"""
import io

from ai_dev_system.gate.terminal_gate2 import TerminalGate2IO


def _task(tid, deps=None, **over):
    t = {
        "id": tid, "title": tid, "phase": "implement", "type": "coding",
        "deps": list(deps or []), "execution_type": "atomic",
        "agent_type": "Engineer", "required_inputs": [], "expected_outputs": [],
        "done_definition": "done", "enriched_by": "skeleton", "objective": f"obj {tid}",
    }
    t.update(over)
    return t


def _valid_envelope(extra=None):
    tasks = [
        _task("TASK-PARSE"),
        _task("TASK-DESIGN", deps=["TASK-PARSE"]),
        _task("TASK-IMPL", deps=["TASK-DESIGN"]),
        _task("TASK-VALIDATE", deps=["TASK-IMPL"]),
    ]
    if extra:
        tasks.extend(extra)
    return {"graph_version": 1, "tasks": tasks}


def _scripted(lines):
    it = iter(lines)
    return lambda prompt="": next(it)


def _io(lines, **kw):
    return TerminalGate2IO(prompt_fn=_scripted(lines), out=io.StringIO(), **kw)


def _find(env, tid):
    return next(t for t in env["tasks"] if t["id"] == tid)


def test_auto_approve_skips_prompt():
    def _boom(prompt=""):
        raise AssertionError("prompt_fn must not be called when auto_approve=True")

    io_ = TerminalGate2IO(prompt_fn=_boom, out=io.StringIO(), auto_approve=True)
    env = _valid_envelope()
    action, graph = io_.collect_edits(env)
    assert action == "approve"
    assert graph is env


def test_set_field_then_approve():
    env = _valid_envelope()
    action, graph = _io(["set TASK-IMPL objective NewObjective", "approve"]).collect_edits(env)
    assert action == "approve"
    assert _find(graph, "TASK-IMPL")["objective"] == "NewObjective"
    # original envelope untouched (edits applied to a copy)
    assert _find(env, "TASK-IMPL")["objective"] == "obj TASK-IMPL"


def test_remove_task_scrubs_deps():
    env = _valid_envelope(extra=[_task("TASK-EXTRA", deps=["TASK-IMPL"])])
    # make a core task depend on the extra one, to prove deps get scrubbed
    _find(env, "TASK-VALIDATE")["deps"] = ["TASK-IMPL", "TASK-EXTRA"]
    action, graph = _io(["remove TASK-EXTRA", "approve"]).collect_edits(env)
    assert action == "approve"
    assert all(t["id"] != "TASK-EXTRA" for t in graph["tasks"])
    assert _find(graph, "TASK-VALIDATE")["deps"] == ["TASK-IMPL"]


def test_dep_remove_then_approve():
    env = _valid_envelope()
    action, graph = _io(["dep remove TASK-IMPL TASK-DESIGN", "approve"]).collect_edits(env)
    assert action == "approve"
    assert _find(graph, "TASK-IMPL")["deps"] == []


def test_dep_add():
    env = _valid_envelope()
    action, graph = _io(["dep add TASK-PARSE TASK-VALIDATE", "reject"]).collect_edits(env)
    # reject returns, but the edit should have been applied to the working copy first
    assert "TASK-VALIDATE" in _find(graph, "TASK-PARSE")["deps"]


def test_reject_returns_reject():
    env = _valid_envelope()
    action, _ = _io(["reject"]).collect_edits(env)
    assert action == "reject"


def test_invalid_graph_blocks_approve():
    # Missing core node TASK-VALIDATE → validate fails → approve must not succeed.
    env = {"graph_version": 1, "tasks": [
        _task("TASK-PARSE"),
        _task("TASK-DESIGN", deps=["TASK-PARSE"]),
        _task("TASK-IMPL", deps=["TASK-DESIGN"]),
    ]}
    calls = []

    def fn(prompt=""):
        calls.append(prompt)
        return ["approve", "reject"][len(calls) - 1]

    action, _ = TerminalGate2IO(prompt_fn=fn, out=io.StringIO()).collect_edits(env)
    assert action == "reject"          # first approve was rejected by validation
    assert len(calls) == 2             # approve (blocked) then reject
