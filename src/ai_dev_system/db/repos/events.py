"""EventRepo — SQLite-backed. Append-only audit trail."""
from __future__ import annotations

import sqlite3
from typing import Optional

from ai_dev_system.db.helpers import dump_json, new_uuid


class EventRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def insert(
        self,
        run_id: str,
        event_type: str,
        actor: str,
        task_run_id: Optional[str] = None,
        payload: Optional[dict] = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO events (event_id, run_id, task_run_id, event_type, actor, payload)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (new_uuid(), run_id, task_run_id, event_type, actor,
             dump_json(payload or {})),
        )
