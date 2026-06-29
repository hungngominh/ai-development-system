"""Unit tests for the `ai-dev assistant` CLI command (Task 7).

Tests cover:
- build_assistant wiring: ToolRegistry contains the 'now' tool, system prompt references the assistant
- Command registration: 'assistant' is present as a top-level registered command after import
"""
from __future__ import annotations


def test_build_assistant_registers_now_tool():
    from ai_dev_system.cli.commands.assistant import build_assistant
    runtime, system_prompt = build_assistant(model=None)
    assert runtime._registry.allowed_tool_names() == ["mcp__ai_dev__now"]
    assert "assistant" in system_prompt.lower() or "ai-dev" in system_prompt.lower()


def test_assistant_command_is_registered_on_root_app():
    # Importing the commands package triggers @command registration.
    import ai_dev_system.cli.commands  # noqa: F401
    from ai_dev_system.cli.core.registry import get_app

    app = get_app()
    names = {c.name for c in app.registered_commands}
    assert "assistant" in names
