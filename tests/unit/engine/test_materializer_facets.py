from ai_dev_system.engine.materializer import _build_context


def test_build_context_includes_facets_when_present():
    task = {"id": "TASK-IMPL", "type": "coding",
            "facets": {"input": {"status": "filled", "content": "a CSV", "reason": ""}}}
    ctx = _build_context(task)
    assert ctx["facets"]["input"]["content"] == "a CSV"


def test_build_context_facets_default_empty():
    ctx = _build_context({"id": "TASK-DESIGN"})
    assert ctx["facets"] == {}
