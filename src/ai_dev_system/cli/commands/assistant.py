"""ai-dev assistant — conversational assistant over the owned harness, with
durable memory, sessions, and budget (Plan 2). Local REPL surface."""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

import typer

from ai_dev_system.cli.core.registry import command

if TYPE_CHECKING:
    from ai_dev_system.assistant.agent import Assistant

_SYSTEM_PROMPT = (
    "You are ai-dev's internal assistant. You own your tool-use loop. "
    "Use the 'now' tool for the current time. Use the 'memory' tool to durably "
    "record facts about yourself (MEMORY) or the operator (USER) when worth remembering."
)


def build_assistant(model: str | None) -> "Assistant":
    from ai_dev_system.config import Config
    from ai_dev_system.db.connection import get_connection
    from ai_dev_system.db.migrator import apply_schema
    from ai_dev_system.harness.tools.registry import ToolRegistry
    from ai_dev_system.harness.tools.builtin import now_tool
    from ai_dev_system.harness.tools.memory_tool import make_memory_tool
    from ai_dev_system.harness.permissions import make_permission_callback
    from ai_dev_system.harness.runtime import SdkAgentRuntime
    from ai_dev_system.assistant.memory import MemoryStore, assistant_home
    from ai_dev_system.assistant.session import SessionStore
    from ai_dev_system.assistant.budget import BudgetTracker
    from ai_dev_system.assistant.agent import Assistant

    cfg = Config.from_env()
    _init_conn = get_connection(cfg.database_url)
    try:
        apply_schema(_init_conn)
    finally:
        _init_conn.close()

    def conn_factory():
        return get_connection(cfg.database_url)

    store = MemoryStore(assistant_home())
    registry = ToolRegistry()
    registry.register(now_tool, "now")
    registry.register(make_memory_tool(store), "memory")

    runtime = SdkAgentRuntime(
        registry=registry,
        permission_callback=make_permission_callback(),
        model=model,
    )
    sessions = SessionStore(conn_factory)
    session_id = sessions.load_or_create("local", "cli")
    cap = os.environ.get("AI_DEV_ASSISTANT_BUDGET_USD")
    return Assistant(
        runtime=runtime,
        memory_store=store,
        session_store=sessions,
        budget=BudgetTracker(conn_factory),
        base_prompt=_SYSTEM_PROMPT,
        session_id=session_id,
        cap_usd=float(cap) if cap else None,
    )


@command(verb="assistant", help="Launch the conversational assistant (local REPL).")
def assistant_cmd(
    model: str = typer.Option(None, "--model", help="Model alias (default: account default)."),
) -> None:
    from ai_dev_system.gateway.local_cli import run_repl
    from ai_dev_system.assistant.memory import assistant_home
    from ai_dev_system.assistant.session import (
        consume_clean_shutdown, mark_clean_shutdown,
    )

    home = assistant_home()
    asst = build_assistant(model=model)
    if not consume_clean_shutdown(home):
        asst.mark_resume()
        typer.echo("(resumed previous session)")
    try:
        run_repl(asst)
        raise typer.Exit(0)
    finally:
        mark_clean_shutdown(home)
