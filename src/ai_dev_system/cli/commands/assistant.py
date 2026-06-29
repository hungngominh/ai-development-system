"""ai-dev assistant -- launch the conversational assistant (local REPL surface).

Plan 1 scope: a single-turn REPL over the owned harness with the `now` proof tool.
Memory, persistent sessions, budget, and chat surfaces arrive in later plans."""
from __future__ import annotations

from typing import TYPE_CHECKING

import typer

from ai_dev_system.cli.core.registry import command

if TYPE_CHECKING:
    from ai_dev_system.harness.runtime import SdkAgentRuntime

_SYSTEM_PROMPT = (
    "You are ai-dev's internal assistant. You own your tool-use loop. "
    "When the user asks for the current time, call the 'now' tool and report it."
)


def build_assistant(model: str | None) -> tuple[SdkAgentRuntime, str]:
    from ai_dev_system.harness.tools.registry import ToolRegistry
    from ai_dev_system.harness.tools.builtin import now_tool
    from ai_dev_system.harness.permissions import make_permission_callback
    from ai_dev_system.harness.runtime import SdkAgentRuntime

    registry = ToolRegistry()
    registry.register(now_tool, "now")
    runtime = SdkAgentRuntime(
        registry=registry,
        permission_callback=make_permission_callback(),
        model=model,
    )
    return runtime, _SYSTEM_PROMPT


@command(verb="assistant", help="Launch the conversational assistant (local REPL).")
def assistant_cmd(
    model: str = typer.Option(None, "--model", help="Model alias (default: SDK/account default)."),
) -> None:
    from ai_dev_system.gateway.local_cli import run_repl

    runtime, system_prompt = build_assistant(model=model)
    run_repl(runtime, system_prompt)
    raise typer.Exit(0)
