"""ai-dev info — run status + artifact overview.

Top-level command: ai-dev info <run-id>
Also exposes: ai-dev info config
"""
from __future__ import annotations

from typing import Optional

import typer

from ai_dev_system.cli.core.output import OutputRenderer
from ai_dev_system.cli.core.registry import command


@command(
    verb="info",
    help="Show run status, artifacts, and next recommended action",
)
def info_cmd(
    run_id: Optional[str] = typer.Argument(None, help="Run UUID (omit for 'info config')"),
    show_config: bool = typer.Option(False, "--config", help="Show active config + feature flags"),
    json_output: bool = typer.Option(False, "--json"),
    quiet: bool = typer.Option(False, "--quiet"),
) -> None:
    """Print run status, artifacts, and next step recommendation."""
    out = OutputRenderer(mode="json" if json_output else "human", quiet=quiet)

    if show_config or run_id is None:
        _show_config(out)
        raise typer.Exit(0)

    _show_run(run_id, out)
    raise typer.Exit(0)


def _show_config(out: OutputRenderer) -> None:
    from ai_dev_system.config import Config
    from ai_dev_system.feature_flags import FeatureFlags

    try:
        cfg = Config.from_env()
        flags = FeatureFlags.from_env()
        out.write({
            "status": "ok",
            "database_url": _mask_creds(cfg.database_url),
            "storage_root": str(cfg.storage_root),
            "llm_provider": getattr(cfg, "llm_provider", "unknown"),
            "feature_flags": {
                "use_intake_wizard": flags.use_intake_wizard,
                "use_question_pipeline_v2": flags.use_question_pipeline_v2,
                "use_debate_v2": flags.use_debate_v2,
            },
        })
    except Exception as exc:
        out.write_error(code=3, message=f"Config load failed: {exc}")
        raise typer.Exit(3)


def _show_run(run_id: str, out: OutputRenderer) -> None:
    from ai_dev_system.config import Config
    from ai_dev_system.db.connection import get_connection

    config = Config.from_env()
    conn = get_connection(config.database_url)

    try:
        row = conn.execute(
            """SELECT run_id, project_id, status, pipeline_version, legacy,
                      created_at, last_activity_at, completed_at, intake_brief_id
               FROM runs WHERE run_id = ?""",
            (run_id,),
        ).fetchone()
        if row is None:
            out.write_error(code=1, message=f"Run {run_id!r} not found")
            raise typer.Exit(1)

        artifacts = conn.execute(
            "SELECT artifact_type, artifact_id FROM artifacts WHERE run_id = ? ORDER BY created_at",
            (run_id,),
        ).fetchall()

        payload = {
            "run_id": row["run_id"],
            "project_id": row["project_id"],
            "status": row["status"],
            "pipeline_version": row["pipeline_version"],
            "legacy": bool(row["legacy"]),
            "created_at": row["created_at"],
            "last_activity_at": row["last_activity_at"],
            "completed_at": row["completed_at"],
            "artifacts": [
                {"type": a["artifact_type"], "id": a["artifact_id"]}
                for a in artifacts
            ],
            "next_step": _recommend_next(row["status"]),
        }
        out.write(payload)
    finally:
        conn.close()


def _recommend_next(status: str) -> str:
    _MAP = {
        "COLLECTING_INTAKE": "ai-dev intake resume --run-id <id>",
        "INTAKE_COMPLETE": "ai-dev debate start --run-id <id>",
        "RUNNING_PHASE_1A": "wait — debate in progress",
        "DEBATE_COMPLETE": "ai-dev gate review-debate --run-id <id>",
        "RUNNING_PHASE_1D": "ai-dev phase-b run --run-id <id>",
        "RUNNING_PHASE_B": "wait — Phase B in progress",
        "PHASE_B_COMPLETE": "ai-dev gate review-verification --run-id <id>",
        "ABORTED": "start a new run: ai-dev intake start",
        "COMPLETE": "done",
    }
    return _MAP.get(status, f"unknown status {status!r}")


def _mask_creds(url: str) -> str:
    import re
    return re.sub(r"://([^:@]+):([^@]+)@", r"://\1:***@", url or "")
