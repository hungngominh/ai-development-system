import psycopg


class VersionLockRepo:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    def lock_and_increment(self, run_id: str, artifact_type: str) -> int:
        self.conn.execute("""
            INSERT INTO artifact_version_locks (run_id, artifact_type)
            VALUES (%s, %s) ON CONFLICT DO NOTHING
        """, (run_id, artifact_type))
        row = self.conn.execute("""
            SELECT current_version FROM artifact_version_locks
            WHERE run_id = %s AND artifact_type = %s
            FOR UPDATE
        """, (run_id, artifact_type)).fetchone()
        next_version = row["current_version"] + 1
        self.conn.execute("""
            UPDATE artifact_version_locks SET current_version = %s
            WHERE run_id = %s AND artifact_type = %s
        """, (next_version, run_id, artifact_type))
        return next_version
