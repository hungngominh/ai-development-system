import psycopg
from typing import Optional


class EventRepo:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    def insert(
        self, run_id: str, event_type: str, actor: str,
        task_run_id: Optional[str] = None,
        payload: Optional[dict] = None,
    ) -> None:
        self.conn.execute("""
            INSERT INTO events (run_id, task_run_id, event_type, actor, payload)
            VALUES (%s, %s, %s::event_type, %s, %s)
        """, (run_id, task_run_id, event_type, actor, psycopg.types.json.Jsonb(payload or {})))
