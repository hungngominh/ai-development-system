from ai_dev_system.task_graph.generator import generate_task_graph, GraphValidationError
from ai_dev_system.gate.gate2 import run_gate_2
from ai_dev_system.gate.stub_gate2 import StubGate2IO


def test_generate_minimal_graph():
    spec = {"problem.md": "test", "requirements.md": "test", "constraints.md": "test",
            "success_criteria.md": "test", "assumptions.md": "test"}
    brief = {"constraints": {"hard": [], "soft": []},
             "scope": {"type": "unknown"}, "success_signals": []}
    envelope = generate_task_graph(spec, brief, "artifact-123")
    assert envelope["graph_version"] == 1
    assert len(envelope["tasks"]) == 4
    assert envelope["rules_applied"] == []
    assert envelope["llm_enriched"] is False


def test_generate_with_rules():
    spec = {"problem.md": "forum", "requirements.md": "reqs", "constraints.md": "pg"}
    brief = {"constraints": {"hard": ["Must use PostgreSQL"], "soft": []},
             "scope": {"type": "product"}, "success_signals": []}
    envelope = generate_task_graph(spec, brief, "artifact-123")
    assert "RULE-DATABASE" in envelope["rules_applied"]
    assert "RULE-PRODUCT-SPLIT" in envelope["rules_applied"]
    ids = {t["id"] for t in envelope["tasks"]}
    assert "TASK-DESIGN.SCHEMA" in ids
    assert "TASK-IMPL.BACKEND" in ids
    assert "TASK-IMPL.FRONTEND" in ids


def test_gate2_approve():
    envelope = generate_task_graph({}, {"constraints": {"hard": [], "soft": []},
                                        "scope": {"type": "unknown"}, "success_signals": []},
                                   "a-123")
    io = StubGate2IO(action="approve")
    result = run_gate_2(envelope, io)
    assert result.status == "approved"
    assert result.graph == envelope


def test_gate2_reject():
    envelope = generate_task_graph({}, {"constraints": {"hard": [], "soft": []},
                                        "scope": {"type": "unknown"}, "success_signals": []},
                                   "a-123")
    io = StubGate2IO(action="reject")
    result = run_gate_2(envelope, io)
    assert result.status == "rejected"
