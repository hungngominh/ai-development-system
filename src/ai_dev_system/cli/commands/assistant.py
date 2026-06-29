"""ai-dev assistant — conversational assistant over the owned harness, with
durable memory, sessions, and budget (Plan 2). Local REPL surface."""
from __future__ import annotations

from typing import TYPE_CHECKING

import typer

from ai_dev_system.cli.core.registry import command

if TYPE_CHECKING:
    from ai_dev_system.assistant.agent import Assistant


def build_assistant(model: str | None) -> "Assistant":
    from ai_dev_system.assistant.factory import build_assistant_factory
    return build_assistant_factory(model).for_chat("local", "cli")


@command(verb="assistant", help="Launch the conversational assistant (local REPL).")
def assistant_cmd(
    model: str = typer.Option(None, "--model", help="Model alias (default: account default)."),
) -> None:
    import sys

    from ai_dev_system.gateway.local_cli import run_repl
    from ai_dev_system.assistant.memory import assistant_home
    from ai_dev_system.assistant.session import (
        consume_clean_shutdown, mark_clean_shutdown,
    )

    # The assistant may reply in any language (e.g. Vietnamese); force UTF-8 stdout
    # so a non-ASCII reply never crashes the REPL on a Windows cp1252 console.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - stdout may not support reconfigure
        pass

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
