"""Integration tests for run_phase_b_pipeline()."""
import pytest
from unittest.mock import patch

from ai_dev_system.debate_pipeline import run_debate_pipeline, run_phase_b_pipeline, PhaseBResult
from ai_dev_system.debate.llm import StubDebateLLMClient
from ai_dev_system.gate.gate1_bridge import finalize_gate1, Decision
from ai_dev_system.gate.stub_gate2 import StubGate2IO

RAW_IDEA = "Build a simple task manager for small teams."

_DECISIONS = [
    Decision(
        question_id="Q1",
        question_text="Who are the primary users?",
        classification="stakeholder",
        resolution_type="FORCED_HUMAN",
        answer="Small team leads and individual contributors",
        options_considered=["Team leads only", "All employees"],
        rationale="Human override: broader audience confirmed by product owner",
    ),
    Decision(
        question_id="Q2",
        question_text="Should tasks support sub-tasks?",
        classification="functional",
        resolution_type="CONSENSUS",
        answer="Yes, one level of sub-tasks",
        options_considered=["No sub-tasks", "One level", "Unlimited nesting"],
        rationale="Agents agreed on single-level nesting as sufficient",
    ),
]


def _run_phase_a_and_gate1(conn, config, project_id):
    """Helper: run Phase A + Gate 1 and return run_id."""
    client = StubDebateLLMClient()
    result = run_debate_pipeline(RAW_IDEA, config, conn, project_id, client)
    finalize_gate1(result.run_id, _DECISIONS, config.storage_root, conn)
    return result.run_id


@patch("subprocess.run")
def test_phase_b_returns_result(mock_subproc, conn, config, project_id):
    """Phase B returns PhaseBResult with a non-None graph_artifact_id."""
    mock_subproc.return_value = type("R", (), {"returncode": 0, "stderr": b""})()

    run_id = _run_phase_a_and_gate1(conn, config, project_id)

    client = StubDebateLLMClient()
    gate2_io = StubGate2IO(action="approve")

    result = run_phase_b_pipeline(
        run_id=run_id,
        config=config,
        conn_factory=lambda: conn,
        gate2_io=gate2_io,
        llm_client=client,
    )

    assert isinstance(result, PhaseBResult)
    assert result.run_id == run_id
    assert result.graph_artifact_id is not None


@patch("subprocess.run")
def test_phase_b_artifact_type_is_task_graph_approved(mock_subproc, conn, config, project_id):
    """The promoted artifact has type TASK_GRAPH_APPROVED."""
    mock_subproc.return_value = type("R", (), {"returncode": 0, "stderr": b""})()

    run_id = _run_phase_a_and_gate1(conn, config, project_id)

    client = StubDebateLLMClient()
    gate2_io = StubGate2IO(action="approve")

    result = run_phase_b_pipeline(
        run_id=run_id,
        config=config,
        conn_factory=lambda: conn,
        gate2_io=gate2_io,
        llm_client=client,
    )

    row = conn.execute(
        "SELECT artifact_type FROM artifacts WHERE artifact_id = %s",
        (result.graph_artifact_id,),
    ).fetchone()
    assert row["artifact_type"] == "TASK_GRAPH_APPROVED"


def test_phase_b_without_gate1_raises(conn, config, project_id):
    """Calling Phase B before Gate 1 raises AssertionError mentioning RUNNING_PHASE_1D."""
    client = StubDebateLLMClient()
    result = run_debate_pipeline(RAW_IDEA, config, conn, project_id, client)
    # Run is PAUSED_AT_GATE_1 — Gate 1 was NOT called, so status is wrong

    gate2_io = StubGate2IO(action="approve")

    with pytest.raises(AssertionError, match="RUNNING_PHASE_1D"):
        run_phase_b_pipeline(
            run_id=result.run_id,
            config=config,
            conn_factory=lambda: conn,
            gate2_io=gate2_io,
            llm_client=client,
        )
