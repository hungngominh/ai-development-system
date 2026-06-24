"""CLI core: shared parser, output, context, registry."""
from ai_dev_system.cli.core.context import CLIContext
from ai_dev_system.cli.core.output import OutputRenderer
from ai_dev_system.cli.core.registry import command, get_app

__all__ = ["CLIContext", "OutputRenderer", "command", "get_app"]
