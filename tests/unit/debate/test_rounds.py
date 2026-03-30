import pytest
from ai_dev_system.debate.rounds import run_debate_round
from ai_dev_system.debate.report import Question, RoundResult
from ai_dev_system.debate.llm import StubDebateLLMClient

QUESTION = Question(
    id="Q1", text="Use JWT?", classification="REQUIRED",
    domain="security", agent_a="SecuritySpecialist", agent_b="BackendArchitect"
)


def test_run_debate_round_returns_round_result():
    client = StubDebateLLMClient()
    result = run_debate_round(QUESTION, round_num=1, prev_moderator_summary=None, llm_client=client)
    assert isinstance(result, RoundResult)
    assert result.round_number == 1
    assert result.agent_a_position != ""
    assert result.agent_b_position != ""
    assert result.moderator_summary != ""


def test_run_debate_round_valid_status():
    client = StubDebateLLMClient()
    result = run_debate_round(QUESTION, round_num=1, prev_moderator_summary=None, llm_client=client)
    assert result.resolution_status in (
        "RESOLVED", "RESOLVED_WITH_CAVEAT", "ESCALATE_TO_HUMAN", "NEED_MORE_EVIDENCE"
    )


def test_run_debate_round_confidence_in_range():
    client = StubDebateLLMClient()
    result = run_debate_round(QUESTION, round_num=1, prev_moderator_summary=None, llm_client=client)
    assert 0.0 <= result.confidence <= 1.0


def test_run_debate_round_2_includes_prev_summary():
    client = StubDebateLLMClient()
    result = run_debate_round(QUESTION, round_num=2,
                              prev_moderator_summary="JWT appears stronger.",
                              llm_client=client)
    assert result.round_number == 2
