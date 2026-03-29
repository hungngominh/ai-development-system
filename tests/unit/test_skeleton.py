from ai_dev_system.task_graph.skeleton import build_skeleton, CORE_SKELETON


def test_skeleton_has_4_nodes():
    graph = build_skeleton()
    assert len(graph) == 4


def test_skeleton_correct_ids():
    graph = build_skeleton()
    ids = {t["id"] for t in graph}
    assert ids == {"TASK-PARSE", "TASK-DESIGN", "TASK-IMPL", "TASK-VALIDATE"}


def test_skeleton_correct_deps():
    graph = build_skeleton()
    by_id = {t["id"]: t for t in graph}
    assert by_id["TASK-PARSE"]["deps"] == []
    assert by_id["TASK-DESIGN"]["deps"] == ["TASK-PARSE"]
    assert by_id["TASK-IMPL"]["deps"] == ["TASK-DESIGN"]
    assert by_id["TASK-VALIDATE"]["deps"] == ["TASK-IMPL"]


def test_skeleton_all_atomic():
    graph = build_skeleton()
    assert all(t["execution_type"] == "atomic" for t in graph)


def test_skeleton_returns_deep_copy():
    g1 = build_skeleton()
    g2 = build_skeleton()
    g1[0]["title"] = "mutated"
    assert g2[0]["title"] != "mutated"
    assert CORE_SKELETON[0]["title"] != "mutated"
