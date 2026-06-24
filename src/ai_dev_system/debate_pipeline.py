# src/ai_dev_system/debate_pipeline.py
import json
import os
import warnings
from dataclasses import dataclass
from pathlib import Path

from ai_dev_system.config import Config
from ai_dev_system.feature_flags import FeatureFlags
from ai_dev_system.normalize import normalize_idea
from ai_dev_system.debate.agents import AgentRegistry
from ai_dev_system.debate.config import DebateConfig
from ai_dev_system.debate.questions import generate_questions
from ai_dev_system.debate.questions.pipeline import run_pipeline as run_question_pipeline_v2
from ai_dev_system.debate.engine import run_debate
from ai_dev_system.debate.report import DebateReport
from ai_dev_system.intake.digest import brief_digest as build_brief_digest
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


def _load_intake_brief(conn, run_id: str) -> dict | None:
    """Read the INTAKE_BRIEF artifact for a run, if any. Returns the parsed
    brief.json dict, or None when:
      - the run is legacy (pre-v5 or pipeline_version=1) — never has a brief,
      - the run has no intake_brief_id set,
      - the artifact row / brief.json file is missing.
    """
    from ai_dev_system.migration.classify import is_legacy_run
    if is_legacy_run(conn, run_id):
        return None

    row = conn.execute(
        "SELECT intake_brief_id FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if row is None or not row["intake_brief_id"]:
        return None
    art = conn.execute(
        "SELECT content_ref FROM artifacts WHERE artifact_id = ?",
        (row["intake_brief_id"],),
    ).fetchone()
    if art is None:
        return None
    brief_path = Path(art["content_ref"]) / "brief.json"
    if not brief_path.exists():
        return None
    with open(brief_path, encoding="utf-8") as f:
        return json.load(f)


@dataclass
class DebatePipelineResult:
    run_id: str
    debate_report: DebateReport
    artifact_id: str


def _question_path(
    flags: FeatureFlags,
    brief_v1: dict,
    brief_v2: dict | None,
    llm_client,
):
    """Spec phase1-migration §Dispatcher: question gen routes by flag.

    Returns `(questions, decisions, digest)` where `decisions` and
    `digest` are populated only on the v2 path (None otherwise).

    Fallback contract: when `use_question_pipeline_v2` is on but no
    brief_v2 is available, warn and degrade to v1 rather than failing
    the run — the linear flag order requires `use_intake_wizard` first,
    but legacy callers may still slip through.

    NOTE: dispatch telemetry events (DISPATCH_*) are deferred — adding
    them requires extending the events.event_type CHECK constraint via
    a schema migration. The flag snapshot in DebateReport.brief + the
    presence/absence of `decisions` already exposes which path ran for
    eval purposes.
    """
    if flags.use_question_pipeline_v2 and brief_v2 is not None:
        digest = build_brief_digest(brief_v2)
        result = run_question_pipeline_v2(brief_v2, digest, llm_client)
        return result.questions_final, result.decisions, digest

    if flags.use_question_pipeline_v2 and brief_v2 is None:
        warnings.warn(
            "use_question_pipeline_v2=true but no brief_v2 supplied; "
            "falling back to legacy generate_questions.",
            stacklevel=2,
        )

    questions = generate_questions(brief_v1, llm_client)
    return questions, None, None


def _debate_path(
    flags: FeatureFlags,
    questions,
    llm_client,
    *,
    run_id: str,
    brief: dict,
    decisions,
    digest: str | None,
    progress=None,
) -> DebateReport:
    """Spec phase1-migration §Dispatcher: debate routes by flag.

    Flag-on (`use_debate_v2`) wires DebateConfig + AgentRegistry +
    brief_digest + decisions into the engine. Flag-off keeps the v1
    no-kwargs call shape so existing fixtures stay deterministic.

    `progress` (default no-op) is forwarded to the engine for live UX.
    """
    if flags.use_debate_v2:
        registry = AgentRegistry.from_directory()
        return run_debate(
            questions, llm_client, run_id=run_id, brief=brief,
            config=DebateConfig(),
            registry=registry,
            brief_digest=digest,
            decisions=decisions,
            progress=progress,
        )

    return run_debate(
        questions, llm_client, run_id=run_id, brief=brief, progress=progress
    )


def run_debate_pipeline(
    raw_idea: str,
    config: Config,
    conn,
    project_id: str,
    llm_client,
    *,
    brief_v2: dict | None = None,
    flags: FeatureFlags | None = None,
    progress=None,
) -> DebatePipelineResult:
    """Phase A: normalize → question gen → debate → DEBATE_REPORT artifact → PAUSED_AT_GATE_1.

    Feature-flag dispatch (snapshot once at entry per
    phase1-migration-plan §Dispatcher Pattern):
        - `use_question_pipeline_v2` + `brief_v2` → 4-stage pipeline
          (Inventory → Materializer → Critic → Coverage); else legacy.
        - `use_debate_v2` → engine wired with AgentRegistry,
          DebateConfig, brief_digest, and Decision list; else v1.

    `brief_v2` is supplied by the caller (e.g. start-project skill
    when use_intake_wizard is enabled). When omitted, the v2 question
    pipeline silently degrades to v1 so legacy callers keep working.
    """
    run_repo = RunRepo(conn)
    task_run_repo = TaskRunRepo(conn)
    event_repo = EventRepo(conn)

    # Snapshot flags once at entry.
    active_flags = flags or FeatureFlags.from_env()

    # Run created with status=RUNNING_PHASE_1A
    run_id = run_repo.create(project_id=project_id, pipeline_type="debate_pipeline")

    # Step 1: Normalize (legacy v1 brief — still needed as fallback
    # input to v1 question gen + as the `brief` field of the
    # DebateReport for backwards-compat downstream consumers).
    brief = normalize_idea(raw_idea)
    # Stamp the flag snapshot onto the brief so it travels with the
    # DebateReport artifact (eval/audit can read which path ran).
    brief = {**brief, "_flags": active_flags.snapshot()}

    # Step 2: Generate questions (flag-dispatched)
    task_run = task_run_repo.create_sync(run_id, task_type="generate_questions")
    task_run["input_artifact_ids"] = []
    event_repo.insert(run_id, "TASK_STARTED", "debate_pipeline", task_run["task_run_id"])
    questions, decisions, digest = _question_path(
        active_flags, brief, brief_v2, llm_client,
    )

    # Step 3: Run debate (flag-dispatched)
    run_repo.update_status(run_id, "RUNNING_PHASE_1B")

    task_run = task_run_repo.create_sync(run_id, task_type="run_debate")
    task_run["input_artifact_ids"] = []
    event_repo.insert(run_id, "TASK_STARTED", "debate_pipeline", task_run["task_run_id"])
    debate_report = _debate_path(
        active_flags, questions, llm_client,
        run_id=run_id, brief=brief, decisions=decisions, digest=digest,
        progress=progress,
    )

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
        "SELECT status, current_artifacts FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    assert row["status"] == "RUNNING_PHASE_1D", (
        f"Expected RUNNING_PHASE_1D, got {row['status']}"
    )

    # Load approved_answers from artifact (current_artifacts is JSON TEXT in SQLite)
    from ai_dev_system.db.helpers import load_json
    current_artifacts = load_json(row["current_artifacts"], default={}) or {}
    aa_artifact_id = current_artifacts["approved_answers_id"]
    aa_artifact = conn.execute(
        "SELECT content_ref FROM artifacts WHERE artifact_id = ?", (aa_artifact_id,)
    ).fetchone()
    aa_path = os.path.join(aa_artifact["content_ref"], "approved_answers.json")
    with open(aa_path, encoding="utf-8") as f:
        approved_answers = json.load(f)

    # Optional: load INTAKE_BRIEF v2 if this run has one (Phase 1 v2 path).
    # Legacy runs (no intake_brief_id) get brief_v2=None → finalize_spec falls back
    # to the v1 prompt unchanged.
    brief_v2 = _load_intake_brief(conn, run_id)
    intake_brief_id = current_artifacts.get("intake_brief_id")

    # Step 1: finalize_spec
    task_run = task_run_repo.create_sync(run_id, task_type="finalize_spec")
    input_ids = [aa_artifact_id]
    if intake_brief_id:
        input_ids.append(intake_brief_id)
    task_run["input_artifact_ids"] = input_ids
    event_repo.insert(run_id, "TASK_STARTED", "debate_pipeline", task_run["task_run_id"])

    temp_path = build_temp_path(
        config.storage_root, run_id, task_run["task_id"], task_run["attempt_number"]
    )
    bundle = finalize_spec(
        approved_answers, run_id, llm_client,
        output_dir=Path(temp_path), brief_v2=brief_v2,
    )

    promoted = PromotedOutput(name="spec_bundle", artifact_type="SPEC_BUNDLE",
                              description="5-file spec bundle from Gate 1 answers")
    spec_artifact_id = promote_output(conn, config, task_run, promoted, temp_path)

    # Refresh bundle content from final artifact location
    spec_row = conn.execute(
        "SELECT content_ref FROM artifacts WHERE artifact_id = ?", (spec_artifact_id,)
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

    # Name the file task_graph.json — materialize_task_runs reads exactly that
    # from the TASK_GRAPH_APPROVED artifact dir.
    temp_approved = _write_json_to_temp_debate(
        config, task_run_g2, gate2_result.graph, filename="task_graph.json"
    )
    graph_artifact_id = promote_output(
        conn, config, task_run_g2,
        PromotedOutput("task_graph_approved", "TASK_GRAPH_APPROVED", "Human-approved task graph"),
        temp_approved,
    )

    # Step 4: Beads sync
    beads_sync(run_id, gate2_result.graph, conn)

    # Commit before execution: run_execution spawns worker/background threads
    # that open their OWN connections (conn_factory) and must see the committed
    # TASK_GRAPH_APPROVED artifact + spec rows. Without this the materializer
    # raises "Artifact <id> not found". (get_connection opens autocommit-off.)
    conn.commit()

    # Step 5: Execution (only if agent provided)
    execution_result = None
    if agent is not None:
        # Advance out of RUNNING_PHASE_1D so materialize_task_runs flips the run
        # to RUNNING_EXECUTION (it only transitions from CREATED / RUNNING_PHASE_2A
        # / RUNNING_PHASE_3). Without this the run stays at 1D, never reaches a
        # terminal state, and run_execution blocks forever.
        conn.execute(
            "UPDATE runs SET status='RUNNING_PHASE_3', last_activity_at=CURRENT_TIMESTAMP "
            "WHERE run_id=? AND status='RUNNING_PHASE_1D'",
            (run_id,),
        )
        conn.commit()
        execution_result = run_execution(
            run_id, graph_artifact_id, config, agent,
            poll_interval_s=config.poll_interval_s,
        )

        # Step 6: Phase V — Verification (only if execution succeeded)
        # Terminal states per runner.py: {"COMPLETED","FAILED","ABORTED","PAUSED_FOR_DECISION"}.
        # "SUCCESS" is not a run_status value; COMPLETED is the success terminal.
        if execution_result.status == "COMPLETED":
            if llm_client is not None:
                conn.execute(
                    "UPDATE runs SET status = 'RUNNING_PHASE_V', last_activity_at = CURRENT_TIMESTAMP "
                    "WHERE run_id = ? AND status = 'COMPLETED'",
                    (run_id,),
                )
                from ai_dev_system.verification.pipeline import run_phase_v_pipeline
                run_phase_v_pipeline(run_id, spec_artifact_id, config, conn, llm_client)

    # Persist Phase B's own writes (spec/graph promotion, Phase V status) so they
    # survive when the caller's connection is closed — worker-thread writes were
    # already committed on their own connections.
    conn.commit()

    return PhaseBResult(
        run_id=run_id,
        graph_artifact_id=graph_artifact_id,
        execution_result=execution_result,
    )


def _write_json_to_temp_debate(
    config: Config, task_run: dict, data: dict, filename: str | None = None
) -> str:
    """Write dict as JSON to temp path. Returns temp_path directory.

    `filename` defaults to ``<task_id>.json``; pass an explicit name when a
    downstream consumer expects a fixed file (e.g. the materializer reads the
    approved graph from ``task_graph.json``).
    """
    temp_path = build_temp_path(
        config.storage_root, run_id=task_run["run_id"],
        task_id=task_run["task_id"],
        attempt_number=task_run["attempt_number"],
    )
    os.makedirs(temp_path, exist_ok=True)
    name = filename or f"{task_run['task_id']}.json"
    with open(os.path.join(temp_path, name), "w", encoding="utf-8") as f:
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
            # Spec D9: surface the auto-resolve reason so Gate 1 can
            # render the OPTIONAL tier with context.
            "auto_resolution_reason": r.auto_resolution_reason,
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
                "source_decision_id": qdr.question.source_decision_id,
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
