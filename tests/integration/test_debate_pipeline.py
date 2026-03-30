import pytest
from ai_dev_system.debate_pipeline import run_debate_pipeline, DebatePipelineResult
from ai_dev_system.debate.llm import StubDebateLLMClient

RAW_IDEA = "Build a simple task manager for small teams."


def test_phase_a_returns_result(conn, config, project_id):
    client = StubDebateLLMClient()

    result = run_debate_pipeline(RAW_IDEA, config, conn, project_id, client)

    assert isinstance(result, DebatePipelineResult)
    assert result.run_id is not None
    assert result.debate_report is not None
    assert result.artifact_id is not None


def test_phase_a_status_paused_at_gate_1(conn, config, project_id):
    client = StubDebateLLMClient()

    result = run_debate_pipeline(RAW_IDEA, config, conn, project_id, client)

    row = conn.execute(
        "SELECT status FROM runs WHERE run_id = %s", (result.run_id,)
    ).fetchone()
    assert row["status"] == "PAUSED_AT_GATE_1"


def test_phase_a_debate_report_artifact_stored(conn, config, project_id):
    client = StubDebateLLMClient()

    result = run_debate_pipeline(RAW_IDEA, config, conn, project_id, client)

    artifact = conn.execute(
        "SELECT artifact_type FROM artifacts WHERE artifact_id = %s", (result.artifact_id,)
    ).fetchone()
    assert artifact["artifact_type"] == "DEBATE_REPORT"
