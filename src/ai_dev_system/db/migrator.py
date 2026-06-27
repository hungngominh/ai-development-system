"""Schema migration runner for SQLite.

Applies docs/schema/control-layer-schema.sql + each migrations/vN-*.sql to a
target DB. Handles SQLite-specific concerns:

- ALTER TABLE ADD COLUMN cannot be guarded with IF NOT EXISTS on older SQLite,
  so we check column existence via PRAGMA table_info before issuing.
- Migrations are tracked in a `schema_migrations` table.
- Each .sql file is applied as a single transaction. On error → rollback that file.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


SCHEMA_DIR = Path(__file__).parent.parent.parent.parent / "docs" / "schema"
MIGRATIONS_DIR = SCHEMA_DIR / "migrations"


# Map of (table → list of columns) added by v5. Runner adds these if missing.
V5_ADD_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "runs": [
        ("pipeline_version", "INTEGER NOT NULL DEFAULT 1"),
        ("intake_state", "TEXT"),
        ("intake_brief_id", "TEXT"),
        ("gate1_session_state", "TEXT"),
        ("legacy", "INTEGER NOT NULL DEFAULT 0"),
        ("feature_overrides", "TEXT"),
    ],
}


@dataclass
class MigrationResult:
    name: str
    applied: bool
    skipped_reason: str | None = None
    error: str | None = None


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            name        TEXT PRIMARY KEY,
            applied_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            checksum    TEXT
        )
        """
    )


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(row["name"] == column for row in cur.fetchall())


def _apply_v5_add_columns(conn: sqlite3.Connection) -> None:
    """Add new columns for v5 if not present. Idempotent."""
    for table, cols in V5_ADD_COLUMNS.items():
        for col_name, col_type in cols:
            if not _column_exists(conn, table, col_name):
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}")

    # Backfill legacy flag for existing runs (one-time)
    if _column_exists(conn, "runs", "legacy"):
        conn.execute("UPDATE runs SET legacy = 1 WHERE pipeline_version = 1 AND legacy = 0")


# Full events.event_type allow-list as of v6 (mirror of control-layer-schema.sql).
# Used only to rebuild OLD DBs whose events CHECK predates 'RULE_LEARNED'.
_V6_EVENT_TYPES = [
    'RUN_CREATED', 'RUN_COMPLETED', 'RUN_ABORTED',
    'PHASE_STARTED', 'PHASE_COMPLETED',
    'GATE_ENTERED', 'GATE_APPROVED', 'GATE_REJECTED',
    'ARTIFACT_CREATED', 'ARTIFACT_APPROVED', 'ARTIFACT_SUPERSEDED', 'ARTIFACT_FAILED',
    'TASK_READY', 'TASK_STARTED', 'TASK_COMPLETED', 'TASK_FAILED', 'TASK_RETRYING', 'TASK_SKIPPED',
    'VERIFICATION_PASSED', 'VERIFICATION_FAILED',
    'VERIFICATION_STARTED', 'VERIFICATION_COMPLETED', 'REMEDIATION_CREATED',
    'ESCALATION_RAISED', 'HUMAN_DECISION_RECORDED',
    'RULES_APPLIED', 'BEADS_SYNC_WARNING',
    'INTAKE_STARTED', 'INTAKE_FIELD_ANSWERED', 'INTAKE_FIELD_SUGGESTED',
    'INTAKE_RESUMED', 'INTAKE_COMPLETED', 'INTAKE_ABORTED',
    'QUESTION_INVENTORY_GENERATED', 'QUESTION_DRAFT_GENERATED',
    'CRITIC_ITERATION_DONE', 'COVERAGE_REPORT_GENERATED',
    'MODERATOR_PARSE_FAIL',
    'SECTION_GENERATION_DEGRADED', 'SECTION_LENGTH_EXCEEDED',
    'BRIEF_EDIT_APPLIED',
    'G8_RETRIGGER_STARTED', 'G8_NOOP', 'G8_RETRIGGER_COMPLETED',
    'BRIEF_EDIT_THRESHOLD_EXCEEDED',
    'RULE_LEARNED',
]


