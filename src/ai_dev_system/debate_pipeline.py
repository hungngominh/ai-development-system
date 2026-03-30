# src/ai_dev_system/debate_pipeline.py
import json
import os
from dataclasses import dataclass
from pathlib import Path

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
from ai_dev_system.finalize_spec import finalize_spec
from ai_dev_system.task_graph.generator import generate_task_graph
from ai_dev_system.gate.gate2 import run_gate_2
from ai_dev_system.beads.sync import beads_sync
from ai_dev_system.engine.runner import run_execution, ExecutionResult
from ai_dev_system.pipeline import PipelineAborted


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


@dataclass
class PhaseBResult:
    run_id: str
    graph_artifact_id: str
    execution_result: "ExecutionResult | None" = None


def run_phase_b_pipeline(
    run_id: str,
    config: Config,
    conn_factory,
    gate2_io,
    llm_client,
    agent=None,
) -> PhaseBResult:
    """Phase B: approved_answers → finalize_spec → task_graph → Gate 2 → beads_sync → execution.

    Accepts conn_factory (not a live conn) because Phase B is invoked in a new process
    after the Gate 1 pause. In tests, pass `lambda: db_conn`.
    """
    conn = conn_factory()
    task_run_repo = TaskRunRepo(conn)
    event_repo = EventRepo(conn)

    # Guard: must be called after Gate 1
    row = conn.execute(
        "SELECT status, current_artifacts FROM runs WHERE run_id = %s", (run_id,)
    ).fetchone()
    assert row["status"] == "RUNNING_PHASE_1D", (
        f"Expected RUNNING_PHASE_1D, got {row['status']}"
    )

    # Load approved_answers from artifact
    aa_artifact_id = row["current_artifacts"]["approved_answers_id"]
    aa_artifact = conn.execute(
        "SELECT content_ref FROM artifacts WHERE artifact_id = %s", (aa_artifact_id,)
    ).fetchone()
    aa_path = os.path.join(aa_artifact["content_ref"], "approved_answers.json")
    with open(aa_path, encoding="utf-8") as f:
        approved_answers = json.load(f)

    # Step 1: finalize_spec
    task_run = task_run_repo.create_sync(run_id, task_type="finalize_spec")
    task_run["input_artifact_ids"] = [aa_artifact_id]
    event_repo.insert(run_id, "TASK_STARTED", "debate_pipeline", task_run["task_run_id"])

    temp_path = build_temp_path(
        config.storage_root, run_id, task_run["task_id"], task_run["attempt_number"]
    )
    bundle = finalize_spec(approved_answers, run_id, llm_client, output_dir=Path(temp_path))

    promoted = PromotedOutput(name="spec_bundle", artifact_type="SPEC_BUNDLE",
                              description="5-file spec bundle from Gate 1 answers")
    spec_artifact_id = promote_output(conn, config, task_run, promoted, temp_path)

    # Refresh bundle content from final artifact location
    spec_row = conn.execute(
        "SELECT content_ref FROM artifacts WHERE artifact_id = %s", (spec_artifact_id,)
    ).fetchone()
    bundle_root = Path(spec_row["content_ref"])
    spec_content = {
        name: (bundle_root / name).read_text(encoding="utf-8")
        for name in bundle.files
        if (bundle_root / name).exists()
    }

    # Step 2: generate_task_graph
    task_run_tg = task_run_repo.create_sync(run_id, task_type="generate_task_graph")
    task_run_tg["input_artifact_ids"] = [spec_artifact_id]
    event_repo.insert(run_id, "TASK_STARTED", "debate_pipeline", task_run_tg["task_run_id"])

    envelope = generate_task_graph(spec_content, approved_answers, spec_artifact_id, llm_client)
    temp_tg = _write_json_to_temp_debate(config, task_run_tg, envelope)
    promote_output(conn, config, task_run_tg,
                   PromotedOutput("task_graph", "TASK_GRAPH_GENERATED", "Generated task graph"),
                   temp_tg)

    # Step 3: Gate 2
    task_run_g2 = task_run_repo.create_sync(run_id, task_type="task_graph_gate2")
    task_run_g2["input_artifact_ids"] = []
    event_repo.insert(run_id, "TASK_STARTED", "debate_pipeline", task_run_g2["task_run_id"])

    gate2_result = run_gate_2(envelope, gate2_io)
    if gate2_result.status == "rejected":
        task_run_repo.mark_failed(task_run_g2["task_run_id"], "EXECUTION_ERROR", "user_rejected")
        raise PipelineAborted("User rejected task graph at Gate 2")

    temp_approved = _write_json_to_temp_debate(config, task_run_g2, gate2_result.graph)
    graph_artifact_id = promote_output(
        conn, config, task_run_g2,
        PromotedOutput("task_graph_approved", "TASK_GRAPH_APPROVED", "Human-approved task graph"),
        temp_approved,
    )

    # Step 4: Beads sync
    beads_sync(run_id, gate2_result.graph, conn)

    # Step 5: Execution (only if agent provided)
    execution_result = None
    if agent is not None:
        execution_result = run_execution(run_id, graph_artifact_id, config, agent)

    return PhaseBResult(
        run_id=run_id,
        graph_artifact_id=graph_artifact_id,
        execution_result=execution_result,
    )


def _write_json_to_temp_debate(config: Config, task_run: dict, data: dict) -> str:
    """Write dict as JSON to temp path. Returns temp_path directory."""
    temp_path = build_temp_path(
        config.storage_root, run_id=task_run["run_id"],
        task_id=task_run["task_id"],
        attempt_number=task_run["attempt_number"],
    )
    os.makedirs(temp_path, exist_ok=True)
    with open(os.path.join(temp_path, f"{task_run['task_id']}.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return temp_path


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
