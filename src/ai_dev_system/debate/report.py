from dataclasses import dataclass, field
from typing import Literal


@dataclass
class Question:
    id: str
    text: str
    classification: Literal["REQUIRED", "STRATEGIC", "OPTIONAL"]
    domain: str
    agent_a: str
    agent_b: str
    source_decision_id: str | None = None


@dataclass
class RoundResult:
    round_number: int
    agent_a_position: str
    agent_b_position: str
    moderator_summary: str
    resolution_status: Literal[
        "RESOLVED", "RESOLVED_WITH_CAVEAT",
        "ESCALATE_TO_HUMAN", "NEED_MORE_EVIDENCE",
        "MODERATOR_PARSE_FAILED",
    ]
    confidence: float
    caveat: str | None
    # M5.F.2 (spec D9): human-readable reason populated by auto_resolve()
    # for OPTIONAL questions so Gate 1 can render the auto-resolved tier
    # with context. None for round results produced by actual debate.
    auto_resolution_reason: str | None = None


@dataclass
class QuestionDebateResult:
    question: Question
    rounds: list[RoundResult]
    final: RoundResult


@dataclass
class DebateReport:
    run_id: str
    brief: dict
    results: list[QuestionDebateResult]
    generated_at: str  # ISO UTC


def _format_auto_resolution_reason(question: Question, decision=None) -> str:
    """Spec D9 reason string.

    With a Decision in hand we surface its safe-default (if any) so
    Gate 1 can show *what* was auto-applied. Without a decision we fall
    back to a generic classification-based reason.
    """
    if decision is None:
        return "OPTIONAL classification — no debate required, no safe default known."
    if getattr(decision, "has_safe_default", False):
        return (
            f"OPTIONAL — using safe default from decision {decision.id} "
            f"({decision.summary})."
        )
    return (
        f"OPTIONAL — decision {decision.id} ({decision.summary}) flagged "
        f"non-blocking, deferred from debate."
    )


def auto_resolve(question: Question, decision=None) -> QuestionDebateResult:
    """Auto-resolve OPTIONAL questions without LLM calls.

    `decision` (optional) is the matched Decision dataclass — when
    supplied, the resulting RoundResult carries an
    `auto_resolution_reason` referencing the decision so Gate 1 can
    render the auto-resolved tier with context (spec D9).
    """
    reason = _format_auto_resolution_reason(question, decision)
    round_result = RoundResult(
        round_number=1,
        agent_a_position="",
        agent_b_position="",
        moderator_summary="Optional question auto-resolved.",
        resolution_status="RESOLVED",
        confidence=1.0,
        caveat=None,
        auto_resolution_reason=reason,
    )
    return QuestionDebateResult(question=question, rounds=[round_result], final=round_result)
