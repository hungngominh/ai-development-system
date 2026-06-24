"""ai-dev intake — wizard subcommands.

Verbs:
- start    — create a new run + interactive wizard
- resume   — continue a saved (PAUSED) wizard
- abort    — discard an in-progress wizard, mark run ABORTED
- show     — print the live intake_state or promoted brief for a run
"""
from __future__ import annotations

import sys

import typer

from ai_dev_system.cli.core.output import OutputRenderer
from ai_dev_system.cli.core.registry import command


def _stdin_prompt(prompt: str) -> str:
    """Default prompt I/O: write prompt to stderr, read one line from stdin."""
    sys.stderr.write(prompt + "\n> ")
    sys.stderr.flush()
    line = sys.stdin.readline()
    if not line:
        # EOF / pipe closed → treat as `save`
        return "save"
    return line.rstrip("\n")


@command(
    noun="intake",
    verb="start",
    help="Start the intake wizard for a new run",
    noun_help="Intake wizard — structured brief collection (Phase 1a-0)",
)
def intake_start(
    project_name: str = typer.Option(..., "--project-name", help="Human-readable project name"),
    project_id: str = typer.Option(None, "--project-id", help="Existing project id (defaults to slugified name)"),
    template: str = typer.Option("generic_v1", "--template", help="Intake template id"),
    json_output: bool = typer.Option(False, "--json", help="Emit final result as JSON to stdout"),
    quiet: bool = typer.Option(False, "--quiet", help="Suppress progress on stderr"),
    no_llm: bool = typer.Option(False, "--no-llm", help="Disable `?` suggest (skip LLM client setup)"),
    context_dir: str = typer.Option(None, "--context-dir",
        help="Path to existing project directory — auto-fills tech stack, data sources, README, etc."),
) -> None:
    """Run the intake wizard interactively (stdin/stdout)."""
    from ai_dev_system.config import Config
    from ai_dev_system.db.connection import get_connection
    from ai_dev_system.db.migrator import apply_schema
    from ai_dev_system.intake.runner import run_intake

    out = OutputRenderer(mode="json" if json_output else "human", quiet=quiet)
    pid = project_id or _slugify(project_name)

    llm = None
    if not no_llm:
        try:
            from ai_dev_system.llm_factory import make_real_llm_client
            llm = make_real_llm_client()
        except Exception as exc:
            out.warn(f"LLM client unavailable ({exc}). `?` suggest disabled.")

    try:
        config = Config.from_env()
        conn = get_connection(config.database_url)
        apply_schema(conn)  # idempotent — safe to call every run

        result = run_intake(
            conn=conn,
            config=config,
            project_id=pid,
            prompt_fn=_stdin_prompt,
            template_id=template,
            intro_writer=out.progress,
            llm=llm,
            context_dir=context_dir,
        )
        conn.close()

        payload = {
            "status": result.status,
            "run_id": result.run_id,
            "project_id": pid,
            "fields_answered": result.fields_answered,
            "critical_missing": result.critical_missing or [],
        }
        if result.brief_id:
            payload["brief_id"] = result.brief_id

        out.write(payload)
        raise typer.Exit(0)
    except typer.Exit:
        raise
    except Exception as exc:
        out.write_error(code=1, message=f"intake failed: {exc}")
        raise typer.Exit(1)


