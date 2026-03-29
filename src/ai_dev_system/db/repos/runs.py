import psycopg


class RunRepo:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    def update_current_artifact(self, run_id: str, key: str, artifact_id: str) -> None:
        self.conn.execute("""
            UPDATE runs
            SET current_artifacts = jsonb_set(current_artifacts, %s, to_jsonb(%s::text)),
                last_activity_at = now()
            WHERE run_id = %s
        """, (f"{{{key}}}", artifact_id, run_id))
