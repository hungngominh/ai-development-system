# src/ai_dev_system/gate/gate1_review/__main__.py
"""CLI entry point for Gate 1 review module (G5+G10).

Usage:
    python -m ai_dev_system.gate.gate1_review load <run_id>
    python -m ai_dev_system.gate.gate1_review render --run-id <id>
    python -m ai_dev_system.gate.gate1_review parse  --run-id <id> --input "<text>"
    python -m ai_dev_system.gate.gate1_review finalize --run-id <id> --decisions-json '<json>'
    python -m ai_dev_system.gate.gate1_review save-state --run-id <id> --state-json '<json>'
    python -m ai_dev_system.gate.gate1_review load-state --run-id <id>
    python -m ai_dev_system.gate.gate1_review clear-state --run-id <id>

Called by the review-debate skill. All output is JSON.
"""

from __future__ import annotations

import argparse
import json
import sys

from ai_dev_system.config import Config
from ai_dev_system.db.connection import get_connection
from ai_dev_system.db.migrator import apply_schema
from ai_dev_system.gate.gate1_review.loader import load_gate1_context
from ai_dev_system.gate.gate1_review.parser import parse_user_input
from ai_dev_system.gate.gate1_review.renderer import (
    render_all,
    render_brief_header,
)
from ai_dev_system.gate.gate1_review.sections import build_sections, total_pending
from ai_dev_system.gate.gate1_review.state import (
    GateSessionState,
    load_state,
    save_state,
    clear_state,
)


def _get_conn():
    config = Config.from_env()
    conn = get_connection(config.database_url)
    apply_schema(conn)
    return conn


def cmd_load(args) -> None:
    """Load Gate 1 context and emit JSON summary for the skill."""
    conn = _get_conn()
    ctx = load_gate1_context(args.run_id, conn)
    sections = build_sections(ctx)
    pending = total_pending(sections)

    sections_data = []
    for s in sections:
        sections_data.append({
            "name": s.name,
            "count": len(s.items),
            "collapsed_by_default": s.collapsed_by_default,
            "pending": s.pending_count(),
            "items": [
                {
                    "question_id": i.question_id,
                    "question_text": i.question_text,
                    "classification": i.classification,
                    "domain": i.domain,
                    "resolution_status": i.resolution_status,
                    "confidence": i.confidence,
                    "decision_context": i.decision_context,
                    "auto_resolution_reason": i.auto_resolution_reason,
                }
                for i in s.items
            ],
        })

    print(json.dumps({
        "run_id": ctx.run_id,
        "project_name": ctx.project_name,
        "is_legacy": ctx.is_legacy_brief,
        "pending_count": pending,
        "sections": sections_data,
        "brief_header": render_brief_header(ctx),
    }, ensure_ascii=False, indent=2))


def cmd_render(args) -> None:
    """Render full Gate 1 markdown review."""
    conn = _get_conn()
    ctx = load_gate1_context(args.run_id, conn)
    sections = build_sections(ctx)
    print(render_all(ctx, sections))


def cmd_parse(args) -> None:
    """Parse a user input string and emit structured ParseResult as JSON."""
    llm = None
    if getattr(args, "llm", False):
        try:
            from ai_dev_system.llm_factory import make_real_llm_client
            llm = make_real_llm_client()
        except Exception:
            pass  # LLM unavailable — regex-only parse

    result = parse_user_input(
        args.input,
        pending_forced=args.pending_forced,
        pending_parse_failed=args.pending_pf,
        llm_client=llm,
    )
    print(json.dumps({
        "action": result.action_type,
        "target": result.target,
        "choice": result.choice,
        "payload": result.payload,
        "message": result.message,
        "accepted": result.accepted,
    }, ensure_ascii=False))


