"""ai-dev migrate — Phase 1 v2 migration utilities.

Verbs:
- classify-runs   — scan all runs, classify v1/v2, write migration_audit rows (idempotent).
- status          — show current DB schema version + run counts by pipeline version.

This command is a one-shot backfill helper. The schema migration in
`db/migrator._apply_v5_add_columns()` already flips `runs.legacy = 1` for
pre-v5 rows; this command additionally records WHY each run got that flag in
the `migration_audit` table so post-migration analysis is possible.
"""
from __future__ import annotations

import typer

from ai_dev_system.cli.core.output import OutputRenderer
from ai_dev_system.cli.core.registry import command


@command(
    noun="migrate",
    verb="classify-runs",
    help="Classify all runs (v1_continue / v2_new / v2_resume / abort), write migration_audit",
    noun_help="Migration utilities (Phase 1 v1 -> v2)",
)
def migrate_classify_runs(
    json_output: bool = typer.Option(False, "--json", help="Emit summary as JSON to stdout"),
    quiet: bool = typer.Option(False, "--quiet"),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Classify + print, do NOT insert migration_audit rows or commit",
    ),
) -> None:
    """Backfill `migration_audit` with one row per run. Re-running is safe (skips
    runs that already have an audit row for migration_version=5)."""
    from ai_dev_system.config import Config
    from ai_dev_system.db.connection import get_connection
    from ai_dev_system.db.migrator import apply_schema
    from ai_dev_system.migration.classify import (
        classify_all_runs, classify_run, summarise,
    )

    out = OutputRenderer(mode="json" if json_output else "human", quiet=quiet)
    config = Config.from_env()
    conn = get_connection(config.database_url)

    try:
        apply_schema(conn)  # ensure migration_audit exists

        if dry_run:
            rows = conn.execute(
                "SELECT run_id, status, pipeline_version, legacy FROM runs"
            ).fetchall()
            classified = [classify_run(r) for r in rows]
        else:
            classified = classify_all_runs(conn)
            conn.commit()

        counts = summarise(classified)
        payload = {
            "status": "ok",
            "total": len(classified),
            "counts": counts,
            "dry_run": dry_run,
        }
        out.write(payload)
        raise typer.Exit(0)
    except typer.Exit:
        raise
    except Exception as exc:
        out.write_error(code=1, message=f"migrate classify-runs failed: {exc}")
        raise typer.Exit(1)
    finally:
        conn.close()


@command(noun="migrate", verb="status", help="Show DB schema version and run counts by pipeline version")
def migrate_status(
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Print current migration state: schema version, run counts (v1/v2/legacy)."""
    from ai_dev_system.config import Config
    from ai_dev_system.db.connection import get_connection
    from ai_dev_system.db.migrator import apply_schema

    out = OutputRenderer(mode="json" if json_output else "human")
    config = Config.from_env()
    conn = get_connection(config.database_url)

    try:
        apply_schema(conn)

        applied = conn.execute(
            "SELECT name FROM schema_migrations ORDER BY name"
        ).fetchall()
        # Extract max vN number from migration filenames (e.g. "v5-phase1-v2.sql" → 5)
        import re as _re
        versions = [int(m.group(1)) for row in applied
                    if (m := _re.match(r"v(\d+)", row["name"]))]
        schema_version = max(versions) if versions else 0

        counts = {}
        for row in conn.execute(
            "SELECT pipeline_version, legacy, COUNT(*) AS n FROM runs GROUP BY pipeline_version, legacy"
        ).fetchall():
            key = f"v{row['pipeline_version'] or 1}_{'legacy' if row['legacy'] else 'new'}"
            counts[key] = row["n"]

        total = conn.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"]

        out.write({
            "status": "ok",
            "schema_version": schema_version,
            "total_runs": total,
            "run_counts": counts,
        })
        raise typer.Exit(0)
    except typer.Exit:
        raise
    except Exception as exc:
        out.write_error(code=1, message=f"migrate status failed: {exc}")
        raise typer.Exit(1)
    finally:
        conn.close()
