"""CLI entry point for Phase B Gate 2 operations: pause-at-gate2 and resume-after-gate2.

Two non-interactive modes the harness can spawn detached:

  --mode to-gate2 --run-id R
      Builds config/conn_factory/llm_client/llm_for (reusing run_phase_b factories),
      calls run_phase_b_to_gate2, prints JSON {run_id, status: PAUSED_AT_GATE_2}, exits 0.
      On error → stderr + exit 1.

  --mode resume-gate2 --run-id R --decision approve|reject
      Builds config/conn_factory/llm_client/llm_for AND agent (same as run_phase_b),
      calls resume_phase_b_after_gate2. PipelineAborted (reject is normal) → exit 0
      with JSON {run_id, status: aborted}. Unexpected errors → stderr + exit 1.

Factory functions (_make_llm_client, _make_agent) are imported directly from
run_phase_b — NOT duplicated.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from ai_dev_system.config import Config
from ai_dev_system.db.connection import get_connection
from ai_dev_system.debate_pipeline import (
    run_phase_b_to_gate2,
    resume_phase_b_after_gate2,
)
from ai_dev_system.pipeline import PipelineAborted

# Reuse factory helpers from run_phase_b — single source of truth
from ai_dev_system.cli.run_phase_b import _make_llm_client, _make_agent


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Phase B Gate 2 operations: pause-at-gate2 or resume-after-gate2.",
    )
    parser.add_argument(
        "--mode",
        required=True,
        choices=["to-gate2", "resume-gate2"],
        help="Operation: 'to-gate2' pauses at Gate 2; 'resume-gate2' resumes after Gate 2.",
    )
    parser.add_argument(
        "--run-id",
        required=True,
        dest="run_id",
        help="Run ID to operate on.",
    )
    parser.add_argument(
        "--decision",
        dest="decision",
        choices=["approve", "reject"],
        default=None,
        help="Required for --mode resume-gate2: approve or reject the task graph.",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)

    # Validate: resume-gate2 requires --decision
    if args.mode == "resume-gate2" and args.decision is None:
        print("--decision is required for --mode resume-gate2", file=sys.stderr)
        return 1

    # Load config + connection factory
    try:
        config = Config.from_env()
    except Exception as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 1

    conn_factory = lambda: get_connection(config.database_url)

    # Create LLM client (reused from run_phase_b)
    try:
        llm_client = _make_llm_client()
    except RuntimeError as exc:
        print(f"LLM configuration error: {exc}", file=sys.stderr)
        return 1

    # Per-step client resolver (real mode only); stub mode keeps single client.
    llm_for = None
    if os.environ.get("AI_DEV_STUB_LLM") != "1":
        from ai_dev_system.llm_factory import make_llm_client
        llm_for = make_llm_client

    if args.mode == "to-gate2":
        print(
            f"[Phase B] Running to Gate 2 (pause): finalize_spec → task_graph → PAUSED...",
            file=sys.stderr,
        )
        try:
            run_phase_b_to_gate2(
                run_id=args.run_id,
                config=config,
                conn_factory=conn_factory,
                llm_client=llm_client,
                llm_for=llm_for,
            )
        except Exception as exc:
            print(f"Pipeline error: {exc}", file=sys.stderr)
            return 1

        print(json.dumps({"run_id": args.run_id, "status": "PAUSED_AT_GATE_2"}))
        return 0

    else:  # resume-gate2
        # Build agent (reused from run_phase_b)
        try:
            agent = _make_agent()
        except RuntimeError as exc:
            print(f"Agent configuration error: {exc}", file=sys.stderr)
            return 1

        print(
            f"[Phase B] Resuming after Gate 2 (decision={args.decision})...",
            file=sys.stderr,
        )
        try:
            resume_phase_b_after_gate2(
                run_id=args.run_id,
                config=config,
                conn_factory=conn_factory,
                decision=args.decision,
                agent=agent,
                llm_client=llm_client,
                llm_for=llm_for,
            )
        except PipelineAborted:
            # Reject is a normal outcome — exit 0 with aborted status
            print(json.dumps({"run_id": args.run_id, "status": "aborted", "decision": args.decision}))
            return 0
        except Exception as exc:
            print(f"Pipeline error: {exc}", file=sys.stderr)
            return 1

        print(json.dumps({"run_id": args.run_id, "status": "resumed", "decision": args.decision}))
        return 0


if __name__ == "__main__":
    sys.exit(main())
