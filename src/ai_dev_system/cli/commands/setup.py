"""Wraps the existing setup_wizard in the new @command framework."""
from __future__ import annotations

import typer

from ai_dev_system.cli.core.registry import command


@command(verb="setup", help="Interactive setup wizard (config + DB migrations + skills)")
def setup_cmd(
    ctx: typer.Context,
) -> None:
    """Run the setup wizard. Existing implementation in setup_wizard.run_setup()."""
    from ai_dev_system.cli.setup_wizard import run_setup

    run_setup()
