"""Phase 1 v2 run classifier (S8).

Pure classification + DB-backed audit writer used by:
  - the one-shot `ai-dev migrate classify-runs` CLI to backfill `migration_audit`
    rows for runs that existed before v5 was applied,
  - dispatchers that need to know whether a run should follow v1 or v2 code
    paths (`is_legacy_run`).

Classification rules come from `docs/superpowers/specs/2026-05-23-phase1-migration-plan.md`:

  v1_continue → pre-v5 run, or pipeline_version=1 in a terminal state.
                Finishes on legacy code path; read-only after terminal.
  v2_new      → pipeline_version=2, NOT in COLLECTING_INTAKE.
  v2_resume   → pipeline_version=2 AND status=COLLECTING_INTAKE.
  abort       → unexpected combination (logged for manual review).

This module ONLY observes — it does not mutate runs. The `legacy=1` backfill
already happens inside `db/migrator._apply_v5_add_columns()` on schema apply.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Iterable, Literal, Optional

V5_MIGRATION_VERSION = 5

Classification = Literal["v1_continue", "v2_new", "v2_resume", "abort"]

TERMINAL_STATUSES: frozenset[str] = frozenset({"COMPLETED", "ABORTED", "FAILED"})


@dataclass(frozen=True)
class ClassifiedRun:
    run_id: str
    classification: Classification
    notes: str


def classify_run(row: dict | sqlite3.Row) -> ClassifiedRun:
    """Classify a single run row. Pure function; safe to call with no DB side effects.

    The row must expose at least: run_id, status, pipeline_version, legacy.
    Missing pipeline_version defaults to 1 (treated as legacy).
    """
    run_id = row["run_id"]
    status = row["status"]
    pv = _get(row, "pipeline_version", default=1) or 1
    legacy = bool(_get(row, "legacy", default=0))

    if pv == 1:
        if status in TERMINAL_STATUSES:
            return ClassifiedRun(run_id, "v1_continue",
                                 notes=f"pipeline_version=1, terminal status={status}")
        # In-flight v1 run — stays on legacy path until terminal.
        return ClassifiedRun(run_id, "v1_continue",
                             notes=f"pipeline_version=1, in-flight status={status}, legacy={int(legacy)}")

    if pv == 2:
        if status == "COLLECTING_INTAKE":
            return ClassifiedRun(run_id, "v2_resume",
                                 notes="pipeline_version=2 + intake paused")
        return ClassifiedRun(run_id, "v2_new",
                             notes=f"pipeline_version=2, status={status}")

    return ClassifiedRun(run_id, "abort",
                         notes=f"unexpected pipeline_version={pv}, status={status}")


def _get(row, key, default=None):
    """Tolerate both sqlite3.Row (no .get) and plain dicts."""
    try:
        val = row[key]
        return val if val is not None else default
    except (KeyError, IndexError):
        return default


def write_audit(conn: sqlite3.Connection, classified: ClassifiedRun) -> None:
    """Insert a row into migration_audit. Caller must commit.

    Idempotent: skip insert if a row for this (run_id, migration_version) already
    exists. This makes the CLI backfill safe to re-run.
    """
    existing = conn.execute(
        "SELECT 1 FROM migration_audit WHERE run_id = ? AND migration_version = ?",
        (classified.run_id, V5_MIGRATION_VERSION),
    ).fetchone()
    if existing is not None:
        return
    conn.execute(
        """
        INSERT INTO migration_audit (run_id, migration_version, classification, notes)
        VALUES (?, ?, ?, ?)
        """,
        (classified.run_id, V5_MIGRATION_VERSION,
         classified.classification, classified.notes),
    )


def classify_all_runs(conn: sqlite3.Connection) -> list[ClassifiedRun]:
    """Scan every row in `runs`, classify, and append audit entries. Idempotent.

    Returns the full classification list (whether or not audit was already
    recorded) so callers can summarise. Commit is caller's job.
    """
    rows = conn.execute(
        "SELECT run_id, status, pipeline_version, legacy FROM runs"
    ).fetchall()
    out: list[ClassifiedRun] = []
    for r in rows:
        c = classify_run(r)
        write_audit(conn, c)
        out.append(c)
    return out


def is_legacy_run(conn: sqlite3.Connection, run_id: str) -> bool:
    """Cheap helper for dispatchers. True ⇔ run.legacy=1 OR pipeline_version=1.

    Defensive: a missing row returns False (caller will likely hit its own error).
    """
    row = conn.execute(
        "SELECT legacy, pipeline_version FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if row is None:
        return False
    if row["legacy"]:
        return True
    pv = row["pipeline_version"]
    return pv is None or pv == 1


def summarise(classified: Iterable[ClassifiedRun]) -> dict[str, int]:
    """Count classifications. Useful for the CLI to print a summary."""
    counts: dict[str, int] = {"v1_continue": 0, "v2_new": 0, "v2_resume": 0, "abort": 0}
    for c in classified:
        counts[c.classification] = counts.get(c.classification, 0) + 1
    return counts
