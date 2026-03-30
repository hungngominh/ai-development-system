# src/ai_dev_system/db/repos/escalations.py
import psycopg
import psycopg.types.json
from typing import Optional


class EscalationRepo:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    def upsert_open(
        self,
        run_id: str,
        task_run_id: str,
        reason: str,
        options: list[str],
    ) -> str:
        """Insert an OPEN escalation or return existing one's ID (idempotent)."""
        self.conn.execute("""
            INSERT INTO escalations (run_id, task_run_id, reason, options)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT ON CONSTRAINT uq_escalation_open_dedup
            DO NOTHING
        """, (run_id, task_run_id, reason, psycopg.types.json.Jsonb(options)))

        row = self.conn.execute("""
            SELECT escalation_id FROM escalations
            WHERE run_id = %s AND task_run_id = %s AND reason = %s AND status = 'OPEN'
        """, (run_id, task_run_id, reason)).fetchone()
        return row["escalation_id"] if row else None

    def get_open(self, run_id: str) -> list[dict]:
        rows = self.conn.execute("""
            SELECT * FROM escalations
            WHERE run_id = %s AND status = 'OPEN'
            ORDER BY created_at ASC
        """, (run_id,)).fetchall()
        return [dict(r) for r in rows]

    def get_and_lock(self, escalation_id: str) -> Optional[dict]:
        """SELECT FOR UPDATE — must be inside a transaction."""
        row = self.conn.execute("""
            SELECT * FROM escalations
            WHERE escalation_id = %s
            FOR UPDATE
        """, (escalation_id,)).fetchone()
        return dict(row) if row else None

    def mark_resolved(self, escalation_id: str, resolution: str) -> int:
        result = self.conn.execute("""
            UPDATE escalations
            SET status = 'RESOLVED', resolution = %s, resolved_at = now()
            WHERE escalation_id = %s AND status = 'OPEN'
        """, (resolution, escalation_id))
        return result.rowcount
