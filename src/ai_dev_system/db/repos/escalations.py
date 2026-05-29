"""EscalationRepo — SQLite-backed.

PG `ON CONFLICT ON CONSTRAINT` → SQLite `ON CONFLICT(cols) DO NOTHING` (same UNIQUE columns).
PG `FOR UPDATE` removed (SQLite single-writer).
"""
from __future__ import annotations

import sqlite3
from typing import Optional

from ai_dev_system.db.helpers import dump_json, new_uuid


class EscalationRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def upsert_open(
        self,
        run_id: str,
        task_run_id: str,
        reason: str,
        options: list[str],
    ) -> Optional[str]:
        """Insert an OPEN escalation or return existing one's ID (idempotent).

        Uses ON CONFLICT (matching UNIQUE(run_id, task_run_id, reason, status))
        — the column tuple that backs uq_escalation_open_dedup in PG, declared
        inline on the table in SQLite.
        """
        self.conn.execute(
            """
            INSERT INTO escalations (escalation_id, run_id, task_run_id, reason, options, status)
            VALUES (?, ?, ?, ?, ?, 'OPEN')
            ON CONFLICT(run_id, task_run_id, reason, status) DO NOTHING
            """,
            (new_uuid(), run_id, task_run_id, reason, dump_json(options)),
        )

        row = self.conn.execute(
            """
            SELECT escalation_id FROM escalations
            WHERE run_id = ? AND task_run_id = ? AND reason = ? AND status = 'OPEN'
            """,
            (run_id, task_run_id, reason),
        ).fetchone()
        return row["escalation_id"] if row else None

    def get_open(self, run_id: str) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT * FROM escalations
            WHERE run_id = ? AND status = 'OPEN'
            ORDER BY created_at ASC
            """,
            (run_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_and_lock(self, escalation_id: str) -> Optional[dict]:
        """SQLite has no FOR UPDATE — just SELECT (single-writer)."""
        row = self.conn.execute(
            "SELECT * FROM escalations WHERE escalation_id = ?",
            (escalation_id,),
        ).fetchone()
        return dict(row) if row else None

    def mark_resolved(self, escalation_id: str, resolution: str) -> int:
        cur = self.conn.execute(
            """
            UPDATE escalations
            SET status = 'RESOLVED', resolution = ?, resolved_at = CURRENT_TIMESTAMP
            WHERE escalation_id = ? AND status = 'OPEN'
            """,
            (resolution, escalation_id),
        )
        return cur.rowcount
