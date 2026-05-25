# src/ai_dev_system/debate/rounds.py
"""Debate round orchestrator.

v1: three sequential calls (Agent A → Agent B → Moderator) with bare
question text in the user prompts.

M5.E adds optional enrichment hooks — when the caller (engine) passes
`brief_digest`, `decision`, `inject_skeptic`, and/or a custom
`moderator_system_prompt`, the round prompts get the spec D6 context
shell and the spec D4 skeptic prefix wrap. With no kwargs, behaviour
is bit-identical to v1.
"""

from ai_dev_system.debate.agents import AGENT_PROMPTS, MODERATOR_PROMPT
from ai_dev_system.debate.diversity import build_skeptic_round_user
from ai_dev_system.debate.moderator import MAX_MODERATOR_RETRIES, run_moderator
from ai_dev_system.debate.questions.models import Decision
from ai_dev_system.debate.report import Question, RoundResult

_AGENT_A_INSTRUCTION = "Đưa ra / điều chỉnh quan điểm của bạn về câu hỏi sau."
_AGENT_B_INSTRUCTION = "Phản biện và đưa ra quan điểm riêng của bạn."


def _build_context_block(brief_digest: str | None, decision: Decision | None) -> str:
    """Spec D6 round-prompt enrichment.

    Returns a leading block ("Project context: ... / Decision đang
    debate: ...") that goes ABOVE the question text. Empty string
    when neither brief_digest nor decision is supplied so v1 callers
    see no change in prompt shape.
    """
    parts: list[str] = []
    if brief_digest:
        parts.append(f"Project context:\n{brief_digest}")
    if decision is not None:
        parts.append(
            f"Decision đang debate ({decision.id}): {decision.summary}\n"
            f"Vì sao quan trọng: classification={decision.classification}, "
            f"blocks={decision.blocks_what or 'unspecified'}"
        )
    if not parts:
        return ""
    return "\n\n".join(parts) + "\n\n"


def build_agent_a_user(
    question: Question,
    prev_summary: str | None,
    *,
    brief_digest: str | None = None,
    decision: Decision | None = None,
) -> str:
    prev_context = (
        f"\n\nTóm tắt vòng trước: {prev_summary}" if prev_summary else ""
    )
    context_block = _build_context_block(brief_digest, decision)
    return (
        f"{context_block}{question.text}{prev_context}\n\n{_AGENT_A_INSTRUCTION}"
    )


def build_agent_b_user(
    question: Question,
    agent_a_position: str,
    prev_summary: str | None,
    *,
    brief_digest: str | None = None,
    decision: Decision | None = None,
    inject_skeptic: bool = False,
) -> str:
    prev_context = (
        f"\n\nTóm tắt vòng trước: {prev_summary}" if prev_summary else ""
    )
    context_block = _build_context_block(brief_digest, decision)
    base = (
        f"{context_block}{question.text}\n\n"
        f"Quan điểm của {question.agent_a}: {agent_a_position}"
        f"{prev_context}\n\n{_AGENT_B_INSTRUCTION}"
    )
    if inject_skeptic:
        return build_skeptic_round_user(base, question.agent_a)
    return base


def run_debate_round(
    question: Question,
    round_num: int,
    prev_moderator_summary: str | None,
    llm_client,
    *,
    brief_digest: str | None = None,
    decision: Decision | None = None,
    inject_skeptic: bool = False,
    moderator_system_prompt: str = MODERATOR_PROMPT,
    max_moderator_retries: int = MAX_MODERATOR_RETRIES,
) -> RoundResult:
    """Three sequential LLM calls: Agent A → Agent B → Moderator.

    Optional kwargs (all default to v1 behaviour):
        brief_digest, decision      — D6 round-prompt enrichment.
        inject_skeptic              — D4 echo recovery: wraps the
                                       Agent B user prompt with the
                                       steel-man instruction.
        moderator_system_prompt     — engine swaps in
                                       MODERATOR_PROMPT_CALIBRATED
                                       when DebateConfig requests.
        max_moderator_retries       — overrides moderator.MAX_MODERATOR_RETRIES
                                       per-call (rarely needed; usually
                                       set via DebateConfig).

    Moderator JSON parsing + retry is delegated to
    `debate.moderator.run_moderator` (M5.C). Unparseable responses
    surface as `resolution_status="MODERATOR_PARSE_FAILED"`.
    """
    agent_a_user = build_agent_a_user(
        question, prev_moderator_summary,
        brief_digest=brief_digest, decision=decision,
    )
    agent_a_position = llm_client.complete(
        system=AGENT_PROMPTS[question.agent_a],
        user=agent_a_user,
    )

    agent_b_user = build_agent_b_user(
        question, agent_a_position, prev_moderator_summary,
        brief_digest=brief_digest, decision=decision,
        inject_skeptic=inject_skeptic,
    )
    agent_b_position = llm_client.complete(
        system=AGENT_PROMPTS[question.agent_b],
        user=agent_b_user,
    )

    moderator_user = (
        f"Câu hỏi: {question.text}\n\n"
        f"{question.agent_a}: {agent_a_position}\n\n"
        f"{question.agent_b}: {agent_b_position}"
    )
    return run_moderator(
        llm_client,
        system_prompt=moderator_system_prompt,
        user_context=moderator_user,
        round_number=round_num,
        agent_a_position=agent_a_position,
        agent_b_position=agent_b_position,
        max_retries=max_moderator_retries,
    )
