from dataclasses import dataclass, field
from typing import Literal


@dataclass
class TaskSummaryEntry:
    task_id: str
    done_definition_met: bool
    output_artifact_id: str | None
    verification_step_results: list[str] = field(default_factory=list)


@dataclass
class CriterionResult:
    criterion_id: str
    criterion_text: str
    verdict: Literal["PASS", "FAIL", "SKIP"]
    confidence: float                          # 0.0–1.0
    evidence: list[str]                        # task output excerpts used to judge
    reasoning: str                             # LLM explanation
    related_task_ids: list[str] = field(default_factory=list)


@dataclass
class VerificationReport:
    run_id: str
    attempt: int                               # 1-based; counted from VERIFICATION_REPORT artifacts
    criteria: list[CriterionResult]
    overall: Literal["ALL_PASS", "HAS_FAIL"]
    task_summary: dict[str, TaskSummaryEntry]  # task_id → TaskSummaryEntry
    generated_at: str                          # ISO 8601 UTC timestamp
