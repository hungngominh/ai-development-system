"""ai-dev CLI entry point — typer-based command tree."""
from __future__ import annotations

from typing import Optional

import typer

from ai_dev_system.cli.core.context import CLIContext
from ai_dev_system.cli.core.output import OutputRenderer
from ai_dev_system.cli.core.registry import get_app
from ai_dev_system.feature_flags import (
    FeatureFlagOrderError,
    FeatureFlags,
    parse_feature_overrides,
)

# Import command modules → triggers @command registration onto the root app
from ai_dev_system.cli import commands  # noqa: F401


app = get_app()


@app.callback(invoke_without_command=False)
def _root_callback(
    ctx: typer.Context,
    json_mode: bool = typer.Option(
        False,
        "--json",
        help="Output single-line JSON to stdout, progress to stderr.",
    ),
    quiet: bool = typer.Option(False, "-q", "--quiet", help="Suppress non-error output."),
    verbose: int = typer.Option(0, "-v", "--verbose", count=True, help="Increase verbosity."),
    no_color: bool = typer.Option(False, "--no-color", help="Disable ANSI colors."),
    config_path: Optional[str] = typer.Option(
        None, "--config", help="Override config file path."
    ),
    feature: Optional[list[str]] = typer.Option(
        None,
        "--feature",
        help="Override feature flag: KEY=VALUE. Repeatable.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show planned mutations without applying."
    ),
) -> None:
    """ai-dev — AI Development System CLI."""
    # Parse feature overrides
    try:
        overrides = parse_feature_overrides(feature or [])
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)

    # Build CLIContext with output renderer in selected mode
    output = OutputRenderer(
        mode="json" if json_mode else "human",
        quiet=quiet or json_mode,
        no_color=no_color,
    )

    cli_ctx = CLIContext(
        output=output,
        quiet=quiet,
        verbose_level=verbose,
        dry_run=dry_run,
        config_path=config_path,
        feature_overrides=overrides,
    )

    # Validate feature flag linear order eagerly (decision #18)
    try:
        FeatureFlags.from_env(overrides=overrides)
    except FeatureFlagOrderError as exc:
        output.error(str(exc))
        raise typer.Exit(code=3)

    # Stash on typer context for subcommands
    ctx.obj = cli_ctx

    # Cleanup hook
    ctx.call_on_close(cli_ctx.close)


def main() -> None:
    """Entry point for the `ai-dev` script."""
    app()


if __name__ == "__main__":
    main()
