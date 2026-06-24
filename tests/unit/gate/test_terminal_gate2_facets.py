import io

from ai_dev_system.gate.terminal_gate2 import TerminalGate2IO


def _envelope():
    # Use the real 4-task skeleton so `approve` (which re-runs validate_graph)
    # passes; attach facets to the coding task.
    from ai_dev_system.task_graph.skeleton import build_skeleton
    tasks = build_skeleton()
    impl = next(t for t in tasks if t["id"] == "TASK-IMPL")
    impl["facets"] = {
        "input": {"status": "needs_human", "content": "", "reason": ""},
        "database": {"status": "filled", "content": "adds users table", "reason": ""},
    }
    return {"tasks": tasks}


def _run(commands):
    it = iter(commands)
    out = io.StringIO()
    gate = TerminalGate2IO(prompt_fn=lambda *a: next(it), out=out)
    status, edited = gate.collect_edits(_envelope())
    return status, edited, out.getvalue()


def _impl_task(edited):
    return next(t for t in edited["tasks"] if t["id"] == "TASK-IMPL")


def test_facet_set_marks_filled():
    status, edited, _ = _run(["facet set TASK-IMPL input 'a CSV upload'", "approve"])
    f = _impl_task(edited)["facets"]["input"]
    assert f["status"] == "filled" and f["content"] == "a CSV upload"
    assert status == "approve"


def test_facet_na_marks_na_with_reason():
    status, edited, _ = _run(["facet na TASK-IMPL database 'no persistence'", "approve"])
    f = _impl_task(edited)["facets"]["database"]
    assert f["status"] == "na" and f["reason"] == "no persistence"


def test_facet_show_renders_facets():
    _, _, text = _run(["facet show TASK-IMPL", "approve"])
    assert "input" in text and "needs_human" in text


def test_approve_warns_on_needs_human_but_proceeds():
    status, _, text = _run(["approve"])
    assert status == "approve"           # not blocked
    assert "needs_human" in text or "needs clarification" in text  # warned


def test_render_shows_facet_summary():
    _, _, text = _run(["list", "approve"])
    assert "facets:" in text  # e.g. "facets: 1 filled / 1 needs-human / 0 N/A"
