"""Stage 4 — Coverage Validator (rule-based, no LLM calls).

Runs four checks (spec M4.4):

- C1 decision_coverage: every non-OPTIONAL decision must have at least
  one question pointing at it via `question.source_decision_id`.
  FAIL when any non-OPTIONAL decision is uncovered. The pipeline
  orchestrator (M4.5) re-triggers Stage 2 for the missing decisions
  and re-runs coverage once.
- C2 domain_balance: every domain declared in any
  `decision.domain_hints` should appear in at least one question's
  `domain`. WARN-only.
- C3 classification_sanity: REQUIRED / total >= 0.3. WARN-only.
  When total questions == 0, returns warn with ratio 0.0.
- C4 question_count: total >= max(MIN_ABSOLUTE, ceil(MIN_RATIO * len(decisions))).
  FAIL otherwise.

`run()` is pure — it returns a `CoverageReport` and never raises for
domain failures. The caller (pipeline.py) decides whether to abort,
retry, or proceed based on the report and on whether each check is
listed as blocking.
"""

import math
from collections import Counter

from ai_dev_system.debate.profile import PRODUCT_BEHAVIORAL_DOMAINS
from ai_dev_system.debate.questions.models import (
    CoverageCheck,
    CoverageReport,
    Decision,
)
from ai_dev_system.debate.report import Question

CLASSIFICATION_REQUIRED_MIN_RATIO = 0.3
QUESTION_COUNT_MIN_ABSOLUTE = 5
QUESTION_COUNT_MIN_RATIO = 0.6

BLOCKING_CHECKS = ("C1_decision_coverage", "C4_question_count")


class CoverageError(RuntimeError):
    """Raised by pipeline.py when a blocking check (C1, C4) cannot be
    satisfied. Not raised by this module — coverage is pure reporting.
    """


def check_c1_decision_coverage(
    questions: list[Question], decisions: list[Decision]
) -> CoverageCheck:
    non_optional_ids = {d.id for d in decisions if d.classification != "OPTIONAL"}
    covered = {q.source_decision_id for q in questions if q.source_decision_id}
    missing = sorted(non_optional_ids - covered)
    return CoverageCheck(
        name="C1_decision_coverage",
        status="fail" if missing else "pass",
        detail={
            "missing_decision_ids": missing,
            "covered_count": len(non_optional_ids - set(missing)),
            "non_optional_count": len(non_optional_ids),
        },
    )


def check_c2_domain_balance(
    questions: list[Question], decisions: list[Decision]
) -> CoverageCheck:
    declared: set[str] = set()
    for d in decisions:
        for hint in d.domain_hints:
            declared.add(hint)
    covered = {q.domain for q in questions}
    missing = sorted(declared - covered)
    return CoverageCheck(
        name="C2_domain_balance",
        status="warn" if missing else "pass",
        detail={
            "declared_domains": sorted(declared),
            "covered_domains": sorted(declared & covered),
            "missing_domains": missing,
        },
    )


def check_c3_classification_sanity(questions: list[Question]) -> CoverageCheck:
    total = len(questions)
    required = sum(1 for q in questions if q.classification == "REQUIRED")
    ratio = (required / total) if total else 0.0
    status = "pass" if ratio >= CLASSIFICATION_REQUIRED_MIN_RATIO else "warn"
    return CoverageCheck(
        name="C3_classification_sanity",
        status=status,
        detail={
            "required_count": required,
            "total": total,
            "ratio": round(ratio, 4),
            "threshold": CLASSIFICATION_REQUIRED_MIN_RATIO,
        },
    )


def check_c4_question_count(
    questions: list[Question], decisions: list[Decision]
) -> CoverageCheck:
    total = len(questions)
    threshold = max(
        QUESTION_COUNT_MIN_ABSOLUTE,
        math.ceil(QUESTION_COUNT_MIN_RATIO * len(decisions)),
    )
    return CoverageCheck(
        name="C4_question_count",
        status="pass" if total >= threshold else "fail",
        detail={
            "total": total,
            "threshold": threshold,
            "decision_count": len(decisions),
        },
    )


def check_c5_personalization(questions: list[Question], profile) -> CoverageCheck:
    """WARN when a vertical profile is present but no question lands in a
    product/behavioral domain — personalization was likely dropped."""
    if profile is None or profile.is_empty():
        return CoverageCheck(name="C5_personalization", status="pass",
                             detail={"reason": "no profile"})
    product = [q for q in questions if q.domain in PRODUCT_BEHAVIORAL_DOMAINS]
    return CoverageCheck(
        name="C5_personalization",
        status="pass" if product else "warn",
        detail={"product_question_count": len(product), "total": len(questions)},
    )


def _domain_distribution(questions: list[Question]) -> dict[str, int]:
    return dict(Counter(q.domain for q in questions))


def _classification_distribution(questions: list[Question]) -> dict[str, int]:
    return dict(Counter(q.classification for q in questions))


def run(
    questions: list[Question],
    decisions: list[Decision],
    brief_v2: dict,
    profile=None,
) -> CoverageReport:
    """Execute Stage 4. No LLM calls; pure rule-based.

    `brief_v2` is currently unused by any check (the C2 declared-domain
    set is derived from decisions, not brief). The parameter is kept
    so future checks (e.g. scope_out vs question.domain) can land
    without a signature change.

    `profile`: optional `ProjectProfile` for C5 personalization check.
    """
    _ = brief_v2  # reserved for future checks

    checks = [
        check_c1_decision_coverage(questions, decisions),
        check_c2_domain_balance(questions, decisions),
        check_c3_classification_sanity(questions),
        check_c4_question_count(questions, decisions),
        check_c5_personalization(questions, profile),
    ]

    c1 = checks[0]
    return CoverageReport(
        checks=checks,
        covered_decision_ids=sorted(
            {q.source_decision_id for q in questions if q.source_decision_id}
        ),
        missing_decision_ids=list(c1.detail.get("missing_decision_ids", [])),
        domain_distribution=_domain_distribution(questions),
        classification_distribution=_classification_distribution(questions),
        total_questions=len(questions),
    )
