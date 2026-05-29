"""TaskRunRepo — SQLite-backed.

Differences from PG version:
- ? param style (no %s)
- FOR UPDATE SKIP LOCKED dropped (SQLite is single-writer; concurrent pickup serializes)
- jsonb_set → json_set
- Array literals '{}' → JSON arrays '[]'
- error_type cast removed (TEXT column with CHECK)
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from ai_dev_system.db.helpers import dump_json, load_array, new_uuid


class TaskRunRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create_sync(self, run_id: str, task_type: str) -> dict:
        """Create a task_run for synchronous pipeline. Returns full dict."""
        task_run_id = new_uuid()
        self.conn.execute(
            """
            INSERT INTO task_runs (
                task_run_id, run_id, task_id, task_graph_artifact_id,
                attempt_number, status,
                agent_type, started_at, heartbeat_at,
                input_artifact_ids, resolved_dependencies, promoted_outputs
            ) VALUES (?, ?, ?, NULL, 1, 'RUNNING', 'pipeline',
                      CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, '[]', '[]', '[]')
            """,
            (task_run_id, run_id, task_type),
        )
        return {
            "task_run_id": task_run_id,
            "run_id": run_id,
            "task_id": task_type,
            "attempt_number": 1,
            "status": "RUNNING",
        }

    def pickup(self, run_id: str, worker_id: str, max_concurrent: int = 4) -> Optional[dict]:
        """Pick up one READY task. Single-writer SQLite serializes naturally."""
        running = self.conn.execute(
            "SELECT COUNT(*) AS n FROM task_runs WHERE run_id = ? AND status = 'RUNNING'",
            (run_id,),
        ).fetchone()
        if running["n"] >= max_concurrent:
            return None

        task = self.conn.execute(
            """
            SELECT task_run_id, task_id, run_id, attempt_number,
                   input_artifact_ids, promoted_outputs
            FROM task_runs
            WHERE run_id = ? AND status = 'READY' AND worker_id IS NULL
            ORDER BY attempt_number ASC
            LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        if not task:
            return None

        self.conn.execute(
            """
            UPDATE task_runs
            SET status = 'RUNNING', worker_id = ?,
                locked_at = CURRENT_TIMESTAMP, heartbeat_at = CURRENT_TIMESTAMP,
                started_at = CURRENT_TIMESTAMP
            WHERE task_run_id = ?
            """,
            (worker_id, task["task_run_id"]),
        )
        return dict(task) | {"status": "RUNNING"}

    def mark_success(self, task_run_id: str, output_ref: str,
                     output_artifact_id: Optional[str]) -> int:
        cur = self.conn.execute(
            """
            UPDATE task_runs
            SET status = 'SUCCESS', output_ref = ?, output_artifact_id = ?,
                completed_at = CURRENT_TIMESTAMP
            WHERE task_run_id = ? AND status = 'RUNNING'
              AND output_artifact_id IS NULL AND completed_at IS NULL
            """,
            (output_ref, output_artifact_id, task_run_id),
        )
        return cur.rowcount

    def mark_failed(self, task_run_id: str, error_type: str, error_detail: str) -> int:
        cur = self.conn.execute(
            """
            UPDATE task_runs
            SET status = 'FAILED', error_type = ?, error_detail = ?,
                completed_at = CURRENT_TIMESTAMP
            WHERE task_run_id = ? AND status = 'RUNNING'
            """,
            (error_type, error_detail, task_run_id),
        )
        return cur.rowcount

    def mark_failed_final(self, task_run_id: str, error_type: str, error_detail: str) -> int:
        cur = self.conn.execute(
            """
            UPDATE task_runs
            SET status = 'FAILED_FINAL', error_type = ?, error_detail = ?,
                completed_at = CURRENT_TIMESTAMP
            WHERE task_run_id = ? AND status = 'RUNNING'
            """,
            (error_type, error_detail, task_run_id),
        )
        return cur.rowcount

    def mark_failed_retryable(self, task_run_id: str, error_type: str, error_detail: str) -> int:
        cur = self.conn.execute(
            """
            UPDATE task_runs
            SET status = 'FAILED_RETRYABLE', error_type = ?, error_detail = ?,
                completed_at = CURRENT_TIMESTAMP
            WHERE task_run_id = ? AND status = 'RUNNING'
            """,
            (error_type, error_detail, task_run_id),
        )
        return cur.rowcount

    def create_retry(
        self,
        run_id: str,
        source_task: dict,
        retry_delay_s: float = 0,
        reset_retry_count: bool = False,
    ) -> str:
        """Create a new attempt row linked to source_task. Returns new task_run_id.
        Caller manages the transaction.
        """
        new_id = new_uuid()
        new_retry_count = 0 if reset_retry_count else (source_task.get("retry_count", 0) + 1)
        retry_at = None
        if retry_delay_s and retry_delay_s > 0:
            retry_at = (datetime.now(timezone.utc) + timedelta(seconds=retry_delay_s)).isoformat()

        resolved_deps = source_task.get("resolved_dependencies") or []
        if isinstance(resolved_deps, str):
            # Possibly already-serialized JSON
            resolved_deps_json = resolved_deps
        else:
            resolved_deps_json = dump_json(list(resolved_deps))

        self.conn.execute(
            """
            INSERT INTO task_runs (
                task_run_id, run_id, task_id,
                task_graph_artifact_id,
                attempt_number, status,
                agent_type,
                resolved_dependencies,
                input_artifact_ids,
                promoted_outputs,
                retry_count,
                retry_at,
                agent_routing_key,
                context_snapshot,
                previous_attempt_id
            ) VALUES (?, ?, ?, ?, ?, 'PENDING', ?, ?, '[]', '[]', ?, ?, ?, ?, ?)
            """,
            (
                new_id, run_id, source_task["task_id"],
                source_task.get("task_graph_artifact_id"),
                (source_task.get("attempt_number", 1) + 1),
                source_task.get("agent_type"),
                resolved_deps_json,
                new_retry_count,
                retry_at,
                source_task.get("agent_routing_key"),
                source_task.get("context_snapshot"),
                source_task.get("task_run_id"),
            ),
        )
        return new_id

    def get_by_id(self, task_run_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM task_runs WHERE task_run_id = ?", (task_run_id,)
        ).fetchone()
        return dict(row) if row else None

    def update_heartbeat(self, task_run_id: str) -> None:
        self.conn.execute(
            "UPDATE task_runs SET heartbeat_at = CURRENT_TIMESTAMP WHERE task_run_id = ?",
            (task_run_id,),
        )

    def get_pending(self, run_id: str) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT task_run_id, task_id, resolved_dependencies
            FROM task_runs WHERE run_id = ? AND status = 'PENDING'
            """,
            (run_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def create_from_graph(self, run_id: str, task: dict, task_graph_artifact_id: str) -> str:
        """Create a PENDING task_run from a graph node."""
        task_run_id = new_uuid()
        deps = task.get("deps", [])
        self.conn.execute(
            """
            INSERT INTO task_runs (
                task_run_id, run_id, task_id, task_graph_artifact_id,
                attempt_number, status, agent_type,
                input_artifact_ids, resolved_dependencies, promoted_outputs
            ) VALUES (?, ?, ?, ?, 1, 'PENDING', ?, '[]', ?, '[]')
            """,
            (task_run_id, run_id, task["id"], task_graph_artifact_id,
             task.get("agent_type", "unknown"),
             dump_json(deps)),
        )
        return task_run_id
