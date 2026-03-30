from datetime import datetime
import pytest
from ai_dev_system.debate.engine import run_debate
from ai_dev_system.debate.report import Question, DebateReport
from ai_dev_system.debate.llm import StubDebateLLMClient

REQUIRED_Q = Question(
    id="Q1", text="Auth?", classification="REQUIRED",
    domain="security", agent_a="SecuritySpecialist", agent_b="BackendArchitect"
)
OPTIONAL_Q = Question(
    id="Q2", text="Color?", classification="OPTIONAL",
    domain="product", agent_a="ProductManager", agent_b="QAEngineer"
)
STRATEGIC_Q = Question(
    id="Q3", text="DB engine?", classification="STRATEGIC",
    domain="database", agent_a="DatabaseSpecialist", agent_b="BackendArchitect"
)


def test_run_debate_returns_debate_report():
    client = StubDebateLLMClient()
    report = run_debate([REQUIRED_Q], client, run_id="r1", brief={})
    assert isinstance(report, DebateReport)
    assert report.run_id == "r1"
    assert len(report.results) == 1


def test_optional_questions_auto_resolved_no_rounds():
    client = StubDebateLLMClient()
    report = run_debate([OPTIONAL_Q], client, run_id="r1", brief={})
    result = report.results[0]
    assert result.final.resolution_status == "RESOLVED"
    assert result.final.confidence == 1.0
    assert result.rounds[0].agent_a_position == ""


def test_stop_on_high_confidence():
    """Stub returns confidence=0.9 → should stop after 1 round."""
    client = StubDebateLLMClient()
    report = run_debate([REQUIRED_Q], client, run_id="r1", brief={})
    result = report.results[0]
    assert len(result.rounds) == 1  # stopped early because confidence >= 0.8


def test_all_three_classifications():
    client = StubDebateLLMClient()
    report = run_debate([REQUIRED_Q, OPTIONAL_Q, STRATEGIC_Q], client, run_id="r1", brief={})
    assert len(report.results) == 3


def test_generated_at_is_iso_utc():
    client = StubDebateLLMClient()
    report = run_debate([REQUIRED_Q], client, run_id="r1", brief={})
    # Should parse without error
    datetime.fromisoformat(report.generated_at.replace("Z", "+00:00"))
