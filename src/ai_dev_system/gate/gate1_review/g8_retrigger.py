# src/ai_dev_system/gate/gate1_review/g8_retrigger.py
"""Gate 1 G8 — Brief edit re-trigger logic.

Per spec: 2026-05-25-g8-brief-edit-retrigger.md
When scope-affecting fields are edited during Gate 1 review, re-run the
Materializer (Stage 2) for affected decisions and append new questions
to the QUESTION_COVERAGE_REPORT artifact.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from ai_dev_system.db.helpers import new_uuid
from ai_dev_system.db.repos.events import EventRepo

if TYPE_CHECKING:
    import sqlite3

logger = logging.getLogger(__name__)

MAX_RETRIGGER_COUNT = 5


def run_g8_retrigger(
    run_id: str,
    brief_edits: list[dict],   # [{field_name, operation, value}, ...]
    brief: dict,
    conn: "sqlite3.Connection",
    llm_client,
) -> dict:
    """Orchestrate G8 brief-edit re-trigger.

    Returns summary dict with keys:
        noop          — True if no affected decisions
        new_questions — list of new question dicts appended
        retrigger_count — updated count after this run
    """
    from ai_dev_system.debate.questions import materializer, critic
    from ai_dev_system.gate.gate1_review.loader import _load_decision_inventory
    from ai_dev_system.intake.digest import brief_digest

    event_repo = EventRepo(conn)
    edited_fields = list({e["field_name"] for e in brief_edits})

    # Check retrigger threshold
    retrigger_count = _load_retrigger_count(conn, run_id)
    event_repo.insert(run_id, "G8_RETRIGGER_STARTED", "gate1",
                      payload={"edited_fields": edited_fields,
                               "retrigger_count": retrigger_count})

    if retrigger_count >= MAX_RETRIGGER_COUNT:
        event_repo.insert(run_id, "BRIEF_EDIT_THRESHOLD_EXCEEDED", "gate1",
                          payload={"retrigger_count": retrigger_count,
                                   "edited_fields": edited_fields})
        logger.warning("G8 retrigger_count=%d >= %d; continuing per Decision #41",
                       retrigger_count, MAX_RETRIGGER_COUNT)

    # Load decision inventory
    decisions = _load_decision_inventory(conn, run_id)
    if not decisions:
        # No inventory (legacy run) → re-materialize all decisions is out of scope;
        # treat as noop and log
        event_repo.insert(run_id, "G8_NOOP", "gate1",
                          payload={"reason": "no_decision_inventory",
                                   "edited_fields": edited_fields})
        return {"noop": True, "new_questions": [], "retrigger_count": retrigger_count}

    # Compute affected decisions
    affected = _compute_affected_decisions(decisions, edited_fields)
    if not affected:
        event_repo.insert(run_id, "G8_NOOP", "gate1",
                          payload={"reason": "no_affected_decisions",
                                   "edited_fields": edited_fields})
        return {"noop": True, "new_questions": [], "retrigger_count": retrigger_count}

    # Recompute brief digest from edited brief
    new_digest = brief_digest(brief)

    # Stage 2: materialize new questions for affected decisions
    new_questions_raw = materializer.run(
        decisions=affected,
        brief_digest=new_digest,
        llm_client=llm_client,
        mode="retrigger",
    )

    # Apply -r{N} suffix to IDs
    next_count = retrigger_count + 1
    for q in new_questions_raw:
        q.id = f"{q.id}-r{next_count}"

    # Stage 3: critic loop on new questions only
    if new_questions_raw:
        new_questions_raw, _ = critic.run(
            questions=new_questions_raw,
            brief_digest=new_digest,
            llm_client=llm_client,
        )

    # Append to QUESTION_COVERAGE_REPORT
    new_question_ids = _append_to_coverage_report(
        conn, run_id, new_questions_raw, edited_fields, affected, next_count
    )

    # Persist updated retrigger count
    _save_retrigger_count(conn, run_id, next_count)

    event_repo.insert(run_id, "G8_RETRIGGER_COMPLETED", "gate1",
                      payload={"affected_decision_ids": [d.id for d in affected],
                               "new_question_ids": new_question_ids})

    return {
        "noop": False,
        "new_questions": new_question_ids,
        "retrigger_count": next_count,
    }


def _compute_affected_decisions(decisions, edited_fields: list[str]):
    """Filter decisions whose brief_field_refs overlap with edited_fields.

    Per spec: if brief_field_refs is empty on all decisions (legacy inventory),
    return all decisions (worst-case full re-materialize).
    """
    edited_set = set(edited_fields)
    has_any_refs = any(getattr(d, "brief_field_refs", []) for d in decisions)

    if not has_any_refs:
        return decisions  # legacy: re-materialize everything

    return [
        d for d in decisions
        if set(getattr(d, "brief_field_refs", [])) & edited_set
    ]


def _append_to_coverage_report(
    conn,
    run_id: str,
    new_questions,
    edited_fields: list[str],
    affected_decisions,
    retrigger_count: int,
) -> list[str]:
    """Append new questions to QUESTION_COVERAGE_REPORT artifact.

    Returns list of new question IDs.
    """
    art_row = conn.execute(
        "SELECT artifact_id, content_ref FROM artifacts "
        "WHERE run_id = ? AND artifact_type = 'QUESTION_COVERAGE_REPORT' AND status = 'ACTIVE'",
        (run_id,),
    ).fetchone()

    if art_row is None:
        logger.warning("G8: no QUESTION_COVERAGE_REPORT for run %s; skipping append", run_id)
        return []

    report_path = Path(art_row["content_ref"]) / "coverage_report.json"
    if not report_path.exists():
        logger.warning("G8: coverage_report.json not found at %s", report_path)
        return []

    with open(report_path, encoding="utf-8") as f:
        report = json.load(f)

    new_q_dicts = []
    for q in new_questions:
        new_q_dicts.append({
            "id": q.id,
            "text": q.text,
            "classification": q.classification,
            "domain": q.domain,
            "agent_a": q.agent_a,
            "agent_b": q.agent_b,
            "source_decision_id": getattr(q, "source_decision_id", None),
        })

    report.setdefault("questions", []).extend(new_q_dicts)
    report.setdefault("retriggers", []).append({
        "retrigger_id": retrigger_count,
        "triggered_at": datetime.now(timezone.utc).isoformat(),
        "edited_fields": edited_fields,
        "affected_decision_ids": [d.id for d in affected_decisions],
        "new_question_ids": [q["id"] for q in new_q_dicts],
    })

    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    return [q["id"] for q in new_q_dicts]


def _load_retrigger_count(conn, run_id: str) -> int:
    row = conn.execute(
        "SELECT metadata FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if not row:
        return 0
    meta = row["metadata"]
    if isinstance(meta, str):
        meta = json.loads(meta) if meta.strip() else {}
    return int((meta or {}).get("g8_retrigger_count", 0))


def _save_retrigger_count(conn, run_id: str, count: int) -> None:
    row = conn.execute(
        "SELECT metadata FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if not row:
        return
    meta = row["metadata"]
    if isinstance(meta, str):
        meta = json.loads(meta) if meta.strip() else {}
    meta = meta or {}
    meta["g8_retrigger_count"] = count
    conn.execute(
        "UPDATE runs SET metadata = ? WHERE run_id = ?",
        (json.dumps(meta), run_id),
    )
