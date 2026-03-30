import psycopg
from typing import Optional


class ArtifactRepo:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    def supersede_active(self, run_id: str, artifact_type: str) -> None:
        self.conn.execute("""
            UPDATE artifacts SET status = 'SUPERSEDED'
            WHERE run_id = %s AND artifact_type = %s::artifact_type AND status = 'ACTIVE'
        """, (run_id, artifact_type))

    def get(self, artifact_id: str):
        row = self.conn.execute(
            "SELECT * FROM artifacts WHERE artifact_id = %s", (artifact_id,)
        ).fetchone()
        return dict(row) if row else None

    def insert(
        self, run_id: str, artifact_type: str, version: int,
        created_by: str, input_artifact_ids: list,
        content_ref: str, content_checksum: str, content_size: int,
    ) -> str:
        row = self.conn.execute("""
            INSERT INTO artifacts (
                run_id, artifact_type, version, status, created_by,
                input_artifact_ids, content_ref, content_checksum, content_size
            ) VALUES (%s, %s::artifact_type, %s, 'ACTIVE', %s::created_by_type, %s, %s, %s, %s)
            RETURNING artifact_id
        """, (run_id, artifact_type, version, created_by,
              input_artifact_ids, content_ref, content_checksum, content_size)).fetchone()
        return str(row["artifact_id"])
