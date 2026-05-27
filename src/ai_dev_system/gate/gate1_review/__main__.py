# src/ai_dev_system/gate/gate1_review/__main__.py
"""CLI entry point for Gate 1 review module (G5).

Usage:
    python -m ai_dev_system.gate.gate1_review load <run_id>
        → JSON: {sections, brief_header, pending_count, is_legacy}

    python -m ai_dev_system.gate.gate1_review parse --run-id <id> --input "<text>"
                                                     [--pending-forced N]
                                                     [--pending-pf N]
        → JSON: {action, target, choice, payload, message, accepted}

    python -m ai_dev_system.gate.gate1_review render --run-id <id>
        → Full markdown Gate 1 review output

    python -m ai_dev_system.gate.gate1_review finalize --run-id <id>
                                                       --decisions-json '<json>'
        → JSON: {aa_id, dl_id, status}

Called by the `/review-debate` skill. All output is JSON so the skill
can parse structured results.
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
    result = parse_user_input(
        args.input,
        pending_forced=args.pending_forced,
        pending_parse_failed=args.pending_pf,
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

    aa_id, dl_id = finalize_gate1(args.run_id, decisions, config.storage_root, conn)
    print(json.dumps({"status": "ok", "aa_id": aa_id, "dl_id": dl_id}))


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
    p_parse = sub.add_parser("parse", help="Parse user input → JSON action")
    p_parse.add_argument("--run-id", required=True)
    p_parse.add_argument("--input", required=True)
    p_parse.add_argument("--pending-forced", type=int, default=0)
    p_parse.add_argument("--pending-pf", type=int, default=0)

    # finalize
    p_fin = sub.add_parser("finalize", help="Finalize Gate 1 → write artifacts")
    p_fin.add_argument("--run-id", required=True)
    p_fin.add_argument("--decisions-json", required=True)

    args = parser.parse_args()
    try:
        {
            "load": cmd_load,
            "render": cmd_render,
            "parse": cmd_parse,
            "finalize": cmd_finalize,
        }[args.command](args)
    except Exception as exc:
        print(json.dumps({"error": str(exc), "status": "error"}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
