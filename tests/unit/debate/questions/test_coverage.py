"""M4.4 Coverage Validator tests.

Covers: each check (C1-C4) pass/warn/fail cases, edge cases (empty
questions, optional-only missing, zero decisions, boundary counts),
distribution accuracy, report.is_pass semantics. Plus C5_personalization
WARN-only check.
"""

from ai_dev_system.debate.profile import ProjectProfile
from ai_dev_system.debate.questions import coverage
from ai_dev_system.debate.questions.coverage import (
    CLASSIFICATION_REQUIRED_MIN_RATIO,
    QUESTION_COUNT_MIN_ABSOLUTE,
    QUESTION_COUNT_MIN_RATIO,
)
from ai_dev_system.debate.questions.models import Decision
from ai_dev_system.debate.report import Question


# ---- helpers ----


def _decision(
    id_: str,
    *,
    classification: str = "REQUIRED",
    domain_hints: list[str] | None = None,
) -> Decision:
    return Decision(
        id=id_,
        summary=f"Decide {id_}",
        classification=classification,
        domain_hints=domain_hints if domain_hints is not None else ["backend"],
        blocks_what=["voting"] if classification != "OPTIONAL" else [],
        has_safe_default=False,
    )


def _question(
    qid: str,
    *,
    classification: str = "REQUIRED",
    domain: str = "backend",
    source: str | None = None,
) -> Question:
    return Question(
        id=qid,
        text=f"Text for {qid}",
        classification=classification,
        domain=domain,
        agent_a="BackendArchitect",
        agent_b="ProductManager",
        source_decision_id=source,
    )


# ---- C1 decision_coverage ----


def test_c1_passes_when_every_non_optional_decision_has_a_question():
    decisions = [_decision("d1"), _decision("d2"), _decision("d3")]
    questions = [_question(f"Q{i}", source=f"d{i}") for i in (1, 2, 3)]
    check = coverage.check_c1_decision_coverage(questions, decisions)
    assert check.status == "pass"
    assert check.detail["missing_decision_ids"] == []
    assert check.detail["non_optional_count"] == 3


def test_c1_fails_when_required_decision_missing():
    decisions = [_decision("d1"), _decision("d2"), _decision("d3")]
    questions = [_question("Q1", source="d1"), _question("Q3", source="d3")]
    check = coverage.check_c1_decision_coverage(questions, decisions)
    assert check.status == "fail"
    assert check.detail["missing_decision_ids"] == ["d2"]


def test_c1_ignores_missing_optional_decisions():
    decisions = [
        _decision("d1"),
        _decision("d_opt", classification="OPTIONAL"),
    ]
    questions = [_question("Q1", source="d1")]
    check = coverage.check_c1_decision_coverage(questions, decisions)
    assert check.status == "pass"


def test_c1_trivially_passes_with_zero_decisions():
    check = coverage.check_c1_decision_coverage([], [])
    assert check.status == "pass"
    assert check.detail["non_optional_count"] == 0


# ---- C2 domain_balance ----


def test_c2_passes_when_all_declared_domains_covered():
    decisions = [
        _decision("d1", domain_hints=["backend", "security"]),
        _decision("d2", domain_hints=["frontend"]),
    ]
    questions = [
        _question("Q1", domain="backend"),
        _question("Q2", domain="security"),
        _question("Q3", domain="frontend"),
    ]
    check = coverage.check_c2_domain_balance(questions, decisions)
    assert check.status == "pass"
    assert check.detail["missing_domains"] == []


def test_c2_warns_when_a_domain_is_missing():
    decisions = [_decision("d1", domain_hints=["backend", "security", "ml"])]
    questions = [_question("Q1", domain="backend"), _question("Q2", domain="security")]
    check = coverage.check_c2_domain_balance(questions, decisions)
    assert check.status == "warn"
    assert check.detail["missing_domains"] == ["ml"]


def test_c2_passes_when_no_domain_hints_declared():
    decisions = [_decision("d1", domain_hints=[])]
    questions = [_question("Q1", domain="backend")]
    check = coverage.check_c2_domain_balance(questions, decisions)
    assert check.status == "pass"


# ---- C3 classification_sanity ----


def test_c3_passes_when_required_ratio_meets_threshold():
    questions = [
        _question("Q1", classification="REQUIRED"),
        _question("Q2", classification="REQUIRED"),
        _question("Q3", classification="STRATEGIC"),
        _question("Q4", classification="OPTIONAL"),
    ]
    check = coverage.check_c3_classification_sanity(questions)
    assert check.status == "pass"
    assert check.detail["ratio"] == 0.5


def test_c3_warns_when_required_ratio_below_threshold():
    questions = [
        _question("Q1", classification="REQUIRED"),
        *(_question(f"Q{i}", classification="OPTIONAL") for i in range(2, 11)),
    ]
    check = coverage.check_c3_classification_sanity(questions)
    assert check.status == "warn"
    assert check.detail["ratio"] == 0.1
    assert check.detail["threshold"] == CLASSIFICATION_REQUIRED_MIN_RATIO


