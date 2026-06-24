"""Intake DB layer: checkpoint state to runs.intake_state.

Pattern: write `IntakeState.to_json()` into runs.intake_state TEXT column after
every state-changing event (answer / skip / confirm). Read on resume.

`intake_brief_id` column is set once on promotion.
"""
from __future__ import annotations

import sqlite3
from typing import Optional

from ai_dev_system.intake.engine import IntakeState


class IntakeRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def save_state(self, state: IntakeState) -> None:
        """Idempotent: overwrite the entire intake_state JSON for this run."""
        self.conn.execute(
            """
            UPDATE runs
            SET intake_state = ?, last_activity_at = CURRENT_TIMESTAMP
            WHERE run_id = ?
            """,
            (state.to_json(), state.run_id),
        )

    def load_state(self, run_id: str) -> Optional[IntakeState]:
        row = self.conn.execute(
            "SELECT intake_state FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None or row["intake_state"] is None:
            return None
        return IntakeState.from_json(row["intake_state"])

    def clear_state(self, run_id: str) -> None:
        """Called after INTAKE_BRIEF is promoted — keeps DB tidy."""
        self.conn.execute(
            "UPDATE runs SET intake_state = NULL WHERE run_id = ?", (run_id,)
        )

    def set_brief_id(self, run_id: str, artifact_id: str) -> None:
        self.conn.execute(
            "UPDATE runs SET intake_brief_id = ? WHERE run_id = ?",
            (artifact_id, run_id),
        )

    def get_run_status(self, run_id: str) -> Optional[str]:
        row = self.conn.execute(
            "SELECT status FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        return row["status"] if row else None
