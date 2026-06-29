from ai_dev_system.harness.runtime import FakeAgentRuntime, TurnResult, TurnEvent
from ai_dev_system.gateway.local_cli import run_repl


def _input_seq(lines):
    it = iter(lines)

    def _fn(_prompt=""):
        try:
            return next(it)
        except StopIteration:
            raise EOFError

    return _fn


def test_repl_prints_assistant_reply_then_exits():
    scripted = TurnResult("the time is noon",
                          [TurnEvent("tool_use", {"name": "mcp__ai_dev__now"})],
                          {}, None, None)
    runtime = FakeAgentRuntime(scripted)
    out = []
    run_repl(runtime, "sys", input_fn=_input_seq(["what time is it?", "exit"]),
             output_fn=out.append)
    joined = "\n".join(out)
    assert "[tool] mcp__ai_dev__now" in joined
    assert "assistant> the time is noon" in joined
    assert runtime.calls == [("sys", "what time is it?")]


def test_repl_skips_blank_and_stops_on_eof():
    runtime = FakeAgentRuntime(TurnResult("x", [], {}, None, None))
    out = []
    run_repl(runtime, "sys", input_fn=_input_seq(["   "]), output_fn=out.append)
    # blank skipped, then EOF ends the loop → no run_turn call
    assert runtime.calls == []
