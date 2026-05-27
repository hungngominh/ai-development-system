"""Legacy aliases preserved during migration window (decision #17).

`ai-dev start` and `ai-dev run` continue to work for the deprecation period.
At T+12w (per Migration Plan) these are removed in favor of:
    ai-dev start  ->  ai-dev intake start  (new pipeline)
                  ->  bridges to legacy code via run.legacy=true flag
    ai-dev run    ->  ai-dev phase-b run
"""
from __future__ import annotations

import sys
from typing import Optional

import typer

from ai_dev_system.cli.core.registry import command


@command(
    verb="start",
    help="[legacy] Start Phase 1a. Use 'ai-dev intake start' for new projects.",
    deprecated=True,
)
def start_legacy(
    project_name: str = typer.Option(..., "--project-name", help="Project name"),
    idea: str = typer.Option("", "--idea", help="Raw idea text"),
    constraints: str = typer.Option("", "--constraints", help="Hard/soft constraints"),
) -> None:
    """Legacy entry point — delegates to existing start_project.main()."""
    from ai_dev_system.cli.start_project import main as start_main

    argv = ["--project-name", project_name]
    if idea:
        argv += ["--idea", idea]
    if constraints:
        argv += ["--constraints", constraints]
    sys.exit(start_main(argv))


@command(
    verb="run",
    help="[legacy] Run Phase B pipeline. Use 'ai-dev phase-b run' once available.",
    deprecated=True,
)
def run_legacy(
    run_id: str = typer.Option(..., "--run-id", help="Run UUID to advance"),
) -> None:
    """Legacy entry point — delegates to existing run_phase_b.main()."""
    from ai_dev_system.cli.run_phase_b import main as run_main

    sys.exit(run_main(["--run-id", run_id]))
