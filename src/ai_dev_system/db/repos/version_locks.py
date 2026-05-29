"""VersionLockRepo — SQLite-backed.

Atomic version increment per (run_id, artifact_type). SQLite single-writer makes
this naturally atomic when the whole sequence runs in one transaction.
"""
from __future__ import annotations

import sqlite3


class VersionLockRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def lock_and_increment(self, run_id: str, artifact_type: str) -> int:
        # Ensure row exists (idempotent)
        self.conn.execute(
            """
            INSERT INTO artifact_version_locks (run_id, artifact_type)
            VALUES (?, ?)
            ON CONFLICT(run_id, artifact_type) DO NOTHING
            """,
            (run_id, artifact_type),
        )
        row = self.conn.execute(
            """
            SELECT current_version FROM artifact_version_locks
            WHERE run_id = ? AND artifact_type = ?
            """,
            (run_id, artifact_type),
        ).fetchone()
        next_version = row["current_version"] + 1
        self.conn.execute(
            """
            UPDATE artifact_version_locks SET current_version = ?
            WHERE run_id = ? AND artifact_type = ?
            """,
            (next_version, run_id, artifact_type),
        )
        return next_version
