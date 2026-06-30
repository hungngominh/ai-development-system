"""ai-dev phase-b — Phase B execution pipeline commands.

Verbs:
- run          — run the Phase B worker loop for an approved task graph
- resume       — resume a paused Phase B run
- abort        — abort a running Phase B execution
- to-gate2     — run Phase B to Gate 2 and pause (detached spawn target)
- resume-gate2 — resume Phase B after Gate 2 approval/rejection (detached spawn target)
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


@command(
    noun="phase-b",
    verb="to-gate2",
    help="Run Phase B to Gate 2 and pause (detached non-interactive spawn target)",
)
def phase_b_to_gate2(
    run_id: str = typer.Option(..., "--run-id", help="Run UUID in RUNNING_PHASE_1D status"),
    json_output: bool = typer.Option(False, "--json"),
    quiet: bool = typer.Option(False, "--quiet"),
) -> None:
    """Run Phase B (finalize_spec → task_graph) and pause at PAUSED_AT_GATE_2.

    Intended as a detached spawn target from the harness. Exits 0 on success
    (status PAUSED_AT_GATE_2), exits 1 on error.
    """
    from ai_dev_system.cli.run_phase_b_gate2 import main as gate2_main
    import sys

    out = OutputRenderer(mode="json" if json_output else "human", quiet=quiet)
    out.progress(f"Running Phase B to Gate 2 for run {run_id}...")
    sys.exit(gate2_main(["--mode", "to-gate2", "--run-id", run_id]))


@command(
    noun="phase-b",
    verb="resume-gate2",
    help="Resume Phase B after Gate 2 approval/rejection (detached non-interactive spawn target)",
)
def phase_b_resume_gate2(
    run_id: str = typer.Option(..., "--run-id", help="Run UUID in PAUSED_AT_GATE_2 status"),
    decision: str = typer.Option(..., "--decision", help="approve or reject"),
    json_output: bool = typer.Option(False, "--json"),
    quiet: bool = typer.Option(False, "--quiet"),
) -> None:
    """Resume Phase B after Gate 2: approve→execute/verify, reject→abort.

    Intended as a detached spawn target from the harness. Exits 0 on both
    approve and reject (reject is a normal outcome). Exits 1 on unexpected errors.
    """
    from ai_dev_system.cli.run_phase_b_gate2 import main as gate2_main
    import sys

    if decision not in ("approve", "reject"):
        out = OutputRenderer(mode="json" if json_output else "human", quiet=quiet)
        out.write_error(code=1, message=f"--decision must be 'approve' or 'reject', got {decision!r}")
        raise typer.Exit(1)

    out = OutputRenderer(mode="json" if json_output else "human", quiet=quiet)
    out.progress(f"Resuming Phase B after Gate 2 (decision={decision}) for run {run_id}...")
    sys.exit(gate2_main(["--mode", "resume-gate2", "--run-id", run_id, "--decision", decision]))
