# src/ai_dev_system/debate_pipeline.py
import json
import os
from dataclasses import dataclass

from ai_dev_system.config import Config
from ai_dev_system.normalize import normalize_idea
from ai_dev_system.debate.questions import generate_questions
from ai_dev_system.debate.engine import run_debate
from ai_dev_system.debate.report import DebateReport
from ai_dev_system.agents.base import PromotedOutput
from ai_dev_system.db.repos.runs import RunRepo
from ai_dev_system.db.repos.task_runs import TaskRunRepo
from ai_dev_system.db.repos.events import EventRepo
from ai_dev_system.storage.paths import build_temp_path
from ai_dev_system.storage.promote import promote_output


@dataclass
class DebatePipelineResult:
    run_id: str
    debate_report: DebateReport
    artifact_id: str


def run_debate_pipeline(
    raw_idea: str,
    config: Config,
    conn,
    project_id: str,
    llm_client,
) -> DebatePipelineResult:
    """Phase A: normalize → question gen → debate → DEBATE_REPORT artifact → PAUSED_AT_GATE_1."""
    run_repo = RunRepo(conn)
    task_run_repo = TaskRunRepo(conn)
    event_repo = EventRepo(conn)

    # Run created with status=RUNNING_PHASE_1A
    run_id = run_repo.create(project_id=project_id, pipeline_type="debate_pipeline")

    # Step 1: Normalize
    brief = normalize_idea(raw_idea)

    # Step 2: Generate questions
    task_run = task_run_repo.create_sync(run_id, task_type="generate_questions")
    task_run["input_artifact_ids"] = []
    event_repo.insert(run_id, "TASK_STARTED", "debate_pipeline", task_run["task_run_id"])
    questions = generate_questions(brief, llm_client)

    # Step 3: Run debate
    run_repo.update_status(run_id, "RUNNING_PHASE_1B")

    task_run = task_run_repo.create_sync(run_id, task_type="run_debate")
    task_run["input_artifact_ids"] = []
    event_repo.insert(run_id, "TASK_STARTED", "debate_pipeline", task_run["task_run_id"])
    debate_report = run_debate(questions, llm_client, run_id=run_id, brief=brief)

    # Step 4: Promote DEBATE_REPORT artifact
    temp_path = build_temp_path(
        config.storage_root, run_id,
        task_run["task_id"], task_run["attempt_number"]
    )
    os.makedirs(temp_path, exist_ok=True)
    report_dict = _debate_report_to_dict(debate_report)
    with open(os.path.join(temp_path, "debate_report.json"), "w", encoding="utf-8") as f:
        json.dump(report_dict, f, indent=2, ensure_ascii=False)

    promoted = PromotedOutput(
        name="debate_report",
        artifact_type="DEBATE_REPORT",
        description="AI debate report for Gate 1 review",
    )
    artifact_id = promote_output(conn, config, task_run, promoted, temp_path)

    # Step 5: Pause for Gate 1
    run_repo.update_status(run_id, "PAUSED_AT_GATE_1")

    return DebatePipelineResult(
        run_id=run_id,
        debate_report=debate_report,
        artifact_id=artifact_id,
    )


def _debate_report_to_dict(report: DebateReport) -> dict:
    """Serialize DebateReport to JSON-safe dict."""
    def round_to_dict(r):
        return {
            "round_number": r.round_number,
            "agent_a_position": r.agent_a_position,
            "agent_b_position": r.agent_b_position,
            "moderator_summary": r.moderator_summary,
            "resolution_status": r.resolution_status,
            "confidence": r.confidence,
            "caveat": r.caveat,
        }

    def qdr_to_dict(qdr):
        return {
            "question": {
                "id": qdr.question.id,
                "text": qdr.question.text,
                "classification": qdr.question.classification,
                "domain": qdr.question.domain,
                "agent_a": qdr.question.agent_a,
                "agent_b": qdr.question.agent_b,
            },
            "rounds": [round_to_dict(r) for r in qdr.rounds],
            "final": round_to_dict(qdr.final),
        }

    return {
        "run_id": report.run_id,
        "brief": report.brief,
        "results": [qdr_to_dict(r) for r in report.results],
        "generated_at": report.generated_at,
    }
