import pytest
from ai_dev_system.debate.agents import MODERATOR_PROMPT_CALIBRATED
from ai_dev_system.debate.questions.models import Decision
from ai_dev_system.debate.rounds import (
    _build_context_block,
    build_agent_a_user,
    build_agent_b_user,
    run_debate_round,
)
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


# ---- M5.E enrichment ----


DECISION = Decision(
    id="D1",
    summary="Choose auth scheme",
    classification="REQUIRED",
    domain_hints=["security"],
    blocks_what=["login flow"],
)


def test_build_context_block_empty_when_no_inputs():
    assert _build_context_block(None, None) == ""


def test_build_context_block_includes_brief_digest():
    block = _build_context_block("App for managing tasks.", None)
    assert "Project context:" in block
    assert "App for managing tasks." in block
    # ends with two newlines so it cleanly precedes question text
    assert block.endswith("\n\n")


def test_build_context_block_includes_decision():
    block = _build_context_block(None, DECISION)
    assert "Decision đang debate (D1)" in block
    assert "Choose auth scheme" in block
    assert "classification=REQUIRED" in block
    assert "login flow" in block


def test_build_context_block_both():
    block = _build_context_block("digest", DECISION)
    assert "Project context:" in block
    assert "Decision đang debate" in block
    # both parts, separated by blank line
    assert "digest" in block.split("\n\n")[0]


def test_build_agent_a_user_v1_unchanged_without_kwargs():
    """With no brief_digest/decision, prompt shape matches v1."""
    out = build_agent_a_user(QUESTION, prev_summary=None)
    assert out.startswith("Use JWT?")
    assert "Project context" not in out
    assert "Decision đang debate" not in out


def test_build_agent_a_user_prepends_context_block():
    out = build_agent_a_user(QUESTION, prev_summary=None,
                             brief_digest="Brief X", decision=DECISION)
    assert out.startswith("Project context:")
    assert "Decision đang debate (D1)" in out
    # original question still in the prompt
    assert "Use JWT?" in out


def test_build_agent_b_user_v1_unchanged():
    out = build_agent_b_user(QUESTION, "A's position", prev_summary=None)
    assert out.startswith("Use JWT?")
    assert "Quan điểm của SecuritySpecialist" in out
    assert "Round 1 cho thấy" not in out  # no skeptic wrap


def test_build_agent_b_user_skeptic_wraps_when_flag_set():
    out = build_agent_b_user(QUESTION, "A's position", prev_summary=None,
                             inject_skeptic=True)
    # skeptic prefix wraps the entire prompt
    assert out.startswith("Round 1 cho thấy")
    assert "SecuritySpecialist" in out  # peer name in the prefix
    # original content still present
    assert "Use JWT?" in out


def test_build_agent_b_user_with_full_enrichment():
    out = build_agent_b_user(QUESTION, "A's position", prev_summary="prev",
                             brief_digest="Brief", decision=DECISION,
                             inject_skeptic=True)
    assert out.startswith("Round 1 cho thấy")
    assert "Project context:" in out
    assert "Decision đang debate (D1)" in out
    assert "Tóm tắt vòng trước: prev" in out


class _CapturingStub:
    """Captures every system+user it sees so we can assert on prompts."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self._stub = StubDebateLLMClient()

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self._stub.complete(system, user)


def test_run_debate_round_passes_calibrated_moderator_prompt():
    client = _CapturingStub()
    run_debate_round(
        QUESTION, round_num=1, prev_moderator_summary=None, llm_client=client,
        moderator_system_prompt=MODERATOR_PROMPT_CALIBRATED,
    )
    # 3 calls: agent A, agent B, moderator
    assert len(client.calls) == 3
    mod_system, _ = client.calls[2]
    assert "CALIBRATION" in mod_system


def test_run_debate_round_enrichment_reaches_agent_prompts():
    client = _CapturingStub()
    run_debate_round(
        QUESTION, round_num=1, prev_moderator_summary=None, llm_client=client,
        brief_digest="my brief digest", decision=DECISION,
    )
    _, a_user = client.calls[0]
    _, b_user = client.calls[1]
    assert "my brief digest" in a_user
    assert "Decision đang debate (D1)" in a_user
    assert "my brief digest" in b_user
