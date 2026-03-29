from ai_dev_system.task_graph.skeleton import build_skeleton
from ai_dev_system.task_graph.rules import add_parallel
from ai_dev_system.task_graph.validator import validate_graph


def _make_node(id, **kw):
    base = {"id": id, "title": id, "phase": "implement", "group": "g",
            "execution_type": "atomic", "type": "coding", "agent_type": "Dev",
            "required_inputs": [], "expected_outputs": [], "done_definition": "d",
            "enriched_by": "rule", "created_by_rule": "TEST"}
    base.update(kw)
    return base


def test_valid_skeleton():
    assert validate_graph(build_skeleton()) == []


def test_missing_core_node():
    graph = build_skeleton()
    graph = [t for t in graph if t["id"] != "TASK-PARSE"]
    errors = validate_graph(graph)
    assert any("Missing core" in e for e in errors)


def test_unknown_dep():
    graph = build_skeleton()
    graph[1]["deps"] = ["NONEXISTENT"]
    errors = validate_graph(graph)
    assert any("unknown" in e for e in errors)


def test_duplicate_id():
    graph = build_skeleton()
    graph.append(dict(graph[0]))
    errors = validate_graph(graph)
    assert any("Duplicate" in e for e in errors)


def test_cycle_detection():
    graph = build_skeleton()
    graph[0]["deps"] = ["TASK-VALIDATE"]
    errors = validate_graph(graph)
    assert any("cycle" in e.lower() for e in errors)


def test_composite_without_children():
    graph = build_skeleton()
    graph[2]["execution_type"] = "composite"
    errors = validate_graph(graph)
    assert any("no children" in e.lower() for e in errors)


def test_valid_composite_with_children():
    graph = build_skeleton()
    graph, _ = add_parallel(graph, "TASK-IMPL", [_make_node("TASK-IMPL.A")])
    assert validate_graph(graph) == []