def test_c3_warns_on_empty_questions():
    check = coverage.check_c3_classification_sanity([])
    assert check.status == "warn"
    assert check.detail["total"] == 0
    assert check.detail["ratio"] == 0.0


def test_c3_boundary_pass_at_exactly_threshold():
    # ratio = 3/10 = 0.3 -- pass at boundary
    questions = [_question(f"Q{i}", classification="REQUIRED") for i in range(3)]
    questions += [_question(f"Q{i + 3}", classification="OPTIONAL") for i in range(7)]
    check = coverage.check_c3_classification_sanity(questions)
    assert check.status == "pass"
    assert check.detail["ratio"] == 0.3


# ---- C4 question_count ----


def test_c4_passes_when_count_meets_minimum_absolute():
    decisions = [_decision(f"d{i}") for i in range(3)]
    # ceil(0.6 * 3) = 2; max(5, 2) = 5 → need 5
    questions = [_question(f"Q{i}") for i in range(5)]
    check = coverage.check_c4_question_count(questions, decisions)
    assert check.status == "pass"
    assert check.detail["threshold"] == 5


def test_c4_fails_when_below_minimum_absolute():
    decisions = [_decision(f"d{i}") for i in range(3)]
    questions = [_question(f"Q{i}") for i in range(4)]
    check = coverage.check_c4_question_count(questions, decisions)
    assert check.status == "fail"
    assert check.detail["total"] == 4
    assert check.detail["threshold"] == 5


def test_c4_threshold_scales_with_decision_count():
    decisions = [_decision(f"d{i}") for i in range(10)]
    # ceil(0.6 * 10) = 6; max(5, 6) = 6
    questions_5 = [_question(f"Q{i}") for i in range(5)]
    check_low = coverage.check_c4_question_count(questions_5, decisions)
    assert check_low.status == "fail"
    assert check_low.detail["threshold"] == 6

    questions_6 = [_question(f"Q{i}") for i in range(6)]
    check_ok = coverage.check_c4_question_count(questions_6, decisions)
    assert check_ok.status == "pass"


def test_c4_uses_ceil_not_floor_on_ratio():
    # len(decisions) = 9 → 0.6 * 9 = 5.4 → ceil = 6
    decisions = [_decision(f"d{i}") for i in range(9)]
    assert QUESTION_COUNT_MIN_ABSOLUTE == 5
    assert QUESTION_COUNT_MIN_RATIO == 0.6
    questions = [_question(f"Q{i}") for i in range(5)]
    check = coverage.check_c4_question_count(questions, decisions)
    # 5 < ceil(5.4)=6 → fail
    assert check.status == "fail"
    assert check.detail["threshold"] == 6


# ---- run() integration ----


def _make_aligned_set(n_decisions: int = 6, n_required: int = 5):
    decisions = [_decision(f"d{i}") for i in range(n_decisions)]
    questions = []
    for i in range(n_required):
        questions.append(_question(f"Q{i}", classification="REQUIRED", source=f"d{i}"))
    return decisions, questions


def test_run_returns_report_with_all_five_checks():
    decisions, questions = _make_aligned_set(6, 5)
    report = coverage.run(questions, decisions, brief_v2={})
    assert [c.name for c in report.checks] == [
        "C1_decision_coverage",
        "C2_domain_balance",
        "C3_classification_sanity",
        "C4_question_count",
        "C5_personalization",
    ]


def test_run_is_pass_when_no_check_fails():
    decisions, questions = _make_aligned_set(5, 5)
    report = coverage.run(questions, decisions, brief_v2={})
    assert report.is_pass() is True


def test_run_is_pass_false_when_c1_fails():
    decisions = [_decision(f"d{i}") for i in range(5)]
    # missing decision d4
    questions = [
        _question(f"Q{i}", source=f"d{i}", classification="REQUIRED")
        for i in range(4)
    ]
    # add one more so C4 doesn't also fail (we want isolated C1 fail)
    questions.append(_question("Q-extra", source="d0", classification="REQUIRED"))
    report = coverage.run(questions, decisions, brief_v2={})
    c1 = next(c for c in report.checks if c.name == "C1_decision_coverage")
    assert c1.status == "fail"
    assert report.is_pass() is False
    assert "d4" in report.missing_decision_ids


def test_run_is_pass_false_when_c4_fails():
    decisions = [_decision(f"d{i}") for i in range(5)]
    questions = [
        _question(f"Q{i}", source=f"d{i}", classification="REQUIRED")
        for i in range(3)
    ]
    report = coverage.run(questions, decisions, brief_v2={})
    c4 = next(c for c in report.checks if c.name == "C4_question_count")
    assert c4.status == "fail"
    assert report.is_pass() is False


