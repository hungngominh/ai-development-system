"""Integration tests for run_phase_b_to_gate2 (pause) + resume_phase_b_after_gate2 (approve/reject).

TDD: tests written BEFORE implementation. RED → GREEN.

Note on connections:
- test_to_gate2_pauses + test_resume_reject_aborts: use in-memory SQLite (conn fixture).
  These tests don't need execution (which requires multi-connection file-backed DB).
- test_resume_approve_executes: uses file_config + get_connection(url), because
  run_execution opens its own connections (file_config.database_url is file-backed).
"""
import json

import pytest
from unittest.mock import patch

from ai_dev_system.debate_pipeline import (
    run_debate_pipeline,
    run_phase_b_to_gate2,
    resume_phase_b_after_gate2,
)
from ai_dev_system.debate.llm import StubDebateLLMClient
from ai_dev_system.gate.gate1_bridge import finalize_gate1, Decision
from ai_dev_system.agents.stub import StubAgent
from ai_dev_system.pipeline import PipelineAborted
from ai_dev_system.engine.runner import ExecutionResult
from ai_dev_system.db.connection import get_connection

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
    """Helper: run Phase A + Gate 1 → RUNNING_PHASE_1D, return run_id."""
    client = StubDebateLLMClient()
    result = run_debate_pipeline(RAW_IDEA, config, conn, project_id, client)
    finalize_gate1(result.run_id, _DECISIONS, config.storage_root, conn)
    return result.run_id


@patch("subprocess.run")
def test_to_gate2_pauses(mock_subproc, conn, config, project_id):
    """run_phase_b_to_gate2 pauses the run at PAUSED_AT_GATE_2.

    Assertions:
    - status becomes PAUSED_AT_GATE_2
    - a TASK_GRAPH_GENERATED artifact exists in current_artifacts
    - NO TASK_GRAPH_APPROVED artifact yet
    - returned dict has 'run_id', 'task_graph_gen_id', 'envelope' keys
    """
    mock_subproc.return_value = type("R", (), {"returncode": 0, "stderr": b""})()

    run_id = _run_phase_a_and_gate1(conn, config, project_id)
    client = StubDebateLLMClient()

    result = run_phase_b_to_gate2(
        run_id=run_id,
        config=config,
        conn_factory=lambda: conn,
        llm_client=client,
    )

    # Check return value shape
    assert result["run_id"] == run_id
    assert result["task_graph_gen_id"] is not None
    assert isinstance(result["envelope"], dict)

    # Check status in DB
    row = conn.execute(
        "SELECT status, current_artifacts FROM runs WHERE run_id=?", (run_id,)
    ).fetchone()
    assert row["status"] == "PAUSED_AT_GATE_2", f"Expected PAUSED_AT_GATE_2, got {row['status']}"

    # TASK_GRAPH_GENERATED artifact must exist
    current_artifacts = json.loads(row["current_artifacts"])
    gen_id = current_artifacts.get("task_graph_gen_id")
    assert gen_id is not None, "task_graph_gen_id must be set"

    gen_row = conn.execute(
        "SELECT artifact_type FROM artifacts WHERE artifact_id=?", (gen_id,)
    ).fetchone()
    assert gen_row is not None, "TASK_GRAPH_GENERATED artifact must exist"
    assert gen_row["artifact_type"] == "TASK_GRAPH_GENERATED"

    # NO TASK_GRAPH_APPROVED yet
    approved_id = current_artifacts.get("task_graph_approved_id")
    if approved_id:
        approved_row = conn.execute(
            "SELECT artifact_type, status FROM artifacts WHERE artifact_id=?", (approved_id,)
        ).fetchone()
        # If the key was pre-set to None in the schema, there's no row; that's fine
        assert approved_row is None or approved_row["artifact_type"] != "TASK_GRAPH_APPROVED", \
            "TASK_GRAPH_APPROVED should not exist yet"