def cmd_finalize(args) -> None:
    """Finalize Gate 1 — write APPROVED_ANSWERS + DECISION_LOG artifacts."""
    from ai_dev_system.gate.gate1_bridge import Decision as GateDecision, finalize_gate1
    config = Config.from_env()
    conn = _get_conn()

    raw = json.loads(args.decisions_json)
    decisions = [
        GateDecision(
            question_id=d["question_id"],
            question_text=d["question_text"],
            classification=d["classification"],
            resolution_type=d["resolution_type"],
            answer=d["answer"],
            options_considered=d.get("options_considered", []),
            rationale=d.get("rationale", ""),
        )
        for d in raw
    ]

    # Load scope_affected flag before clearing state (G8)
    session = load_state(args.run_id, conn)
    scope_affected = session.scope_affected

    aa_id, dl_id = finalize_gate1(args.run_id, decisions, config.storage_root, conn)

    # G8: re-trigger question materializer for edited scope fields
    g8_result = None
    if scope_affected and session.brief_edits:
        from ai_dev_system.gate.gate1_review.g8_retrigger import run_g8_retrigger
        brief_edits_raw = [
            {"field_name": e.field_name, "operation": e.operation, "value": e.value}
            for e in session.brief_edits
        ]
        run_row = conn.execute(
            "SELECT current_artifacts FROM runs WHERE run_id = ?", (args.run_id,)
        ).fetchone()
        from ai_dev_system.db.helpers import load_json
        current_artifacts = load_json(run_row["current_artifacts"], default={}) or {}
        brief_id = current_artifacts.get("intake_brief_id")
        brief: dict = {}
        if brief_id:
            from ai_dev_system.gate.gate1_review.loader import _load_artifact_json
            try:
                brief = _load_artifact_json(conn, brief_id, "brief.json")
            except Exception:
                pass
        try:
            import os as _os
            from ai_dev_system.debate.llm import StubDebateLLMClient
            if _os.environ.get("AI_DEV_STUB_LLM") == "1":
                llm_client = StubDebateLLMClient()
            else:
                from ai_dev_system.llm_factory import make_real_llm_client
                llm_client = make_real_llm_client()
            g8_result = run_g8_retrigger(args.run_id, brief_edits_raw, brief, conn, llm_client)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("G8 retrigger failed: %s", exc)
            g8_result = {"noop": True, "error": str(exc)}

    # Clear session state after successful finalize
    clear_state(args.run_id, conn)
    print(json.dumps({
        "status": "ok",
        "aa_id": aa_id,
        "dl_id": dl_id,
        "scope_affected": scope_affected,  # G8: skill shows warning if True
        "g8": g8_result,
    }))


def cmd_save_state(args) -> None:
    """Persist in-progress review state to runs.gate1_session_state."""
    conn = _get_conn()
    state = GateSessionState.from_json(args.run_id, args.state_json)
    save_state(args.run_id, state, conn)
    print(json.dumps({"status": "ok", "run_id": args.run_id}))


def cmd_load_state(args) -> None:
    """Load persisted review state; returns empty state if none saved."""
    conn = _get_conn()
    state = load_state(args.run_id, conn)
    print(json.dumps({
        "status": "ok",
        "run_id": state.run_id,
        "resolved": {
            qid: {
                "choice": r.choice,
                "override": r.override_text,
                "resolution_type": r.resolution_type,
            }
            for qid, r in state.resolved.items()
        },
        "brief_edits": [
            {"field": e.field_name, "operation": e.operation, "value": e.value}
            for e in state.brief_edits
        ],
        "approved_all": state.approved_all,
        "scope_affected": state.scope_affected,  # G8
    }, ensure_ascii=False))


def cmd_clear_state(args) -> None:
    """Clear session state (e.g. on abort)."""
    conn = _get_conn()
    clear_state(args.run_id, conn)
    print(json.dumps({"status": "ok", "run_id": args.run_id}))


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m ai_dev_system.gate.gate1_review",
        description="Gate 1 review CLI (G5)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # load
    p_load = sub.add_parser("load", help="Load Gate 1 context as JSON")
    p_load.add_argument("run_id")

    # render
    p_render = sub.add_parser("render", help="Render full Gate 1 markdown review")
    p_render.add_argument("--run-id", required=True)

    # parse
    p_parse = sub.add_parser("parse", help="Parse user input -> JSON action")
    p_parse.add_argument("--run-id", required=True)
    p_parse.add_argument("--input", required=True)
    p_parse.add_argument("--pending-forced", type=int, default=0)
    p_parse.add_argument("--pending-pf", type=int, default=0)
    p_parse.add_argument("--llm", action="store_true", default=False,
                         help="Enable LLM NLU fallback for ambiguous input (G9)")

    # finalize
    p_fin = sub.add_parser("finalize", help="Finalize Gate 1 -> write artifacts")
    p_fin.add_argument("--run-id", required=True)
    p_fin.add_argument("--decisions-json", required=True)

    # save-state (G10)
    p_ss = sub.add_parser("save-state", help="Persist review session state to DB")
    p_ss.add_argument("--run-id", required=True)
    p_ss.add_argument("--state-json", required=True)

    # load-state (G10)
    p_ls = sub.add_parser("load-state", help="Load persisted review session state from DB")
    p_ls.add_argument("--run-id", required=True)

    # clear-state (G10)
    p_cs = sub.add_parser("clear-state", help="Clear review session state (after finalize or abort)")
    p_cs.add_argument("--run-id", required=True)

    args = parser.parse_args()
    try:
        {
            "load": cmd_load,
            "render": cmd_render,
            "parse": cmd_parse,
            "finalize": cmd_finalize,
            "save-state": cmd_save_state,
            "load-state": cmd_load_state,
            "clear-state": cmd_clear_state,
        }[args.command](args)
    except Exception as exc:
        print(json.dumps({"error": str(exc), "status": "error"}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
