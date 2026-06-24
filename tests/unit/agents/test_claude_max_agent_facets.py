from ai_dev_system.agents.claude_max_agent import ClaudeMaxAgent


def _agent():
    return ClaudeMaxAgent.__new__(ClaudeMaxAgent)  # no __init__ needed for _build_user


def test_filled_facets_render_section():
    ctx = {"task_id": "T", "objective": "o", "facets": {
        "input": {"status": "filled", "content": "a CSV file", "reason": ""},
        "database": {"status": "na", "content": "", "reason": "stateless"},
        "auth_permission": {"status": "needs_human", "content": "", "reason": ""},
    }}
    out = _agent()._build_user("T", ctx, [])
    assert "## Task Specification" in out
    assert "input: a CSV file" in out
    assert "database" not in out.split("## Task Specification")[1]  # na hidden
    assert "auth_permission" in out and "needs clarification" in out  # needs_human flagged


def test_no_section_when_no_useful_facets():
    ctx = {"task_id": "T", "objective": "o", "facets": {
        "input": {"status": "na", "content": "", "reason": "n/a"},
    }}
    out = _agent()._build_user("T", ctx, [])
    assert "## Task Specification" not in out


def test_no_section_when_facets_absent():
    out = _agent()._build_user("T", {"task_id": "T", "objective": "o"}, [])
    assert "## Task Specification" not in out
