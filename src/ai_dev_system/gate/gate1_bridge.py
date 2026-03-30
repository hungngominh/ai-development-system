# src/ai_dev_system/gate/gate1_bridge.py
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from ai_dev_system.config import Config
from ai_dev_system.agents.base import PromotedOutput
from ai_dev_system.db.repos.runs import RunRepo
from ai_dev_system.db.repos.task_runs import TaskRunRepo
from ai_dev_system.db.repos.events import EventRepo
from ai_dev_system.storage.paths import build_temp_path
from ai_dev_system.storage.promote import promote_output


@dataclass
class Decision:
    question_id: str
    question_text: str
    classification: str
    resolution_type: Literal["CONSENSUS", "FORCED_HUMAN", "OVERRIDE"]
    answer: str
    options_considered: list[str] = field(default_factory=list)
    rationale: str = ""


def finalize_gate1(
    run_id: str,
    decisions: list[Decision],
    storage_root: str,
    conn,
) -> tuple[str, str]:
    """Write APPROVED_ANSWERS + DECISION_LOG artifacts. Returns (aa_id, dl_id).
    Transitions run status to RUNNING_PHASE_1D.
    """
    config = Config(storage_root=storage_root, database_url="unused")
    task_run_repo = TaskRunRepo(conn)
    event_repo = EventRepo(conn)
    run_repo = RunRepo(conn)

    # Read debate_report_id for artifact lineage
    run_row = conn.execute(
        "SELECT current_artifacts FROM runs WHERE run_id = %s", (run_id,)
    ).fetchone()
    debate_report_id = run_row["current_artifacts"].get("debate_report_id")

    # --- Artifact 1: APPROVED_ANSWERS ---
    task_run_aa = task_run_repo.create_sync(run_id, task_type="gate1_approved_answers")
    task_run_aa["input_artifact_ids"] = [debate_report_id] if debate_report_id else []
    event_repo.insert(run_id, "TASK_STARTED", "gate1_skill", task_run_aa["task_run_id"])

    temp_aa = build_temp_path(
        config.storage_root, run_id,
        task_run_aa["task_id"], task_run_aa["attempt_number"]
    )
    os.makedirs(temp_aa, exist_ok=True)

    approved_answers = {d.question_id: d.answer for d in decisions}
    with open(os.path.join(temp_aa, "approved_answers.json"), "w", encoding="utf-8") as f:
        json.dump(approved_answers, f, indent=2, ensure_ascii=False)

    aa_id = promote_output(
        conn, config, task_run_aa,
        PromotedOutput("approved_answers", "APPROVED_ANSWERS", "Gate 1 approved answers"),
        temp_aa,
    )

    # --- Artifact 2: DECISION_LOG ---
    task_run_dl = task_run_repo.create_sync(run_id, task_type="gate1_decision_log")
    task_run_dl["input_artifact_ids"] = [debate_report_id] if debate_report_id else []
    event_repo.insert(run_id, "TASK_STARTED", "gate1_skill", task_run_dl["task_run_id"])

    temp_dl = build_temp_path(
        config.storage_root, run_id,
        task_run_dl["task_id"], task_run_dl["attempt_number"]
    )
    os.makedirs(temp_dl, exist_ok=True)

    decision_log = {
        "run_id": run_id,
        "confirmed_at": datetime.now(timezone.utc).isoformat(),
        "decisions": [
            {
                "question_id": d.question_id,
                "question_text": d.question_text,
                "classification": d.classification,
                "resolution_type": d.resolution_type,
                "answer": d.answer,
                "options_considered": d.options_considered,
                "rationale": d.rationale,
            }
            for d in decisions
        ],
    }
    with open(os.path.join(temp_dl, "decision_log.json"), "w", encoding="utf-8") as f:
        json.dump(decision_log, f, indent=2, ensure_ascii=False)

    dl_id = promote_output(
        conn, config, task_run_dl,
        PromotedOutput("decision_log", "DECISION_LOG", "Gate 1 decision log"),
        temp_dl,
    )

    # Transition: Gate 1 approved → Phase B ready
    run_repo.update_status(run_id, "RUNNING_PHASE_1D")

    return aa_id, dl_id
