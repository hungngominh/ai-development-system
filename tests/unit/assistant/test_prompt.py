from ai_dev_system.assistant.memory import Memory
from ai_dev_system.assistant.session import Turn
from ai_dev_system.assistant.prompt import build_system_prompt, render_user_turn


def test_system_prompt_includes_memory_sections():
    out = build_system_prompt("BASE", Memory(agent="agent fact", user="user pref"))
    assert "BASE" in out
    assert "agent fact" in out
    assert "user pref" in out


def test_system_prompt_omits_empty_sections():
    out = build_system_prompt("BASE", Memory(agent="", user=""))
    assert out.strip() == "BASE" or "remember" not in out.lower()


def test_render_user_turn_no_history_is_passthrough():
    assert render_user_turn([], "hello") == "hello"


def test_render_user_turn_includes_history_and_message():
    hist = [Turn("user", "q1"), Turn("assistant", "a1")]
    out = render_user_turn(hist, "q2")
    assert "q1" in out and "a1" in out and "q2" in out
    assert out.rstrip().endswith("q2")  # the new message is last
