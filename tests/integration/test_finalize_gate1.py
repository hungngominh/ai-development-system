import pytest
from ai_dev_system.debate_pipeline import run_debate_pipeline
from ai_dev_system.debate.llm import StubDebateLLMClient
from ai_dev_system.gate.gate1_bridge import finalize_gate1, Decision

RAW_IDEA = "Build a simple task manager for small teams."


def test_finalize_gate1_returns_artifact_ids(conn, config, project_id):
    client = StubDebateLLMClient()
    result = run_debate_pipeline(RAW_IDEA, config, conn, project_id, client)

    decisions = [
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

    aa_id, dl_id = finalize_gate1(
        result.run_id, decisions, config.storage_root, conn
    )

    assert aa_id is not None
    assert dl_id is not None
    assert aa_id != dl_id


def test_finalize_gate1_run_status_updated(conn, config, project_id):
    client = StubDebateLLMClient()
    result = run_debate_pipeline(RAW_IDEA, config, conn, project_id, client)

    decisions = [
        Decision(
            question_id="Q1",
            question_text="Who are the primary users?",
            classification="stakeholder",
            resolution_type="CONSENSUS",
            answer="Small team leads",
        ),
    ]

    finalize_gate1(result.run_id, decisions, config.storage_root, conn)

    row = conn.execute(
        "SELECT status FROM runs WHERE run_id = %s", (result.run_id,)
    ).fetchone()
    assert row["status"] == "RUNNING_PHASE_1D"


def test_finalize_gate1_artifacts_stored_in_db(conn, config, project_id):
    client = StubDebateLLMClient()
    result = run_debate_pipeline(RAW_IDEA, config, conn, project_id, client)

    decisions = [
        Decision(
            question_id="Q1",
            question_text="Who are the primary users?",
            classification="stakeholder",
            resolution_type="CONSENSUS",
            answer="Small team leads",
        ),
    ]

    aa_id, dl_id = finalize_gate1(
        result.run_id, decisions, config.storage_root, conn
    )

    aa_row = conn.execute(
        "SELECT artifact_type FROM artifacts WHERE artifact_id = %s", (aa_id,)
    ).fetchone()
    assert aa_row["artifact_type"] == "APPROVED_ANSWERS"

    dl_row = conn.execute(
        "SELECT artifact_type FROM artifacts WHERE artifact_id = %s", (dl_id,)
    ).fetchone()
    assert dl_row["artifact_type"] == "DECISION_LOG"
