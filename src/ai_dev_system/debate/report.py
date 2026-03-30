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


@dataclass
class RoundResult:
    round_number: int
    agent_a_position: str
    agent_b_position: str
    moderator_summary: str
    resolution_status: Literal[
        "RESOLVED", "RESOLVED_WITH_CAVEAT",
        "ESCALATE_TO_HUMAN", "NEED_MORE_EVIDENCE"
    ]
    confidence: float
    caveat: str | None


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


def auto_resolve(question: Question) -> QuestionDebateResult:
    """Auto-resolve OPTIONAL questions without LLM calls."""
    round_result = RoundResult(
        round_number=1,
        agent_a_position="",
        agent_b_position="",
        moderator_summary="Optional question auto-resolved.",
        resolution_status="RESOLVED",
        confidence=1.0,
        caveat=None,
    )
    return QuestionDebateResult(question=question, rounds=[round_result], final=round_result)
