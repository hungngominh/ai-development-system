"""End-to-end (stub, no Max): idea -> debate -> Gate 1 -> Phase B (spec, task
graph, Gate 2, execution, verification) reaches a terminal state, using a
conn_factory that hands out FRESH connections per worker thread (the real-usage
pattern). Replaces the throwaway scratch driver from the previous session.
"""
from unittest.mock import patch

import pytest

from ai_dev_system.agents.stub import StubAgent
from ai_dev_system.db.connection import get_connection
from ai_dev_system.debate.llm import StubDebateLLMClient
from ai_dev_system.debate_pipeline import run_debate_pipeline, run_phase_b_pipeline
from ai_dev_system.gate.gate1_bridge import Decision, finalize_gate1
from ai_dev_system.gate.stub_gate2 import StubGate2IO

_DECISIONS = [
    Decision(
        question_id="Q1", question_text="Who are the primary users?",
        classification="stakeholder", resolution_type="FORCED_HUMAN",
        answer="Individual developers", options_considered=["a", "b"],
        rationale="human pick",
    ),
    Decision(
        question_id="Q2", question_text="Sub-tasks?",
        classification="functional", resolution_type="CONSENSUS",
        answer="One level", options_considered=["no", "one"],
        rationale="agreed",
    ),
]


class _StubLLM:
    """complete() for spec/task-graph + judge_criterion() for Phase V."""

    def __init__(self):
        self._d = StubDebateLLMClient()

    def complete(self, system, user):
        return self._d.complete(system, user)

    def judge_criterion(self, criterion_id, criterion_text, evidence):
        return ("PASS", 1.0, "stub default")


@pytest.mark.integration
@patch("subprocess.run")
def test_loop_closes_to_terminal(mock_subproc, file_config, project_id):
    mock_subproc.return_value = type("R", (), {"returncode": 0, "stderr": b""})()
    url = file_config.database_url

    # Phase A + Gate 1 (committed so fresh connections can see it).
    conn = get_connection(url)
    res = run_debate_pipeline("Build a tiny todo CLI", file_config, conn, project_id, StubDebateLLMClient())
    finalize_gate1(res.run_id, _DECISIONS, file_config.storage_root, conn)
    conn.commit()
    conn.close()

    # Phase B via conn_factory = NEW connection each call (worker threads).
    result = run_phase_b_pipeline(
        run_id=res.run_id,
        config=file_config,
        conn_factory=lambda: get_connection(url),
        gate2_io=StubGate2IO(action="approve"),
        llm_client=_StubLLM(),
        agent=StubAgent(),
    )

    assert result.execution_result is not None
    assert result.execution_result.status == "COMPLETED", result.execution_result.status

    verify = get_connection(url)
    try:
        final = verify.execute("SELECT status FROM runs WHERE run_id=?", (res.run_id,)).fetchone()["status"]
    finally:
        verify.close()
    assert final in ("PAUSED_AT_GATE_3", "COMPLETED"), final
