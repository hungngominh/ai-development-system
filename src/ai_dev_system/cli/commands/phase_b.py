"""ai-dev phase-b — Phase B execution pipeline commands.

Verbs:
- run     — run the Phase B worker loop for an approved task graph
- resume  — resume a paused Phase B run
- abort   — abort a running Phase B execution
"""
from __future__ import annotations

import typer

from ai_dev_system.cli.core.output import OutputRenderer
from ai_dev_system.cli.core.registry import command


@command(
    noun="phase-b",
    verb="run",
    help="Run Phase B execution pipeline for an approved task graph",
    noun_help="Phase B — execution pipeline (worker loop)",
)
def phase_b_run(
    run_id: str = typer.Option(..., "--run-id", help="Run UUID with an approved task graph"),
    auto_approve: bool = typer.Option(
        False, "--auto-approve",
        help="Skip interactive Gate 2 review and approve the task graph as generated.",
    ),
    json_output: bool = typer.Option(False, "--json"),
    quiet: bool = typer.Option(False, "--quiet"),
) -> None:
    """Execute the approved task graph for a run."""
    from ai_dev_system.cli.run_phase_b import main as run_main
    import sys

    out = OutputRenderer(mode="json" if json_output else "human", quiet=quiet)
    out.progress(f"Starting Phase B execution for run {run_id}...")
    argv = ["--run-id", run_id]
    if auto_approve:
        argv.append("--auto-approve")
    sys.exit(run_main(argv))


@command(noun="phase-b", verb="resume", help="Resume a paused Phase B execution")
def phase_b_resume(
    run_id: str = typer.Option(..., "--run-id", help="Run UUID to resume"),
    json_output: bool = typer.Option(False, "--json"),
    quiet: bool = typer.Option(False, "--quiet"),
) -> None:
    """Resume Phase B from where it paused (e.g., after escalation resolved)."""
    from ai_dev_system.cli.run_phase_b import main as run_main
    import sys

    out = OutputRenderer(mode="json" if json_output else "human", quiet=quiet)
    out.progress(f"Resuming Phase B for run {run_id}...")
    sys.exit(run_main(["--run-id", run_id]))


@command(noun="phase-b", verb="abort", help="Abort a Phase B execution")
def phase_b_abort(
    run_id: str = typer.Option(..., "--run-id", help="Run UUID to abort"),
    reason: str = typer.Option("user_requested", "--reason", help="Abort reason"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Abort Phase B execution and mark run ABORTED."""
    from ai_dev_system.config import Config
    from ai_dev_system.db.connection import get_connection
    from ai_dev_system.db.repos.events import EventRepo

    out = OutputRenderer(mode="json" if json_output else "human")
    config = Config.from_env()
    conn = get_connection(config.database_url)

    try:
        row = conn.execute(
            "SELECT status FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            out.write_error(code=1, message=f"Run {run_id!r} not found")
            raise typer.Exit(1)

        allowed = {"RUNNING_PHASE_B", "PAUSED_PHASE_B", "RUNNING_PHASE_1D"}
        if row["status"] not in allowed:
            out.write_error(
                code=1,
                message=f"Run {run_id!r} is not in a Phase B state",
                run_status=row["status"],
                hint=f"Abortable states: {sorted(allowed)}",
            )
            raise typer.Exit(1)

        conn.execute(
            """UPDATE runs SET status = 'ABORTED',
               last_activity_at = CURRENT_TIMESTAMP, completed_at = CURRENT_TIMESTAMP
               WHERE run_id = ?""",
            (run_id,),
        )
        EventRepo(conn).insert(
            run_id, "PHASE_B_ABORTED", "phase_b_abort",
            payload={"reason": reason},
        )
        conn.commit()

        out.write({"status": "aborted", "run_id": run_id, "reason": reason})
        raise typer.Exit(0)
    except typer.Exit:
        raise
    except Exception as exc:
        out.write_error(code=1, message=f"phase-b abort failed: {exc}")
        raise typer.Exit(1)
    finally:
        conn.close()
