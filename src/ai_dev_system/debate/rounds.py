# src/ai_dev_system/debate/rounds.py
import json
from ai_dev_system.debate.report import Question, RoundResult
from ai_dev_system.debate.agents import AGENT_PROMPTS, MODERATOR_PROMPT

_AGENT_A_INSTRUCTION = "Đưa ra / điều chỉnh quan điểm của bạn về câu hỏi sau."
_AGENT_B_INSTRUCTION = "Phản biện và đưa ra quan điểm riêng của bạn."


def run_debate_round(
    question: Question,
    round_num: int,
    prev_moderator_summary: str | None,
    llm_client,
) -> RoundResult:
    """Three sequential LLM calls: Agent A → Agent B → Moderator."""
    prev_context = f"\n\nTóm tắt vòng trước: {prev_moderator_summary}" if prev_moderator_summary else ""

    # Call 1: Agent A
    agent_a_user = f"{question.text}{prev_context}\n\n{_AGENT_A_INSTRUCTION}"
    agent_a_position = llm_client.complete(
        system=AGENT_PROMPTS[question.agent_a],
        user=agent_a_user,
    )

    # Call 2: Agent B
    agent_b_user = (
        f"{question.text}\n\nQuan điểm của {question.agent_a}: {agent_a_position}"
        f"{prev_context}\n\n{_AGENT_B_INSTRUCTION}"
    )
    agent_b_position = llm_client.complete(
        system=AGENT_PROMPTS[question.agent_b],
        user=agent_b_user,
    )

    # Call 3: Moderator → JSON
    moderator_user = (
        f"Câu hỏi: {question.text}\n\n"
        f"{question.agent_a}: {agent_a_position}\n\n"
        f"{question.agent_b}: {agent_b_position}"
    )
    moderator_raw = llm_client.complete(system=MODERATOR_PROMPT, user=moderator_user)

    try:
        verdict = json.loads(moderator_raw)
    except json.JSONDecodeError:
        verdict = {
            "status": "NEED_MORE_EVIDENCE",
            "confidence": 0.0,
            "summary": moderator_raw,
            "caveat": "Moderator response was not valid JSON.",
        }

    return RoundResult(
        round_number=round_num,
        agent_a_position=agent_a_position,
        agent_b_position=agent_b_position,
        moderator_summary=verdict.get("summary", ""),
        resolution_status=verdict.get("status", "NEED_MORE_EVIDENCE"),
        confidence=float(verdict.get("confidence", 0.0)),
        caveat=verdict.get("caveat"),
    )
