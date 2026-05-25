"""Data models for the v2 Question Generation pipeline.

`Question` itself lives in `ai_dev_system.debate.report` because debate
rounds consume it; only Phase 1b-internal types are defined here.
"""

from dataclasses import dataclass, field
from typing import Literal

from ai_dev_system.debate.report import Question

Classification = Literal["REQUIRED", "STRATEGIC", "OPTIONAL"]


@dataclass
class Decision:
    """Atomic decision unearthed by the Inventory stage.

    `brief_field_refs` is required to support Gate 1 G8 (brief edit
    re-trigger): when a brief field changes, the materializer re-runs
    only for decisions referencing that field. Legacy inventory
    artifacts without this field fall back to "re-materialize all".
    """

    id: str
    summary: str
    classification: Classification
    domain_hints: list[str] = field(default_factory=list)
    blocks_what: list[str] = field(default_factory=list)
    has_safe_default: bool = False
    brief_field_refs: list[str] = field(default_factory=list)


CoverageCheckName = Literal[
    "C1_decision_coverage",
    "C2_domain_balance",
    "C3_classification_sanity",
    "C4_question_count",
]
CoverageStatus = Literal["pass", "warn", "fail"]


@dataclass
class CoverageCheck:
    name: CoverageCheckName
    status: CoverageStatus
    detail: dict = field(default_factory=dict)


@dataclass
class CoverageReport:
    checks: list[CoverageCheck]
    covered_decision_ids: list[str]
    missing_decision_ids: list[str]
    domain_distribution: dict[str, int]
    classification_distribution: dict[str, int]
    total_questions: int

    def is_pass(self) -> bool:
        """True iff no check is `fail`. Warnings do not block."""
        return all(c.status != "fail" for c in self.checks)


@dataclass
class PipelineResult:
    """Full output of `run_pipeline`."""

    decisions: list[Decision]
    questions_final: list[Question]
    coverage_report: CoverageReport
    critic_iterations: int
