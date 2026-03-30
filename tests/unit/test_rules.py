# tests/unit/test_rules.py
import pytest
from ai_dev_system.task_graph.skeleton import build_skeleton
from ai_dev_system.task_graph.rules import add_parallel, add_before, add_after, _find, apply_rules


def _make_node(id, **kw):
    """Helper to create a minimal valid node dict."""
    base = {"id": id, "title": id, "phase": "implement", "group": "g",
            "execution_type": "atomic", "type": "coding", "agent_type": "Dev",
            "required_inputs": [], "expected_outputs": [], "done_definition": "d",
            "enriched_by": "rule", "created_by_rule": "TEST"}
    base.update(kw)
    return base


# --- Primitive tests ---

def test_add_before_inserts_node():
    graph = build_skeleton()
    graph, changed = add_before(graph, "TASK-IMPL", _make_node("TASK-DESIGN.SCHEMA"))
    assert changed is True
    by_id = {t["id"]: t for t in graph}
    assert "TASK-DESIGN.SCHEMA" in by_id
    assert by_id["TASK-DESIGN.SCHEMA"]["deps"] == ["TASK-DESIGN"]
    assert by_id["TASK-IMPL"]["deps"] == ["TASK-DESIGN.SCHEMA"]


def test_add_parallel_makes_composite():
    graph = build_skeleton()
    graph, changed = add_parallel(graph, "TASK-IMPL", [
        _make_node("TASK-IMPL.A"), _make_node("TASK-IMPL.B"),
    ])
    assert changed is True
    by_id = {t["id"]: t for t in graph}
    assert by_id["TASK-IMPL"]["execution_type"] == "composite"
    assert by_id["TASK-IMPL.A"]["deps"] == ["TASK-DESIGN"]
    assert by_id["TASK-IMPL.B"]["deps"] == ["TASK-DESIGN"]
    assert by_id["TASK-VALIDATE"]["deps"] == ["TASK-IMPL"]  # NOT redirected


def test_add_parallel_rejects_already_composite():
    graph = build_skeleton()
    graph, _ = add_parallel(graph, "TASK-IMPL", [_make_node("TASK-IMPL.A")])
    with pytest.raises(ValueError, match="already-composite"):
        add_parallel(graph, "TASK-IMPL", [_make_node("TASK-IMPL.C")])


def test_add_after_on_atomic():
    graph = build_skeleton()
    graph, changed = add_after(graph, "TASK-IMPL", _make_node("TASK-IMPL.PERF"))
    assert changed is True
    by_id = {t["id"]: t for t in graph}
    assert by_id["TASK-IMPL.PERF"]["deps"] == ["TASK-IMPL"]
    assert by_id["TASK-VALIDATE"]["deps"] == ["TASK-IMPL.PERF"]


def test_add_after_on_composite():
    graph = build_skeleton()
    graph, _ = add_parallel(graph, "TASK-IMPL", [
        _make_node("TASK-IMPL.A"), _make_node("TASK-IMPL.B"),
    ])
    graph, _ = add_after(graph, "TASK-IMPL", _make_node("TASK-IMPL.PERF"))
    by_id = {t["id"]: t for t in graph}
    assert set(by_id["TASK-IMPL.PERF"]["deps"]) == {"TASK-IMPL.A", "TASK-IMPL.B"}


# --- Rule tests ---

def test_rule_database_fires():
    graph = build_skeleton()
    spec = {"constraints": {"hard": ["Must use PostgreSQL"], "soft": []},
            "scope": {"type": "unknown"}, "success_signals": []}
    graph, applied = apply_rules(graph, spec)
    assert "RULE-DATABASE" in applied
    assert "TASK-DESIGN.SCHEMA" in {t["id"] for t in graph}


def test_rule_product_split_fires():
    graph = build_skeleton()
    spec = {"constraints": {"hard": [], "soft": []},
            "scope": {"type": "product"}, "success_signals": []}
    graph, applied = apply_rules(graph, spec)
    assert "RULE-PRODUCT-SPLIT" in applied
    by_id = {t["id"]: t for t in graph}
    assert "TASK-IMPL.BACKEND" in by_id
    assert by_id["TASK-IMPL"]["execution_type"] == "composite"


def test_rule_perf_fires():
    graph = build_skeleton()
    spec = {"constraints": {"hard": [], "soft": []},
            "scope": {"type": "unknown"}, "success_signals": ["Search latency < 5s"]}
    graph, applied = apply_rules(graph, spec)
    assert "RULE-PERF" in applied
    assert "TASK-IMPL.PERF" in {t["id"] for t in graph}


def test_no_rules_fire_on_minimal_spec():
    graph = build_skeleton()
    spec = {"constraints": {"hard": [], "soft": []},
            "scope": {"type": "unknown"}, "success_signals": []}
    graph, applied = apply_rules(graph, spec)
    assert applied == []
    assert len(graph) == 4


def test_database_and_product_split_combined():
    graph = build_skeleton()
    spec = {"constraints": {"hard": ["database required"], "soft": []},
            "scope": {"type": "product"}, "success_signals": []}
    graph, applied = apply_rules(graph, spec)
    assert "RULE-DATABASE" in applied
    assert "RULE-PRODUCT-SPLIT" in applied
    by_id = {t["id"]: t for t in graph}
    # Children inherit TASK-DESIGN.SCHEMA dep (from IMPL after database rule)
    assert by_id["TASK-IMPL.BACKEND"]["deps"] == ["TASK-DESIGN.SCHEMA"]
    assert by_id["TASK-IMPL.FRONTEND"]["deps"] == ["TASK-DESIGN.SCHEMA"]
