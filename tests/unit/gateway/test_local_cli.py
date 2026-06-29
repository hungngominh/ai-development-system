from ai_dev_system.harness.runtime import TurnResult, TurnEvent
from ai_dev_system.gateway.local_cli import run_repl


class _FakeResponder:
    def __init__(self, result):
        self._result = result
        self.calls = []

    def respond(self, text):
        self.calls.append(text)
        return self._result


def _input_seq(lines):
    it = iter(lines)

    def _fn(_prompt=""):
        try:
            return next(it)
        except StopIteration:
            raise EOFError

    return _fn


def test_repl_prints_assistant_reply_then_exits():
    result = TurnResult("the time is noon",
                        [TurnEvent("tool_use", {"name": "mcp__ai_dev__now"})],
                        {}, None, None)
    responder = _FakeResponder(result)
    out = []
    run_repl(responder, input_fn=_input_seq(["what time is it?", "exit"]), output_fn=out.append)
    joined = "\n".join(out)
    assert "[tool] mcp__ai_dev__now" in joined
    assert "assistant> the time is noon" in joined
    assert responder.calls == ["what time is it?"]


def test_repl_skips_blank_and_stops_on_eof():
    responder = _FakeResponder(TurnResult("x", [], {}, None, None))
    out = []
    run_repl(responder, input_fn=_input_seq(["   "]), output_fn=out.append)
    assert responder.calls == []