@command(noun="intake", verb="resume", help="Resume a paused intake wizard")
def intake_resume(
    run_id: str = typer.Option(..., "--run-id", help="Run id from a previous `intake start` that was paused"),
    json_output: bool = typer.Option(False, "--json", help="Emit final result as JSON to stdout"),
    quiet: bool = typer.Option(False, "--quiet", help="Suppress progress on stderr"),
    no_llm: bool = typer.Option(False, "--no-llm", help="Disable `?` suggest (skip LLM client setup)"),
) -> None:
    """Resume an intake wizard from where the user typed `save`."""
    from ai_dev_system.config import Config
    from ai_dev_system.db.connection import get_connection
    from ai_dev_system.db.migrator import apply_schema
    from ai_dev_system.intake.repo import IntakeRepo
    from ai_dev_system.intake.runner import run_intake

    out = OutputRenderer(mode="json" if json_output else "human", quiet=quiet)

    llm = None
    if not no_llm:
        try:
            from ai_dev_system.llm_factory import make_real_llm_client
            llm = make_real_llm_client()
        except Exception as exc:
            out.warn(f"LLM client unavailable ({exc}). `?` suggest disabled.")

    try:
        config = Config.from_env()
        conn = get_connection(config.database_url)
        apply_schema(conn)

        row = conn.execute(
            "SELECT project_id, status, intake_state FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            out.write_error(code=1, message=f"Run {run_id!r} not found")
            raise typer.Exit(1)
        if row["status"] != "COLLECTING_INTAKE":
            out.write_error(
                code=1,
                message=f"Run {run_id!r} not resumable",
                run_status=row["status"],
                hint="Only COLLECTING_INTAKE runs can be resumed",
            )
            raise typer.Exit(1)
        if row["intake_state"] is None:
            out.write_error(
                code=1,
                message=f"Run {run_id!r} has no saved intake state",
                hint="Did the wizard ever reach `save`? Use `ai-dev intake start` to begin a new run.",
            )
            raise typer.Exit(1)

        project_id = row["project_id"]
        # Read template id from the persisted state — don't trust the CLI flag.
        state = IntakeRepo(conn).load_state(run_id)
        template_id = state.template_id if state else "generic_v1"

        result = run_intake(
            conn=conn,
            config=config,
            project_id=project_id,
            prompt_fn=_stdin_prompt,
            template_id=template_id,
            run_id=run_id,
            intro_writer=out.progress,
            llm=llm,
        )
        conn.close()

        payload = {
            "status": result.status,
            "run_id": result.run_id,
            "project_id": project_id,
            "fields_answered": result.fields_answered,
            "critical_missing": result.critical_missing or [],
        }
        if result.brief_id:
            payload["brief_id"] = result.brief_id

        out.write(payload)
        raise typer.Exit(0)
    except typer.Exit:
        raise
    except Exception as exc:
        out.write_error(code=1, message=f"intake resume failed: {exc}")
        raise typer.Exit(1)


@command(noun="intake", verb="abort", help="Abort an in-progress intake wizard")
def intake_abort(
    run_id: str = typer.Option(..., "--run-id", help="Run id to abort"),
    reason: str = typer.Option("user_requested", "--reason", help="Free-text abort reason for the audit log"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Mark the run as ABORTED and clear its intake_state.

    Only runs currently in COLLECTING_INTAKE can be aborted via this command.
    The intake_state JSON is dropped (cannot be resumed afterwards). The brief
    is never promoted.
    """
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
        if row["status"] != "COLLECTING_INTAKE":
            out.write_error(
                code=1,
                message=f"Run {run_id!r} not in COLLECTING_INTAKE",
                run_status=row["status"],
                hint="Only intake-stage runs can be aborted by this command",
            )
            raise typer.Exit(1)

        conn.execute(
            """
            UPDATE runs
            SET status = 'ABORTED', intake_state = NULL,
                last_activity_at = CURRENT_TIMESTAMP, completed_at = CURRENT_TIMESTAMP
            WHERE run_id = ?
            """,
            (run_id,),
        )
        EventRepo(conn).insert(
            run_id, "INTAKE_ABORTED", "intake_wizard",
            payload={"reason": reason},
        )
        conn.commit()

        out.write({"status": "intake_aborted", "run_id": run_id, "reason": reason})
        raise typer.Exit(0)
    except typer.Exit:
        raise
    except Exception as exc:
        out.write_error(code=1, message=f"intake abort failed: {exc}")
        raise typer.Exit(1)
    finally:
        conn.close()


@command(noun="intake", verb="show", help="Print the brief or live state for a run")
def intake_show(
    run_id: str = typer.Option(..., "--run-id", help="Run id"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Print intake_state (in-progress) or INTAKE_BRIEF (promoted)."""
    import json
    from pathlib import Path

    from ai_dev_system.config import Config
    from ai_dev_system.db.connection import get_connection
    from ai_dev_system.intake.repo import IntakeRepo

    out = OutputRenderer(mode="json" if json_output else "human")
    config = Config.from_env()
    conn = get_connection(config.database_url)

    try:
        # Live state first
        repo = IntakeRepo(conn)
        live = repo.load_state(run_id)
        if live is not None:
            out.write({
                "status": "intake_in_progress",
                "run_id": run_id,
                "stage": live.stage,
                "field_idx": live.field_idx,
                "fields_answered": sum(1 for a in live.answers.values() if a.source == "user"),
            })
            raise typer.Exit(0)

        # Otherwise look up the promoted brief
        row = conn.execute(
            "SELECT intake_brief_id FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None or row["intake_brief_id"] is None:
            out.write_error(code=1, message=f"No intake state or brief for run {run_id}")
            raise typer.Exit(1)

        art_row = conn.execute(
            "SELECT content_ref FROM artifacts WHERE artifact_id = ?",
            (row["intake_brief_id"],),
        ).fetchone()
        if art_row is None:
            out.write_error(code=1, message="brief artifact missing on disk")
            raise typer.Exit(1)

        brief_path = Path(art_row["content_ref"]) / "brief.json"
        brief = json.loads(brief_path.read_text(encoding="utf-8"))
        out.write({"status": "intake_complete", "run_id": run_id, "brief": brief})
        raise typer.Exit(0)
    finally:
        conn.close()


def _slugify(text: str) -> str:
    """Best-effort: lowercase, replace non-alphanumeric with '-', collapse runs."""
    import re
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return s or "project"
