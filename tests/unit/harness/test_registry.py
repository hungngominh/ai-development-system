from ai_dev_system.harness.tools.registry import ToolRegistry, SERVER_NAME
from ai_dev_system.harness.tools.builtin import now_tool


def test_server_name_is_ai_dev():
    assert SERVER_NAME == "ai_dev"


def test_allowed_tool_names_use_mcp_prefix():
    reg = ToolRegistry()
    reg.register(now_tool, "now")
    assert reg.allowed_tool_names() == ["mcp__ai_dev__now"]


def test_tools_returns_registered_tools():
    reg = ToolRegistry()
    reg.register(now_tool, "now")
    assert reg.tools() == [now_tool]


def test_build_server_returns_a_config_object():
    reg = ToolRegistry()
    reg.register(now_tool, "now")
    server = reg.build_server()
    assert server is not None
