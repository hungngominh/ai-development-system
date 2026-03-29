import psycopg
from typing import Optional


class TaskRunRepo:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    def pickup(self, run_id: str, worker_id: str, max_concurrent: int = 4) -> Optional[dict]:
        running = self.conn.execute(
            "SELECT COUNT(*) as n FROM task_runs WHERE run_id = %s AND status = 'RUNNING'",
            (run_id,)
        ).fetchone()
        if running["n"] >= max_concurrent:
            return None

        task = self.conn.execute("""
            SELECT task_run_id, task_id, run_id, attempt_number,
                   input_artifact_ids, promoted_outputs
            FROM task_runs
            WHERE run_id = %s AND status = 'READY' AND worker_id IS NULL
            ORDER BY attempt_number ASC
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        """, (run_id,)).fetchone()
        if not task:
            return None

        self.conn.execute("""
            UPDATE task_runs
            SET status = 'RUNNING', worker_id = %s,
                locked_at = now(), heartbeat_at = now(), started_at = now()
            WHERE task_run_id = %s
        """, (worker_id, task["task_run_id"]))
        return dict(task) | {"status": "RUNNING"}

    def mark_success(self, task_run_id: str, output_ref: str, output_artifact_id: Optional[str]) -> int:
        result = self.conn.execute("""
            UPDATE task_runs
            SET status = 'SUCCESS', output_ref = %s, output_artifact_id = %s, completed_at = now()
            WHERE task_run_id = %s AND status = 'RUNNING' AND output_artifact_id IS NULL AND completed_at IS NULL
        """, (output_ref, output_artifact_id, task_run_id))
        return result.rowcount

    def mark_failed(self, task_run_id: str, error_type: str, error_detail: str) -> int:
        result = self.conn.execute("""
            UPDATE task_runs
            SET status = 'FAILED', error_type = %s::error_type, error_detail = %s, completed_at = now()
            WHERE task_run_id = %s AND status = 'RUNNING'
        """, (error_type, error_detail, task_run_id))
        return result.rowcount

    def update_heartbeat(self, task_run_id: str) -> None:
        self.conn.execute(
            "UPDATE task_runs SET heartbeat_at = now() WHERE task_run_id = %s",
            (task_run_id,)
        )

    def get_pending(self, run_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT task_run_id, task_id, resolved_dependencies FROM task_runs WHERE run_id = %s AND status = 'PENDING'",
            (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]
