from ai_dev_system.gate.core import run_gate_1, GateResult
from ai_dev_system.gate.stub import StubGateIO
from ai_dev_system.normalize import normalize_idea


def test_gate_approve_no_edits():
    brief = normalize_idea("Build a forum")
    io = StubGateIO(approve=True)
    result = run_gate_1(brief, io)
    assert result.status == "approved"
    assert result.brief["raw_idea"] == "Build a forum"
    assert io.presented is not None


def test_gate_approve_with_edits():
    brief = normalize_idea("Build a forum")
    io = StubGateIO(edits={"problem": "No knowledge sharing"}, approve=True)
    result = run_gate_1(brief, io)
    assert result.status == "approved"
    assert result.brief["problem"] == "No knowledge sharing"
    assert result.brief["raw_idea"] == "Build a forum"  # immutable


def test_gate_reject():
    brief = normalize_idea("Build a forum")
    io = StubGateIO(approve=False)
    result = run_gate_1(brief, io)
    assert result.status == "rejected"
    assert result.brief["problem"] == ""  # original, not edited


def test_gate_deep_edit_nested():
    brief = normalize_idea("Build a forum")
    io = StubGateIO(edits={"constraints": {"hard": ["Must use PostgreSQL"]}}, approve=True)
    result = run_gate_1(brief, io)
    assert result.brief["constraints"]["hard"] == ["Must use PostgreSQL"]
    assert result.brief["constraints"]["soft"] == []  # not corrupted
