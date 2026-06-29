def test_build_assistant_returns_assistant_with_memory_and_now_tools(tmp_path, monkeypatch):
    # Isolate the assistant home + DB so the test doesn't touch the real ones.
    monkeypatch.setenv("AI_DEV_ASSISTANT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'ctl.db'}")
    from ai_dev_system.cli.commands.assistant import build_assistant
    from ai_dev_system.assistant.agent import Assistant

    asst = build_assistant(model=None)
    assert isinstance(asst, Assistant)
    # the runtime carries both tools (now + memory)
    names = asst._runtime._registry.allowed_tool_names()
    assert "mcp__ai_dev__now" in names
    assert "mcp__ai_dev__memory" in names


def test_assistant_command_is_registered_on_root_app():
    import ai_dev_system.cli.commands  # noqa: F401
    from ai_dev_system.cli.core.registry import get_app

    names = {c.name for c in get_app().registered_commands}
    assert "assistant" in names
