import uuid
import psycopg
import psycopg.types.json
from typing import Optional


class TaskRunRepo:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    def create_sync(self, run_id: str, task_type: str) -> dict:
        """Create a task_run for synchronous pipeline. Returns full dict."""
        task_run_id = str(uuid.uuid4())
        self.conn.execute("""
            INSERT INTO task_runs (
                task_run_id, run_id, task_id, attempt_number, status,
                agent_type, started_at, heartbeat_at,
                input_artifact_ids, resolved_dependencies, promoted_outputs
            ) VALUES (%s, %s, %s, 1, 'RUNNING', 'pipeline', now(), now(), '{}', '{}', '[]')
        """, (task_run_id, run_id, task_type))
        return {
            "task_run_id": task_run_id,
            "run_id": run_id,
            "task_id": task_type,
            "attempt_number": 1,
            "status": "RUNNING",
        }

    def pickup(self, run_id: str, worker_id: str, max_concurrent: int = 4) -> Optional[dict]:
        # Concurrency limit check — best-effort for v1. The count check and the FOR UPDATE
        # SKIP LOCKED are not in the same atomic step, so the limit can occasionally be
        # exceeded by 1 under high concurrency. Acceptable for Phase 1 (1-2 workers).
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

    def create_from_graph(self, run_id: str, task: dict, task_graph_artifact_id: str) -> str:
        """Create a PENDING task_run from a graph node. For execution engine."""
        task_run_id = str(uuid.uuid4())
        deps = task.get("deps", [])
        self.conn.execute("""
            INSERT INTO task_runs (
                task_run_id, run_id, task_id, task_graph_artifact_id,
                attempt_number, status, agent_type,
                input_artifact_ids, resolved_dependencies, promoted_outputs
            ) VALUES (%s, %s, %s, %s, 1, 'PENDING', %s, '{}', %s, '[]')
        """, (task_run_id, run_id, task["id"], task_graph_artifact_id,
              task.get("agent_type", "unknown"),
              deps))
        return task_run_id
