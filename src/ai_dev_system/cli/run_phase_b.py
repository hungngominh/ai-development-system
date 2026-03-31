"""CLI entry point for Phase B: finalize_spec → task_graph → execution → verification."""
from __future__ import annotations

import argparse
import json
import os
import sys

from ai_dev_system.config import Config
from ai_dev_system.db.connection import get_connection
from ai_dev_system.debate_pipeline import run_phase_b_pipeline
from ai_dev_system.gate.stub_gate2 import StubGate2IO


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Run Phase B pipeline (finalize spec → task graph → execution → verification)."
    )
    parser.add_argument(
        "--run-id",
        required=True,
        dest="run_id",
        help="Run ID from a completed Phase A (PAUSED_AT_GATE_1 or later).",
    )
    return parser.parse_args(argv)


def _make_llm_client():
    """Return None (skip Phase V) if AI_DEV_STUB_LLM=1, else real unified LLM client."""
    if os.environ.get("AI_DEV_STUB_LLM") == "1":
        return None  # run_phase_b_pipeline guards: if llm_client is not None → runs Phase V
    from ai_dev_system.llm_factory import make_real_llm_client
    return make_real_llm_client()


def _make_agent():
    """Return StubAgent if AI_DEV_STUB_LLM=1, else real CrewAI agent."""
    if os.environ.get("AI_DEV_STUB_LLM") == "1":
        from ai_dev_system.agents.stub import StubAgent
        return StubAgent()
    from ai_dev_system.agents.crewai_agent import make_crewai_agent
    return make_crewai_agent()


def main(argv=None) -> int:
    args = _parse_args(argv)

    # Load config + connection factory
    try:
        config = Config.from_env()
    except Exception as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 1

    conn_factory = lambda: get_connection(config.database_url)

    # Create LLM client (may be None in stub mode)
    try:
        llm_client = _make_llm_client()
    except RuntimeError as exc:
        print(f"LLM configuration error: {exc}", file=sys.stderr)
        return 1

    # Create agent
    try:
        agent = _make_agent()
    except RuntimeError as exc:
        print(f"Agent configuration error: {exc}", file=sys.stderr)
        return 1

    gate2_io = StubGate2IO(action="approve")

    print("[Phase B] Running: finalize_spec → task_graph → Gate 2 → execution...", file=sys.stderr)
    if llm_client is None:
        print("         (AI_DEV_STUB_LLM=1: verification phase skipped)", file=sys.stderr)

    try:
        result = run_phase_b_pipeline(
            run_id=args.run_id,
            config=config,
            conn_factory=conn_factory,
            gate2_io=gate2_io,
            llm_client=llm_client,
            agent=agent,
        )
    except Exception as exc:
        print(f"Pipeline error: {exc}", file=sys.stderr)
        return 1

    # JSON output to stdout
    output = {
        "run_id": result.run_id,
        "graph_artifact_id": result.graph_artifact_id,
        "execution_status": (
            result.execution_result.status
            if result.execution_result is not None
            else "skipped"
        ),
    }
    print(json.dumps(output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
