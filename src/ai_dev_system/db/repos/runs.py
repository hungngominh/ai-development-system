"""RunRepo — SQLite-backed.

Parameter style: ? (SQLite). JSON columns stored as TEXT with json_valid CHECK.
Timestamps use CURRENT_TIMESTAMP (SQLite) for server-side values.
"""
from __future__ import annotations

import sqlite3

from ai_dev_system.db.helpers import dump_json, new_uuid


class RunRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(self, project_id: str, pipeline_type: str) -> str:
        """Create a new run. Returns run_id."""
        run_id = new_uuid()
        initial_artifacts = {
            "initial_brief_id": None, "debate_report_id": None,
            "decision_log_id": None, "approved_answers_id": None,
            "approved_brief_id": None, "spec_bundle_id": None,
            "task_graph_gen_id": None, "task_graph_approved_id": None,
        }
        self.conn.execute(
            """
            INSERT INTO runs (run_id, project_id, status, title, current_artifacts, metadata)
            VALUES (?, ?, 'RUNNING_PHASE_1A', ?, ?, '{}')
            """,
            (run_id, project_id, f"Pipeline: {pipeline_type}",
             dump_json(initial_artifacts)),
        )
        return run_id

    def update_current_artifact(self, run_id: str, key: str, artifact_id: str) -> None:
        """Set one key inside the current_artifacts JSON object.

        SQLite uses json_set(target, '$.key', value). The '$.{key}' path is built safely.
        """
        json_path = f"$.{key}"
        self.conn.execute(
            """
            UPDATE runs
            SET current_artifacts = json_set(current_artifacts, ?, ?),
                last_activity_at = CURRENT_TIMESTAMP
            WHERE run_id = ?
            """,
            (json_path, artifact_id, run_id),
        )

    def update_status(self, run_id: str, status: str) -> None:
        self.conn.execute(
            """
            UPDATE runs SET status = ?, last_activity_at = CURRENT_TIMESTAMP
            WHERE run_id = ?
            """,
            (status, run_id),
        )
