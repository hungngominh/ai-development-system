"""ArtifactRepo — SQLite-backed."""
from __future__ import annotations

import sqlite3
from typing import Optional

from ai_dev_system.db.helpers import dump_json, new_uuid


class ArtifactRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def supersede_active(self, run_id: str, artifact_type: str) -> None:
        self.conn.execute(
            """
            UPDATE artifacts SET status = 'SUPERSEDED'
            WHERE run_id = ? AND artifact_type = ? AND status = 'ACTIVE'
            """,
            (run_id, artifact_type),
        )

    def get(self, artifact_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)
        ).fetchone()
        return dict(row) if row else None

    def insert(
        self,
        run_id: str,
        artifact_type: str,
        version: int,
        created_by: str,
        input_artifact_ids: list,
        content_ref: str,
        content_checksum: str,
        content_size: int,
    ) -> str:
        artifact_id = new_uuid()
        self.conn.execute(
            """
            INSERT INTO artifacts (
                artifact_id, run_id, artifact_type, version, status, created_by,
                input_artifact_ids, content_ref, content_checksum, content_size
            ) VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?, ?, ?, ?)
            """,
            (artifact_id, run_id, artifact_type, version, created_by,
             dump_json(list(input_artifact_ids or [])),
             content_ref, content_checksum, content_size),
        )
        return artifact_id
