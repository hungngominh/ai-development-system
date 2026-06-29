from types import SimpleNamespace

from ai_dev_system.harness.runtime import (
    reduce_messages,
    TurnResult,
    TurnEvent,
    FakeAgentRuntime,
)


def _assistant(blocks):
    return SimpleNamespace(content=blocks, usage={"output_tokens": 1})


def _text(t):
    return SimpleNamespace(text=t)


def _tool_use(name, inp):
    return SimpleNamespace(name=name, input=inp)


def _result(**kw):
    base = dict(total_cost_usd=0.01, usage={"input_tokens": 5, "output_tokens": 7},
                session_id="sess-1", result=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_reduce_collects_text_tooluse_and_result():
    messages = [
        _assistant([_tool_use("mcp__ai_dev__now", {})]),
        _assistant([_text("It is noon.")]),
        _result(),
    ]
    out = reduce_messages(messages)
    assert isinstance(out, TurnResult)
    assert out.final_text == "It is noon."
    assert TurnEvent("tool_use", {"name": "mcp__ai_dev__now", "input": {}}) in out.events
    assert out.cost_usd == 0.01
    assert out.usage == {"input_tokens": 5, "output_tokens": 7}
    assert out.session_id == "sess-1"


def test_reduce_joins_multiple_text_blocks():
    out = reduce_messages([_assistant([_text("a"), _text("b")]), _result()])
    assert out.final_text == "a\nb"


def test_reduce_prefers_result_string_over_text():
    out = reduce_messages([_assistant([_text("ignored")]), _result(result="FINAL")])
    assert out.final_text == "FINAL"


def test_fake_runtime_returns_scripted_and_records_calls():
    scripted = TurnResult("hi", [], {}, None, None)
    fake = FakeAgentRuntime(scripted)
    got = fake.run_turn("sys", "hello")
    assert got is scripted
    assert fake.calls == [("sys", "hello")]
