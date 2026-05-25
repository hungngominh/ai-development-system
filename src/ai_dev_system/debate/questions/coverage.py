"""Stage 4 — Coverage Validator (rule-based, no LLM calls).

Runs four checks (spec M4.4):

- C1 decision_coverage: every non-OPTIONAL decision has at least one
  question. FAIL if any missing; the orchestrator re-triggers
  Stage 2 only for the missing decisions, then re-runs C1 once.
- C2 domain_balance: warn (not fail) when no question covers a
  declared domain hint.
- C3 classification_sanity: WARN when REQUIRED/total < 0.3.
- C4 question_count: FAIL when total < max(5, 0.6 * len(decisions)).

C1 and C4 are blocking; C2 and C3 only warn.
"""

from ai_dev_system.debate.questions.models import (
    CoverageCheck,
    CoverageReport,
    Decision,
)
from ai_dev_system.debate.report import Question

CLASSIFICATION_REQUIRED_MIN_RATIO = 0.3
QUESTION_COUNT_MIN_ABSOLUTE = 5
QUESTION_COUNT_MIN_RATIO = 0.6


class CoverageError(RuntimeError):
    """Raised when a blocking check (C1, C4) cannot be satisfied."""


def run(
    questions: list[Question],
    decisions: list[Decision],
    brief_v2: dict,
) -> CoverageReport:
    """Execute Stage 4. No LLM calls; pure rule-based.

    Args:
        questions: Output of Stage 3.
        decisions: Output of Stage 1.
        brief_v2: Full brief (Stage 4 needs scope_out / domain hints).

    Returns:
        `CoverageReport`. Caller persists as QUESTION_COVERAGE_REPORT
        artifact and emits `COVERAGE_REPORT_GENERATED` event.
    """
    raise NotImplementedError("M4.4 — implement Coverage Validator")


def check_c1_decision_coverage(
    questions: list[Question], decisions: list[Decision]
) -> CoverageCheck:
    raise NotImplementedError("M4.4 — C1")


def check_c2_domain_balance(
    questions: list[Question], decisions: list[Decision]
) -> CoverageCheck:
    raise NotImplementedError("M4.4 — C2")


def check_c3_classification_sanity(questions: list[Question]) -> CoverageCheck:
    raise NotImplementedError("M4.4 — C3")


def check_c4_question_count(
    questions: list[Question], decisions: list[Decision]
) -> CoverageCheck:
    raise NotImplementedError("M4.4 — C4")
