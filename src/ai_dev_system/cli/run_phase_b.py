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
        description="Run Phase B pipeline (finalize spec -> task graph -> execution -> verification)."
    )
    parser.add_argument(
        "--run-id",
        required=True,
        dest="run_id",
        help="Run ID that has passed Gate 1 review (run must be in status RUNNING_PHASE_1D).",
    )
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        dest="auto_approve",
        help="Skip interactive Gate 2 review and approve the task graph as generated.",
    )
    return parser.parse_args(argv)


class _StubPhaseBLLM:
    """Deterministic stub covering BOTH protocols Phase B needs:
    `complete` (finalize_spec + task-graph generation) and `judge_criterion`
    (Phase V verification). Returning None here would crash finalize_spec, which
    also calls the client — so stub mode needs a real no-op client, not None.
    """

    def __init__(self) -> None:
        from ai_dev_system.debate.llm import StubDebateLLMClient
        from ai_dev_system.verification.judge import StubVerificationLLMClient
        self._debate = StubDebateLLMClient()
        self._verify = StubVerificationLLMClient({})

    def complete(self, system: str, user: str) -> str:
        return self._debate.complete(system, user)

    def judge_criterion(self, criterion_id: str, criterion_text: str, evidence: list[str]):
        return self._verify.judge_criterion(criterion_id, criterion_text, evidence)


def _make_llm_client():
    """Stub client (complete + judge_criterion) in stub mode, else the real
    unified client (Max via claude_code, or an API provider)."""
    if os.environ.get("AI_DEV_STUB_LLM") == "1":
        return _StubPhaseBLLM()
    from ai_dev_system.llm_factory import make_real_llm_client
    return make_real_llm_client()


def _make_gate2_io(auto_approve: bool = False):
    """Build the Gate 2 IO.

    - stub mode → StubGate2IO (auto-approve)
    - explicit --auto-approve or a non-TTY stdin → TerminalGate2IO in
      auto-approve mode (so backgrounded/piped runs don't block forever)
    - otherwise → interactive TerminalGate2IO
    """
    if os.environ.get("AI_DEV_STUB_LLM") == "1":
        return StubGate2IO(action="approve")
    from ai_dev_system.gate.terminal_gate2 import TerminalGate2IO
    non_tty = not sys.stdin.isatty()
    if non_tty and not auto_approve:
        print(
            "[Gate 2] stdin is not a TTY → auto-approving the task graph. "
            "Run in a terminal (or pass --auto-approve explicitly) to review interactively.",
            file=sys.stderr,
        )
    return TerminalGate2IO(auto_approve=auto_approve or non_tty)


def _make_agent():
    """Pick the execution agent.

    - stub mode → StubAgent
    - LLM_AGENT_BACKEND=claude_max (or LLM_PROVIDER=claude_code, no key) →
      ClaudeMaxAgent: routes execution through the `claude` CLI (Max), no API key
    - otherwise → CrewAI agent (needs a provider API key)
    """
    if os.environ.get("AI_DEV_STUB_LLM") == "1":
        from ai_dev_system.agents.stub import StubAgent
        return StubAgent()

    backend = os.environ.get("LLM_AGENT_BACKEND", "").strip().lower()
    if backend == "claude_max" or (not backend and os.environ.get("LLM_PROVIDER") == "claude_code"):
        from ai_dev_system.agents.claude_max_agent import make_claude_max_agent
        from ai_dev_system.llm_factory import resolve_step_model_effort
        model, effort = resolve_step_model_effort("executor")
        return make_claude_max_agent(model=model, effort=effort)

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

    # Per-step client resolver (real mode only): lets spec/task-graph/judge run
    # on different model tiers. Stub mode keeps the single deterministic client.
    llm_for = None
    if os.environ.get("AI_DEV_STUB_LLM") != "1":
        from ai_dev_system.llm_factory import make_llm_client
        llm_for = make_llm_client

    # Create agent
    try:
        agent = _make_agent()
    except RuntimeError as exc:
        print(f"Agent configuration error: {exc}", file=sys.stderr)
        return 1

    # Create Gate2 IO
    try:
        gate2_io = _make_gate2_io(auto_approve=args.auto_approve)
    except RuntimeError as exc:
        print(f"Gate2 IO error: {exc}", file=sys.stderr)
        return 1

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
            llm_for=llm_for,
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