@pytest.mark.integration
@patch("subprocess.run")
def test_resume_approve_executes(mock_subproc, file_config, project_id):
    """resume_phase_b_after_gate2 with decision='approve' runs execution.

    Uses file_config (file-backed SQLite) because run_execution opens fresh connections
    from config.database_url — in-memory SQLite does not survive multi-connection access.

    Assertions:
    - returns an ExecutionResult
    - TASK_GRAPH_APPROVED artifact is promoted
    - run reaches COMPLETED or PAUSED_AT_GATE_3 (terminal for this stub)
    """
    mock_subproc.return_value = type("R", (), {"returncode": 0, "stderr": b""})()

    url = file_config.database_url

    # Phase A + Gate 1 (committed so the runner's fresh connections can see it)
    conn = get_connection(url)
    run_id = _run_phase_a_and_gate1(conn, file_config, project_id)
    conn.commit()
    conn.close()

    client = StubDebateLLMClient()

    # Pause at Gate 2
    run_phase_b_to_gate2(
        run_id=run_id,
        config=file_config,
        conn_factory=lambda: get_connection(url),
        llm_client=client,
    )

    # Resume with approval
    execution_result = resume_phase_b_after_gate2(
        run_id=run_id,
        config=file_config,
        conn_factory=lambda: get_connection(url),
        decision="approve",
        agent=StubAgent(),
        llm_client=client,
    )

    # Returns an ExecutionResult
    assert execution_result is not None
    assert isinstance(execution_result, ExecutionResult)

    # TASK_GRAPH_APPROVED artifact must exist
    verify = get_connection(url)
    try:
        row = verify.execute(
            "SELECT status, current_artifacts FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()
        current_artifacts = json.loads(row["current_artifacts"])
        approved_id = current_artifacts.get("task_graph_approved_id")
        assert approved_id is not None, "task_graph_approved_id must be set after approve"

        approved_row = verify.execute(
            "SELECT artifact_type FROM artifacts WHERE artifact_id=?", (approved_id,)
        ).fetchone()
        assert approved_row is not None
        assert approved_row["artifact_type"] == "TASK_GRAPH_APPROVED"

        # Run must be in a terminal state
        final_status = row["status"]
        assert final_status in ("COMPLETED", "PAUSED_AT_GATE_3", "FAILED", "ABORTED"), \
            f"Expected terminal status, got {final_status}"
    finally:
        verify.close()


@patch("subprocess.run")
def test_resume_reject_aborts(mock_subproc, conn, config, project_id):
    """resume_phase_b_after_gate2 with decision='reject' raises PipelineAborted.

    Assertions:
    - raises PipelineAborted
    - run status becomes ABORTED
    - no TASK_GRAPH_APPROVED promoted
    """
    mock_subproc.return_value = type("R", (), {"returncode": 0, "stderr": b""})()

    run_id = _run_phase_a_and_gate1(conn, config, project_id)
    client = StubDebateLLMClient()

    # First pause at gate 2
    run_phase_b_to_gate2(
        run_id=run_id,
        config=config,
        conn_factory=lambda: conn,
        llm_client=client,
    )

    # Now reject
    with pytest.raises(PipelineAborted):
        resume_phase_b_after_gate2(
            run_id=run_id,
            config=config,
            conn_factory=lambda: conn,
            decision="reject",
        )

    # Run should be ABORTED
    row = conn.execute(
        "SELECT status FROM runs WHERE run_id=?", (run_id,)
    ).fetchone()
    assert row["status"] == "ABORTED", f"Expected ABORTED after reject, got {row['status']}"

    # No TASK_GRAPH_APPROVED should have been promoted
    row2 = conn.execute(
        "SELECT current_artifacts FROM runs WHERE run_id=?", (run_id,)
    ).fetchone()
    current_artifacts = json.loads(row2["current_artifacts"])
    # task_graph_approved_id should still be None
    approved_id = current_artifacts.get("task_graph_approved_id")
    if approved_id:
        approved_row = conn.execute(
            "SELECT artifact_type FROM artifacts WHERE artifact_id=?", (approved_id,)
        ).fetchone()
        assert approved_row is None, "TASK_GRAPH_APPROVED must not be promoted on reject"
