from types import SimpleNamespace

from ai_dev_system.harness.runtime import SdkAgentRuntime
from ai_dev_system.harness.tools.registry import ToolRegistry
from ai_dev_system.harness.tools.builtin import now_tool
from ai_dev_system.harness.permissions import make_permission_callback


def _fake_query_factory(captured):
    async def fake_query(*, prompt, options):
        captured["prompt"] = prompt
        captured["options"] = options
        yield SimpleNamespace(content=[SimpleNamespace(text="done")], usage={})
        yield SimpleNamespace(total_cost_usd=0.02,
                              usage={"input_tokens": 1, "output_tokens": 2},
                              session_id="s1", result=None)
    return fake_query


def _runtime(captured):
    reg = ToolRegistry()
    reg.register(now_tool, "now")
    return SdkAgentRuntime(
        registry=reg,
        permission_callback=make_permission_callback(),
        model=None,
        query_fn=_fake_query_factory(captured),
    )


def test_run_turn_reduces_fake_query_output():
    captured = {}
    result = _runtime(captured).run_turn("you are ai-dev", "hi")
    assert result.final_text == "done"
    assert result.cost_usd == 0.02
    assert result.session_id == "s1"


def test_run_turn_passes_prompt_and_builds_options():
    captured = {}
    _runtime(captured).run_turn("you are ai-dev", "what time is it?")
    assert captured["prompt"] == "what time is it?"
    opts = captured["options"]
    assert opts.system_prompt == "you are ai-dev"
    assert opts.allowed_tools == ["mcp__ai_dev__now"]
    assert "ai_dev" in opts.mcp_servers
    assert opts.can_use_tool is not None
