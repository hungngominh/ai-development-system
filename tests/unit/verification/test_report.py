from ai_dev_system.verification.report import (
    CriterionResult, VerificationReport, TaskSummaryEntry
)


def test_criterion_result_construction():
    cr = CriterionResult(
        criterion_id="AC-1",
        criterion_text="User can create tasks",
        verdict="PASS",
        confidence=0.95,
        evidence=["task-1 output: created task OK"],
        reasoning="Task creation confirmed in output",
        related_task_ids=["TASK-1"],
    )
    assert cr.criterion_id == "AC-1"
    assert cr.verdict == "PASS"
    assert cr.confidence == 0.95


def test_task_summary_entry_construction():
    entry = TaskSummaryEntry(
        task_id="TASK-1",
        done_definition_met=True,
        output_artifact_id="some-uuid",
        verification_step_results=["pytest: 5 passed"],
    )
    assert entry.done_definition_met is True
    assert entry.output_artifact_id == "some-uuid"


def test_verification_report_overall_all_pass():
    cr_pass = CriterionResult(
        criterion_id="AC-1", criterion_text="x", verdict="PASS",
        confidence=1.0, evidence=[], reasoning="ok", related_task_ids=[],
    )
    report = VerificationReport(
        run_id="run-1", attempt=1,
        criteria=[cr_pass],
        overall="ALL_PASS",
        task_summary={},
        generated_at="2026-03-31T00:00:00+00:00",
    )
    assert report.overall == "ALL_PASS"
    assert report.attempt == 1


def test_verification_report_overall_has_fail():
    cr_fail = CriterionResult(
        criterion_id="AC-2", criterion_text="y", verdict="FAIL",
        confidence=0.9, evidence=[], reasoning="nope", related_task_ids=[],
    )
    report = VerificationReport(
        run_id="run-1", attempt=2,
        criteria=[cr_fail],
        overall="HAS_FAIL",
        task_summary={},
        generated_at="2026-03-31T00:00:00+00:00",
    )
    assert report.overall == "HAS_FAIL"