def test_run_is_pass_true_when_only_c2_or_c3_warn():
    # c2 warns (missing domain), c3 warns (low REQUIRED ratio), nothing fails
    decisions = [
        _decision("d1", domain_hints=["backend", "ml"]),
        _decision("d2", domain_hints=["backend"]),
        _decision("d3", domain_hints=["backend"]),
    ]
    questions = [
        _question("Q1", source="d1", classification="REQUIRED", domain="backend"),
        _question("Q2", source="d2", classification="OPTIONAL", domain="backend"),
        _question("Q3", source="d3", classification="OPTIONAL", domain="backend"),
        _question("Q4", source="d1", classification="OPTIONAL", domain="backend"),
        _question("Q5", source="d2", classification="OPTIONAL", domain="backend"),
    ]
    report = coverage.run(questions, decisions, brief_v2={})
    statuses = {c.name: c.status for c in report.checks}
    assert statuses["C1_decision_coverage"] == "pass"
    assert statuses["C2_domain_balance"] == "warn"
    assert statuses["C3_classification_sanity"] == "warn"
    assert statuses["C4_question_count"] == "pass"
    assert report.is_pass() is True


# ---- distributions ----


def test_run_builds_domain_distribution():
    decisions = [_decision(f"d{i}") for i in range(5)]
    questions = [
        _question("Q1", source="d0", domain="backend"),
        _question("Q2", source="d1", domain="backend"),
        _question("Q3", source="d2", domain="security"),
        _question("Q4", source="d3", domain="frontend"),
        _question("Q5", source="d4", domain="security"),
    ]
    report = coverage.run(questions, decisions, brief_v2={})
    assert report.domain_distribution == {"backend": 2, "security": 2, "frontend": 1}


def test_run_builds_classification_distribution():
    decisions = [_decision(f"d{i}") for i in range(5)]
    questions = [
        _question("Q1", source="d0", classification="REQUIRED"),
        _question("Q2", source="d1", classification="REQUIRED"),
        _question("Q3", source="d2", classification="STRATEGIC"),
        _question("Q4", source="d3", classification="OPTIONAL"),
        _question("Q5", source="d4", classification="OPTIONAL"),
    ]
    report = coverage.run(questions, decisions, brief_v2={})
    assert report.classification_distribution == {
        "REQUIRED": 2,
        "STRATEGIC": 1,
        "OPTIONAL": 2,
    }


def test_run_lists_covered_decision_ids_sorted_unique():
    decisions = [_decision(f"d{i}") for i in range(5)]
    questions = [
        _question("Q1", source="d2", classification="REQUIRED"),
        _question("Q2", source="d0", classification="REQUIRED"),
        _question("Q3", source="d2", classification="STRATEGIC"),  # dup source
        _question("Q4", source="d4", classification="OPTIONAL"),
        _question("Q5", source="d1", classification="REQUIRED"),
    ]
    report = coverage.run(questions, decisions, brief_v2={})
    assert report.covered_decision_ids == ["d0", "d1", "d2", "d4"]


def test_run_total_questions_matches_input():
    decisions = [_decision(f"d{i}") for i in range(5)]
    questions = [_question(f"Q{i}", source=f"d{i}") for i in range(5)]
    report = coverage.run(questions, decisions, brief_v2={})
    assert report.total_questions == 5


# ---- C5 personalization ----


def _q(domain):
    return Question(id="Q", text="t", classification="REQUIRED", domain=domain,
                    agent_a="ProductManager", agent_b="BackendArchitect",
                    source_decision_id="d1")


def _c5(report):
    return next(c for c in report.checks if c.name == "C5_personalization")


def _make_c5_decisions_and_questions(domain: str):
    """Return (decisions, questions) satisfying C1 and C4 with all questions
    in the given domain — isolates C5 from C4/C1 noise."""
    decisions = [Decision(id=f"d{i}", summary="s", classification="REQUIRED")
                 for i in range(5)]
    questions = [
        Question(id=f"Q{i}", text="t", classification="REQUIRED", domain=domain,
                 agent_a="ProductManager", agent_b="BackendArchitect",
                 source_decision_id=f"d{i}")
        for i in range(5)
    ]
    return decisions, questions


def test_c5_warns_when_profile_set_but_no_product_questions():
    profile = ProjectProfile("couples app", [], ["couple psychology"], [])
    decisions, questions = _make_c5_decisions_and_questions("backend")
    report = coverage.run(questions, decisions, {}, profile=profile)
    assert _c5(report).status == "warn"
    assert report.is_pass() is True  # WARN never blocks


def test_c5_passes_when_product_question_present():
    profile = ProjectProfile("couples app", [], ["couple psychology"], [])
    decisions, questions = _make_c5_decisions_and_questions("psychology")
    report = coverage.run(questions, decisions, {}, profile=profile)
    assert _c5(report).status == "pass"


def test_c5_passes_when_profile_empty():
    decisions, questions = _make_c5_decisions_and_questions("backend")
    report = coverage.run(questions, decisions, {}, profile=ProjectProfile.empty())
    assert _c5(report).status == "pass"
