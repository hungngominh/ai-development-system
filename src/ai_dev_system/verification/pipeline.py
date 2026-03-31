import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from ai_dev_system.agents.base import PromotedOutput
from ai_dev_system.config import Config
from ai_dev_system.db.repos.events import EventRepo
from ai_dev_system.db.repos.task_runs import TaskRunRepo
from ai_dev_system.storage.paths import build_temp_path
from ai_dev_system.storage.promote import promote_output
from ai_dev_system.verification.collector import collect_evidence
from ai_dev_system.verification.judge import VerificationLLMClient
from ai_dev_system.verification.report import CriterionResult, VerificationReport

logger = logging.getLogger(__name__)


def task_run_repo_create(run_id: str, task_type: str, conn) -> dict:
    """Thin wrapper so unit tests can patch it without patching the whole class."""
    return TaskRunRepo(conn).create_sync(run_id, task_type)


def run_phase_v_pipeline(
    run_id: str,
    spec_artifact_id: str,
    config: Config,
    conn,
    llm: VerificationLLMClient,
) -> VerificationReport:
    """
    Phase V-A standalone entry point.

    Precondition:  run.status = RUNNING_PHASE_V (caller must set this before calling)
    Postcondition: run.status = PAUSED_AT_GATE_3

    Returns VerificationReport (also written as VERIFICATION_REPORT artifact).
    """
    report = run_verification(run_id, spec_artifact_id, config, conn, llm)

    conn.execute(
        "UPDATE runs SET status = %s, last_activity_at = now() WHERE run_id = %s",
        ("PAUSED_AT_GATE_3", run_id),
    )
    EventRepo(conn).insert(run_id, "VERIFICATION_COMPLETED", "system")

    return report


def run_verification(
    run_id: str,
    spec_artifact_id: str,
    config: Config,
    conn,
    llm: VerificationLLMClient,
) -> VerificationReport:
    """
    Internal: collect evidence → LLM judge per criterion → persist VERIFICATION_REPORT artifact.

    VERIFICATION_REPORT is NOT tracked in runs.current_artifacts.
    Access pattern: direct artifact table query (run_id + artifact_type='VERIFICATION_REPORT').
    """
    event_repo = EventRepo(conn)
    event_repo.insert(run_id, "VERIFICATION_STARTED", "system")

    # --- Attempt counter: count existing VERIFICATION_REPORT artifacts ---
    count_row = conn.execute(
        """
        SELECT COUNT(*) AS count FROM artifacts
        WHERE run_id = %s AND artifact_type = 'VERIFICATION_REPORT'
          AND status IN ('ACTIVE', 'SUPERSEDED')
        """,
        (run_id,),
    ).fetchone()
    attempt = (count_row["count"] if count_row else 0) + 1

    # --- Collect evidence from completed task_runs ---
    task_summary, evidence = collect_evidence(run_id, conn)

    # --- Load acceptance criteria from SPEC_BUNDLE artifact ---
    spec_row = conn.execute(
        "SELECT content_ref FROM artifacts WHERE artifact_id = %s",
        (spec_artifact_id,),
    ).fetchone()
    if spec_row is None:
        raise ValueError(f"Spec artifact not found: {spec_artifact_id}")
    criteria_list = _parse_acceptance_criteria(spec_row["content_ref"])

    # --- LLM judge each criterion ---
    criterion_results: list[CriterionResult] = []
    for cid, ctext in criteria_list:
        verdict, confidence, reasoning = llm.judge_criterion(cid, ctext, evidence)
        criterion_results.append(CriterionResult(
            criterion_id=cid,
            criterion_text=ctext,
            verdict=verdict,
            confidence=confidence,
            evidence=evidence[:3],  # include up to 3 excerpts in the result
            reasoning=reasoning,
            related_task_ids=list(task_summary.keys()),
        ))

    overall = "ALL_PASS" if all(c.verdict != "FAIL" for c in criterion_results) else "HAS_FAIL"

    report = VerificationReport(
        run_id=run_id,
        attempt=attempt,
        criteria=criterion_results,
        overall=overall,
        task_summary=task_summary,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )

    # --- Persist as VERIFICATION_REPORT artifact ---
    task_run = task_run_repo_create(run_id, "verification_report", conn)
    task_run["input_artifact_ids"] = [spec_artifact_id]

    temp_path = build_temp_path(
        config.storage_root, run_id, task_run["task_id"], task_run["attempt_number"]
    )
    os.makedirs(temp_path, exist_ok=True)

    report_dict = _report_to_dict(report)
    with open(os.path.join(temp_path, "verification_report.json"), "w", encoding="utf-8") as f:
        json.dump(report_dict, f, indent=2, ensure_ascii=False)

    promote_output(
        conn, config, task_run,
        PromotedOutput("verification_report", "VERIFICATION_REPORT", "Phase 4 verification report"),
        temp_path,
    )

    return report


def _parse_acceptance_criteria(spec_bundle_path: str) -> list[tuple[str, str]]:
    """
    Parse acceptance-criteria.md from spec bundle directory.

    Returns list of (criterion_id, criterion_text) tuples.
    Looks for lines matching patterns like:
      - "AC-1: some text"
      - "**AC-1**: some text"
      - "## AC-1 — some text"
    """
    criteria_file = os.path.join(spec_bundle_path, "acceptance-criteria.md")
    if not os.path.exists(criteria_file):
        logger.warning(
            "acceptance-criteria.md not found in spec bundle: %s — "
            "verification will produce vacuous ALL_PASS with zero criteria",
            spec_bundle_path,
        )
        return []

    with open(criteria_file, encoding="utf-8") as f:
        content = f.read()

    results = []
    # Match lines like: AC-1: text, AC-1 — text, **AC-1**: text, ## AC-1 — text
    pattern = re.compile(
        r"(?:^|\n)[#\s]*(?:\*\*)?(?P<id>AC-\d+)(?:\*\*)?[\s:—\-]+(?P<text>[^\n]+)",
        re.MULTILINE,
    )
    for m in pattern.finditer(content):
        results.append((m.group("id").strip(), m.group("text").strip()))

    return results


def _report_to_dict(report: VerificationReport) -> dict:
    return {
        "run_id": report.run_id,
        "attempt": report.attempt,
        "overall": report.overall,
        "generated_at": report.generated_at,
        "criteria": [
            {
                "criterion_id": c.criterion_id,
                "criterion_text": c.criterion_text,
                "verdict": c.verdict,
                "confidence": c.confidence,
                "evidence": c.evidence,
                "reasoning": c.reasoning,
                "related_task_ids": c.related_task_ids,
            }
            for c in report.criteria
        ],
        "task_summary": {
            k: {
                "task_id": v.task_id,
                "done_definition_met": v.done_definition_met,
                "output_artifact_id": v.output_artifact_id,
                "verification_step_results": v.verification_step_results,
            }
            for k, v in report.task_summary.items()
        },
    }
