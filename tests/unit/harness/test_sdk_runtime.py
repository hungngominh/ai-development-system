from types import SimpleNamespace

from ai_dev_system.harness.runtime import SdkAgentRuntime
from ai_dev_system.harness.tools.registry import ToolRegistry
from ai_dev_system.harness.tools.builtin import now_tool
from ai_dev_system.harness.permissions import make_permission_callback


class _FakeClient:
    def __init__(self, options, scripted, captured):
        self._scripted = scripted
        self._captured = captured
        captured["options"] = options

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def query(self, prompt, session_id="default"):
        self._captured["prompt"] = prompt

    async def receive_response(self):
        for m in self._scripted:
            yield m


def _runtime(captured, scripted):
    reg = ToolRegistry()
    reg.register(now_tool, "now")
    return SdkAgentRuntime(
        registry=reg,
        permission_callback=make_permission_callback(),
        model=None,
        client_factory=lambda options: _FakeClient(options, scripted, captured),
    )


def test_run_turn_reduces_client_output():
    captured = {}
    scripted = [
        SimpleNamespace(content=[SimpleNamespace(text="done")], usage={}),
        SimpleNamespace(total_cost_usd=0.02,
                        usage={"input_tokens": 1, "output_tokens": 2},
                        session_id="s1", result=None),
    ]
    result = _runtime(captured, scripted).run_turn("you are ai-dev", "hi")
    assert result.final_text == "done"
    assert result.cost_usd == 0.02
    assert result.session_id == "s1"


def test_run_turn_sends_prompt_and_builds_options():
    captured = {}
    _runtime(
        captured,
        [SimpleNamespace(total_cost_usd=0.0, usage={}, session_id="s", result=None)],
    ).run_turn("you are ai-dev", "what time is it?")
    # contract: prompt sent via client.query (streaming path), no ValueError
    assert captured["prompt"] == "what time is it?"
    opts = captured["options"]
    assert opts.system_prompt == "you are ai-dev"
    assert opts.allowed_tools == ["mcp__ai_dev__now"]
    assert "ai_dev" in opts.mcp_servers
    assert opts.can_use_tool is not None
    assert opts.max_turns == 20