def _apply_v6_events_check(conn: sqlite3.Connection) -> None:
    """Allow 'RULE_LEARNED' on events.event_type. Conditional + idempotent.

    SQLite cannot ALTER a CHECK in place. Fresh DBs already permit 'RULE_LEARNED'
    (control-layer-schema.sql), so this is a no-op there — we only rebuild an OLD
    events table whose CHECK predates the value. Guarding on the live CHECK keeps
    fresh DBs from ever rebuilding (so a future event type added to control-layer
    can't be silently dropped by this frozen list). Safe: nothing REFERENCES events.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='events'"
    ).fetchone()
    if row is None:
        return  # no events table yet (control-layer not applied) — nothing to do
    existing_sql = row["sql"] or ""
    if "RULE_LEARNED" in existing_sql:
        return  # already permitted — fresh DB, no rebuild

    allowed = ", ".join(f"'{t}'" for t in _V6_EVENT_TYPES)
    conn.executescript(
        f"""
        CREATE TABLE events_new (
            event_id        TEXT            PRIMARY KEY,
            run_id          TEXT            NOT NULL REFERENCES runs(run_id),
            task_run_id     TEXT            REFERENCES task_runs(task_run_id),
            correlation_id  TEXT,
            event_type      TEXT            NOT NULL,
            occurred_at     TEXT            NOT NULL DEFAULT CURRENT_TIMESTAMP,
            actor           TEXT            NOT NULL DEFAULT 'system',
            payload         TEXT            NOT NULL DEFAULT '{{}}'
                                            CHECK (json_valid(payload)),
            CHECK (event_type IN ({allowed}))
        );
        INSERT INTO events_new SELECT * FROM events;
        DROP TABLE events;
        ALTER TABLE events_new RENAME TO events;
        CREATE INDEX IF NOT EXISTS idx_events_run_occurred
            ON events(run_id, occurred_at DESC);
        CREATE INDEX IF NOT EXISTS idx_events_task_run_occurred
            ON events(task_run_id, occurred_at DESC) WHERE task_run_id IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_events_correlation
            ON events(correlation_id) WHERE correlation_id IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_events_type_occurred
            ON events(event_type, occurred_at DESC);
        """
    )


def _read_sql(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _strip_sql_comments(sql: str) -> str:
    """Remove -- line comments to detect 'is this file effectively empty'."""
    return "\n".join(
        line for line in sql.splitlines()
        if line.strip() and not line.lstrip().startswith("--")
    ).strip()


def apply_schema(conn: sqlite3.Connection, schema_dir: Path | None = None) -> list[MigrationResult]:
    """Apply control-layer-schema + all migrations in order. Idempotent.

    Returns list of MigrationResult for each file processed.
    """
    schema_dir = schema_dir or SCHEMA_DIR
    results: list[MigrationResult] = []

    _ensure_migrations_table(conn)

    # 1. Apply control-layer-schema.sql (idempotent: uses CREATE TABLE IF NOT EXISTS)
    control_path = schema_dir / "control-layer-schema.sql"
    if control_path.exists():
        sql = _read_sql(control_path)
        try:
            conn.executescript(sql)
            conn.commit()
            results.append(MigrationResult(name=control_path.name, applied=True))
        except sqlite3.Error as e:
            conn.rollback()
            results.append(MigrationResult(
                name=control_path.name, applied=False, error=str(e)
            ))
            return results  # control-layer must succeed before migrations
    else:
        results.append(MigrationResult(
            name="control-layer-schema.sql", applied=False,
            skipped_reason="file not found",
        ))
        return results

    # 2. Apply migrations in lexicographic order (v2, v3, v4, v5, ...)
    migrations_dir = schema_dir / "migrations"
    if not migrations_dir.exists():
        return results

    for migration_path in sorted(migrations_dir.glob("v*.sql")):
        # Skip rollback files
        if "rollback" in migration_path.stem:
            continue

        name = migration_path.name

        # Already applied?
        cur = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE name = ?", (name,)
        )
        if cur.fetchone() is not None:
            results.append(MigrationResult(
                name=name, applied=False, skipped_reason="already applied"
            ))
            continue

        sql = _read_sql(migration_path)
        stripped = _strip_sql_comments(sql)

        # v5 needs special handling for ADD COLUMN
        try:
            if name.startswith("v5-"):
                _apply_v5_add_columns(conn)
            if name.startswith("v6-"):
                _apply_v6_events_check(conn)

            if stripped:
                conn.executescript(sql)

            conn.execute(
                "INSERT INTO schema_migrations (name) VALUES (?)", (name,)
            )
            conn.commit()
            results.append(MigrationResult(name=name, applied=True))
        except sqlite3.Error as e:
            conn.rollback()
            results.append(MigrationResult(name=name, applied=False, error=str(e)))
            # Continue to next migration (don't abort entire chain on one failure)

    return results


def get_applied_migrations(conn: sqlite3.Connection) -> list[str]:
    """Return list of migration names already applied (in order)."""
    try:
        cur = conn.execute(
            "SELECT name FROM schema_migrations ORDER BY applied_at"
        )
        return [row["name"] for row in cur.fetchall()]
    except sqlite3.OperationalError:
        return []
