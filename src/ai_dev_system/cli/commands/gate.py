"""ai-dev gate — human review gates.

Verbs:
- review-debate       — Gate 1: review debate report, approve/edit decisions
- review-graph        — Gate 2: review generated task graph
- review-verification — Gate 3: review verification results
"""
from __future__ import annotations

import typer

from ai_dev_system.cli.core.output import OutputRenderer
from ai_dev_system.cli.core.registry import command


@command(
    noun="gate",
    verb="review-debate",
    help="Gate 1: review debate report and approve decisions",
    noun_help="Human review gates (Gate 1/2/3)",
)
def gate_review_debate(
    run_id: str = typer.Option(..., "--run-id", help="Run UUID at DEBATE_COMPLETE status"),
    cmd: str = typer.Option(
        "render", "--cmd",
        help="Sub-command: load | render | parse | finalize",
    ),
    input_text: str = typer.Option("", "--input", help="User input for `parse` sub-command"),
    pending_forced: int = typer.Option(0, "--pending-forced", help="Forced items pending (for parse)"),
    pending_pf: int = typer.Option(0, "--pending-pf", help="Parse-failed items pending (for parse)"),
    decisions_json: str = typer.Option("", "--decisions-json", help="JSON array for `finalize`"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Bridge to the gate1_review Python package.

    Designed to be called by the review-debate skill — not interactive on its own.
    Sub-commands:
      load     — load gate context, print summary JSON
      render   — render the full Gate 1 review markdown
      parse    — parse one line of user input, return structured action JSON
      finalize — persist approved decisions, advance run status
    """
    import json
    import sys

    from ai_dev_system.config import Config
    from ai_dev_system.db.connection import get_connection
    from ai_dev_system.db.migrator import apply_schema

    out = OutputRenderer(mode="json" if json_output else "human")

    try:
        config = Config.from_env()
        conn = get_connection(config.database_url)
        apply_schema(conn)

        if cmd == "load":
            from ai_dev_system.gate.gate1_review.loader import load_gate1_context
            ctx = load_gate1_context(run_id, conn)
            payload = {
                "status": "ok",
                "run_id": run_id,
                "project_name": ctx.project_name,
                "is_legacy_brief": ctx.is_legacy_brief,
                "n_decisions": len(ctx.decisions) if ctx.decisions else 0,
                "n_questions": len(ctx.questions),
            }
            out.write(payload)

        elif cmd == "render":
            from ai_dev_system.gate.gate1_review.loader import load_gate1_context
            from ai_dev_system.gate.gate1_review.sections import build_sections
            from ai_dev_system.gate.gate1_review.renderer import render_all
            ctx = load_gate1_context(run_id, conn)
            sections = build_sections(ctx)
            print(render_all(ctx, sections))
            out.write({"status": "ok"})

        elif cmd == "parse":
            from ai_dev_system.gate.gate1_review.parser import parse_user_input
            result = parse_user_input(
                input_text,
                pending_forced=pending_forced,
                pending_parse_failed=pending_pf,
            )
            payload = {
                "status": "ok",
                "action_type": result.action_type,
                "target": result.target,
                "choice": result.choice,
                "payload": result.payload,
                "message": result.message,
                "accepted": result.accepted,
            }
            out.write(payload)

        elif cmd == "finalize":
            if not decisions_json.strip():
                out.write_error(code=1, message="--decisions-json required for finalize")
                raise typer.Exit(1)
            from ai_dev_system.gate.gate1_bridge import finalize_gate1, Decision as GateDecision
            decisions_data = json.loads(decisions_json)
            decisions = [GateDecision(**d) for d in decisions_data]
            aa_id, dl_id = finalize_gate1(run_id, decisions, config.storage_root, conn)
            out.write({"status": "ok", "approved_answers_id": aa_id, "decision_log_id": dl_id})

        else:
            out.write_error(code=1, message=f"Unknown gate sub-command: {cmd!r}")
            raise typer.Exit(1)

        conn.close()
        raise typer.Exit(0)
    except typer.Exit:
        raise
    except Exception as exc:
        out.write_error(code=2, message=f"gate review-debate failed: {exc}")
        raise typer.Exit(2)


@command(noun="gate", verb="review-graph", help="Gate 2: review generated task graph")
def gate_review_graph(
    run_id: str = typer.Option(..., "--run-id", help="Run UUID at GRAPH_GENERATED status"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Review the task graph at Gate 2."""
    out = OutputRenderer(mode="json" if json_output else "human")
    # Gate 2 review logic is handled by the skill; this surfaces basic info
    out.info(
        "Gate 2 review is skill-driven. "
        f"Run: ai-dev info {run_id} to see current artifacts."
    )
    out.write({"status": "ok", "run_id": run_id, "gate": 2})
    raise typer.Exit(0)


@command(noun="gate", verb="review-verification", help="Gate 3: review verification results")
def gate_review_verification(
    run_id: str = typer.Option(..., "--run-id", help="Run UUID at VERIFICATION_COMPLETE status"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Bridge to gate3_bridge for verification gate."""
    out = OutputRenderer(mode="json" if json_output else "human")

    try:
        from ai_dev_system.config import Config
        from ai_dev_system.db.connection import get_connection
        from ai_dev_system.gate.gate3_bridge import finalize_gate3

        config = Config.from_env()
        conn = get_connection(config.database_url)
        finalize_gate3(run_id, conn)
        conn.close()
        out.write({"status": "ok", "run_id": run_id, "gate": 3})
        raise typer.Exit(0)
    except typer.Exit:
        raise
    except Exception as exc:
        out.write_error(code=2, message=f"gate review-verification failed: {exc}")
        raise typer.Exit(2)
