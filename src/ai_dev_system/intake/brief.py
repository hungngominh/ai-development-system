"""Promote a confirmed IntakeState into an INTAKE_BRIEF artifact on disk + DB.

Flow:
1. Write brief.json into a temp dir
2. Call promote_output (storage/promote.py) to atomically move + register
3. Update runs.intake_brief_id + status = READY_FOR_DEBATE
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

from ai_dev_system.agents.base import PromotedOutput
from ai_dev_system.config import Config
from ai_dev_system.db.repos.events import EventRepo
from ai_dev_system.db.repos.runs import RunRepo
from ai_dev_system.db.repos.task_runs import TaskRunRepo
from ai_dev_system.intake.engine import IntakeState, to_brief_v2
from ai_dev_system.intake.repo import IntakeRepo
from ai_dev_system.intake.template import Template
from ai_dev_system.storage.paths import build_temp_path
from ai_dev_system.storage.promote import promote_output


def _compute_source_hash(state: IntakeState) -> str:
    """SHA-256 over all user-source answers — stable fingerprint of human input.

    Excludes audit/timestamps so the hash is content-only.
    """
    parts = []
    for fid in sorted(state.answers.keys()):
        a = state.answers[fid]
        if a.source != "user":
            continue
        parts.append(f"{fid}:{json.dumps(a.value, ensure_ascii=False, sort_keys=True)}")
    blob = "\n".join(parts).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def promote_intake_brief(
    conn: sqlite3.Connection,
    config: Config,
    state: IntakeState,
    template: Template,
) -> str:
    """Atomic promotion: brief.json → INTAKE_BRIEF artifact.

    Pre-condition: state.stage == 'DONE'.
    Returns artifact_id.
    """
    if state.stage != "DONE":
        raise ValueError(
            f"Cannot promote intake brief — state.stage = {state.stage}, expected DONE"
        )

    task_run_repo = TaskRunRepo(conn)
    event_repo = EventRepo(conn)
    run_repo = RunRepo(conn)
    intake_repo = IntakeRepo(conn)

    # Synthetic task_run for the promotion (so artifact has a lineage entry)
    task_run = task_run_repo.create_sync(state.run_id, task_type="intake_wizard")
    event_repo.insert(state.run_id, "INTAKE_COMPLETED", "intake_wizard",
                      task_run["task_run_id"])

    temp_dir = build_temp_path(
        config.storage_root, state.run_id,
        task_run["task_id"], task_run["attempt_number"],
    )
    os.makedirs(temp_dir, exist_ok=True)

    brief = to_brief_v2(state, template, source_hash=_compute_source_hash(state))
    (Path(temp_dir) / "brief.json").write_text(
        json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    artifact_id = promote_output(
        conn, config, task_run,
        PromotedOutput(
            name="intake_brief",
            artifact_type="INTAKE_BRIEF",
            description="Structured project brief v2 from intake wizard",
        ),
        temp_dir,
    )

    intake_repo.set_brief_id(state.run_id, artifact_id)
    intake_repo.clear_state(state.run_id)
    run_repo.update_status(state.run_id, "READY_FOR_DEBATE")

    return artifact_id
