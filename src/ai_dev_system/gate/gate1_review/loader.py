# src/ai_dev_system/gate/gate1_review/loader.py
"""Gate 1 review — artifact loader (G1).

Loads all artifacts needed for Gate 1 review into GateReviewContext.

Required artifact:
    DEBATE_REPORT — always present when run is PAUSED_AT_GATE_1.

Optional artifacts (v2 path only; None for legacy runs):
    INTAKE_BRIEF         — brief.json with full v2 fields
    DECISION_INVENTORY   — decisions.json (list of Decision dicts)
    QUESTION_COVERAGE_REPORT — coverage_report.json
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ai_dev_system.db.helpers import load_json
from ai_dev_system.debate.questions.models import Decision
from ai_dev_system.debate.report import Question
from ai_dev_system.migration.classify import is_legacy_run


@dataclass
class GateReviewContext:
    run_id: str
    project_name: str
    brief: dict                         # brief v2 or stub {raw_idea: ...} for legacy
    is_legacy_brief: bool
    debate_report: dict                 # raw JSON from debate_report.json
    decisions: list[Decision] | None    # None if legacy (no decision inventory)
    questions: list[Question]           # extracted from debate_report results
    coverage_report: dict | None        # None if legacy or missing
    # decision lookup by id (derived from decisions list)
    decision_by_id: dict[str, Decision] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.decision_by_id = (
            {d.id: d for d in self.decisions}
            if self.decisions is not None
            else {}
        )


def load_gate1_context(run_id: str, conn) -> GateReviewContext:
    """Load all Gate 1 artifacts for `run_id`.

    Raises ValueError if the run is not found or has no DEBATE_REPORT.
    """
    row = conn.execute(
        "SELECT title, current_artifacts, intake_brief_id FROM runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Run not found: {run_id!r}")

    current_artifacts = load_json(row["current_artifacts"], default={}) or {}
    project_name = row["title"] or run_id
    legacy = is_legacy_run(conn, run_id)

    # DEBATE_REPORT (required)
    debate_report_id = current_artifacts.get("debate_report_id")
    if not debate_report_id:
        raise ValueError(
            f"Run {run_id!r} has no DEBATE_REPORT artifact (current_artifacts has no debate_report_id)"
        )
    debate_report = _load_artifact_json(conn, debate_report_id, "debate_report.json")

    # Extract questions from debate report results
    questions = _extract_questions(debate_report)

    # INTAKE_BRIEF (optional — v2 path only)
    brief: dict
    is_legacy_brief: bool
    intake_brief_id = row["intake_brief_id"] or current_artifacts.get("intake_brief_id")
    if intake_brief_id and not legacy:
        try:
            brief = _load_artifact_json(conn, intake_brief_id, "brief.json")
            is_legacy_brief = False
        except (ValueError, FileNotFoundError):
            brief = debate_report.get("brief", {})
            is_legacy_brief = True
    else:
        # Legacy: reuse the brief field stamped onto DebateReport (v1 normalized dict)
        brief = debate_report.get("brief", {})
        is_legacy_brief = True

    # DECISION_INVENTORY (optional — v2 path only)
    decisions: list[Decision] | None = None
    if not legacy:
        decisions = _load_decision_inventory(conn, run_id)

    # QUESTION_COVERAGE_REPORT (optional — v2 path only)
    coverage_report: dict | None = None
    if not legacy:
        coverage_report = _load_coverage_report(conn, run_id)

    return GateReviewContext(
        run_id=run_id,
        project_name=project_name,
        brief=brief,
        is_legacy_brief=is_legacy_brief,
        debate_report=debate_report,
        decisions=decisions,
        questions=questions,
        coverage_report=coverage_report,
    )


# ---- internal helpers ----


def _load_artifact_json(conn, artifact_id: str, filename: str) -> dict:
    art = conn.execute(
        "SELECT content_ref FROM artifacts WHERE artifact_id = ?",
        (artifact_id,),
    ).fetchone()
    if art is None:
        raise ValueError(f"Artifact {artifact_id!r} not found in DB")
    path = Path(art["content_ref"]) / filename
    if not path.exists():
        raise FileNotFoundError(f"Artifact file not found: {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _extract_questions(debate_report: dict) -> list[Question]:
    questions: list[Question] = []
    for qdr in debate_report.get("results", []):
        q = qdr.get("question", {})
        questions.append(Question(
            id=q["id"],
            text=q["text"],
            classification=q["classification"],
            domain=q["domain"],
            agent_a=q["agent_a"],
            agent_b=q["agent_b"],
            source_decision_id=q.get("source_decision_id"),
        ))
    return questions


def _load_decision_inventory(conn, run_id: str) -> list[Decision] | None:
    art = conn.execute(
        "SELECT content_ref FROM artifacts "
        "WHERE run_id = ? AND artifact_type = 'DECISION_INVENTORY' AND status = 'ACTIVE'",
        (run_id,),
    ).fetchone()
    if art is None:
        return None
    path = Path(art["content_ref"]) / "decisions.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return [_decision_from_dict(d) for d in data]


def _load_coverage_report(conn, run_id: str) -> dict | None:
    art = conn.execute(
        "SELECT content_ref FROM artifacts "
        "WHERE run_id = ? AND artifact_type = 'QUESTION_COVERAGE_REPORT' AND status = 'ACTIVE'",
        (run_id,),
    ).fetchone()
    if art is None:
        return None
    path = Path(art["content_ref"]) / "coverage_report.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _decision_from_dict(d: dict) -> Decision:
    return Decision(
        id=d["id"],
        summary=d["summary"],
        classification=d["classification"],
        domain_hints=d.get("domain_hints", []),
        blocks_what=d.get("blocks_what", []),
        has_safe_default=d.get("has_safe_default", False),
        brief_field_refs=d.get("brief_field_refs", []),
    )
