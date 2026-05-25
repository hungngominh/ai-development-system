from ai_dev_system.debate.questions.models import (
    CoverageCheck,
    CoverageReport,
    Decision,
)


def test_decision_defaults():
    d = Decision(
        id="db_choice",
        summary="Pick a database",
        classification="REQUIRED",
    )
    assert d.domain_hints == []
    assert d.blocks_what == []
    assert d.has_safe_default is False
    assert d.brief_field_refs == []


def test_coverage_report_is_pass_when_no_fail():
    report = CoverageReport(
        checks=[
            CoverageCheck(name="C1_decision_coverage", status="pass"),
            CoverageCheck(name="C2_domain_balance", status="warn"),
            CoverageCheck(name="C3_classification_sanity", status="warn"),
            CoverageCheck(name="C4_question_count", status="pass"),
        ],
        covered_decision_ids=["d1"],
        missing_decision_ids=[],
        domain_distribution={"backend": 1},
        classification_distribution={"REQUIRED": 1},
        total_questions=1,
    )
    assert report.is_pass() is True


def test_coverage_report_is_pass_false_on_fail():
    report = CoverageReport(
        checks=[CoverageCheck(name="C4_question_count", status="fail")],
        covered_decision_ids=[],
        missing_decision_ids=["d1"],
        domain_distribution={},
        classification_distribution={},
        total_questions=0,
    )
    assert report.is_pass() is False
