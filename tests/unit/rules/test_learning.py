"""Unit tests for the failure-learning loop (rules/learning.py)."""
from __future__ import annotations

import pytest
import yaml

from ai_dev_system.rules.learning import (
    LearnedRule,
    learn_from_failure,
    lessons_from_verification,
    lesson_from_rejection,
    scope_task_from_context,
    _is_transient_lesson,
)
from ai_dev_system.rules.registry import RuleRegistry
from ai_dev_system.verification.report import CriterionResult, VerificationReport


def _report(overall: str, criteria: list[CriterionResult]) -> VerificationReport:
    return VerificationReport(
        run_id="run-1",
        attempt=1,
        criteria=criteria,
        overall=overall,
        task_summary={},
        generated_at="2026-06-27T00:00:00+00:00",
    )


def _fail_criterion(reasoning: str = "auth bypass: missing token check") -> CriterionResult:
    return CriterionResult(
        criterion_id="C1",
        criterion_text="Endpoint must reject unauthenticated requests",
        verdict="FAIL",
        confidence=0.9,
        evidence=["handler returned 200 with no token"],
        reasoning=reasoning,
    )


# ── lesson extraction ───────────────────────────────────────────────────────

def test_lessons_from_verification_mines_fail_criteria():
    report = _report("HAS_FAIL", [_fail_criterion()])
    lessons = lessons_from_verification(report)
    assert len(lessons) == 1
    assert "auth bypass" in lessons[0]


def test_lessons_from_verification_all_pass_is_empty():
    report = _report("ALL_PASS", [])
    assert lessons_from_verification(report) == []


def test_lessons_from_verification_skips_passing_criteria():
    passing = CriterionResult("C2", "ok", "PASS", 1.0, [], "looks good")
    report = _report("HAS_FAIL", [passing, _fail_criterion()])
    lessons = lessons_from_verification(report)
    assert len(lessons) == 1


def test_lesson_from_rejection():
    assert lesson_from_rejection("wrong endpoint path") == [
        "Reviewer rejected prior output: wrong endpoint path"
    ]
    assert lesson_from_rejection("") == []


# ── core behaviour (test_cases #1) ───────────────────────────────────────────

def test_verification_fail_produces_matching_rule(tmp_path):
    task = {"task_run_id": "tr-1", "task_type": "code", "tags": ["auth"]}
    report = _report("HAS_FAIL", [_fail_criterion()])

    result = learn_from_failure(
        conn=None, run_id="run-1", task=task,
        rules_dir=tmp_path, source="verification", report=report,
    )

    assert isinstance(result, LearnedRule)
    assert result.created is True
    assert result.deduped is False
    assert any("auth bypass" in r for r in result.file_rules)
    # applies_to matches the failed task
    assert "code" in result.applies_to["task_types"]
    assert "auth" in result.applies_to["tags"]

    # The rule file actually exists and round-trips through RuleRegistry.
    match = RuleRegistry(tmp_path).match_rules(task)
    assert any("auth bypass" in r for r in match.file_rules)


# ── idempotency (test_cases #2) ──────────────────────────────────────────────

def test_idempotent_same_failure_does_not_duplicate(tmp_path):
    task = {"task_run_id": "tr-1", "task_type": "code", "tags": ["auth"]}
    report = _report("HAS_FAIL", [_fail_criterion()])

    first = learn_from_failure(
        conn=None, run_id="run-1", task=task,
        rules_dir=tmp_path, source="verification", report=report,
    )
    second = learn_from_failure(
        conn=None, run_id="run-1", task=task,
        rules_dir=tmp_path, source="verification", report=report,
    )

    assert first.created is True
    assert second.created is False
    assert second.deduped is True
    # No duplicate file_rules entry.
    assert len(second.file_rules) == len(first.file_rules) == 1


def test_new_lesson_appends_without_duplicating(tmp_path):
    task = {"task_run_id": "tr-1", "task_type": "code", "tags": ["auth"]}
    learn_from_failure(
        conn=None, run_id="run-1", task=task, rules_dir=tmp_path,
        source="verification", report=_report("HAS_FAIL", [_fail_criterion("first bug")]),
    )
    result = learn_from_failure(
        conn=None, run_id="run-1", task=task, rules_dir=tmp_path,
        source="verification", report=_report("HAS_FAIL", [_fail_criterion("second bug")]),
    )
    assert result.deduped is False
    assert len(result.file_rules) == 2


# ── transient vs durable (test_cases #3) ─────────────────────────────────────

def test_transient_execution_error_does_not_mint(tmp_path):
    task = {"task_run_id": "tr-1", "task_type": "code", "tags": ["auth"]}
    result = learn_from_failure(
        conn=None, run_id="run-1", task=task, rules_dir=tmp_path,
        source="error", error_type="EXECUTION_ERROR",
    )
    assert result is None
    assert list(tmp_path.glob("*.yaml")) == []


def test_execution_error_type_blocks_even_verification_source(tmp_path):
    task = {"task_run_id": "tr-1", "task_type": "code", "tags": ["auth"]}
    result = learn_from_failure(
        conn=None, run_id="run-1", task=task, rules_dir=tmp_path,
        source="verification", report=_report("HAS_FAIL", [_fail_criterion()]),
        error_type="EXECUTION_ERROR",
    )
    assert result is None


def test_gate_rejection_mints_rule(tmp_path):
    task = {"task_run_id": "tr-1", "task_type": "code", "tags": ["auth"]}
    result = learn_from_failure(
        conn=None, run_id="run-1", task=task, rules_dir=tmp_path,
        source="gate", rejection_reason="output ignores rate limiting",
    )
    assert result is not None
    assert any("rate limiting" in r for r in result.file_rules)


# ── loop closes (test_cases #4) ──────────────────────────────────────────────

def test_loop_closes_future_sibling_task_inherits_lesson(tmp_path):
    failed = {"task_run_id": "tr-1", "task_type": "code", "tags": ["auth"]}
    learn_from_failure(
        conn=None, run_id="run-1", task=failed, rules_dir=tmp_path,
        source="verification", report=_report("HAS_FAIL", [_fail_criterion()]),
    )
    # A *different* task of the same type, freshly loaded registry.
    sibling = {"task_type": "code", "tags": []}
    match = RuleRegistry(tmp_path).match_rules(sibling)
    assert any("auth bypass" in r for r in match.file_rules)


# ── validation / over-fitting guards ─────────────────────────────────────────

def test_unscopable_task_is_skipped(tmp_path):
    task = {"task_run_id": "tr-1", "task_type": "", "tags": []}
    result = learn_from_failure(
        conn=None, run_id="run-1", task=task, rules_dir=tmp_path,
        source="verification", report=_report("HAS_FAIL", [_fail_criterion()]),
    )
    assert result is None
    assert list(tmp_path.glob("*.yaml")) == []


def test_empty_lesson_is_skipped(tmp_path):
    task = {"task_run_id": "tr-1", "task_type": "code", "tags": ["auth"]}
    # HAS_FAIL but every criterion has empty reasoning + text → no lesson.
    blank = CriterionResult("C9", "", "FAIL", 0.5, [], "")
    result = learn_from_failure(
        conn=None, run_id="run-1", task=task, rules_dir=tmp_path,
        source="verification", report=_report("HAS_FAIL", [blank]),
    )
    assert result is None


# ── scope adapter (engine task_runs row → scope task) ────────────────────────

def test_scope_task_from_context_reads_type_and_tags():
    # context_snapshot uses key "type" (not "task_type") and a "tags" list.
    ctx = {"type": "code", "tags": ["auth", "api"], "phase": "implementation"}
    scope = scope_task_from_context({"task_run_id": "tr-9"}, ctx)
    assert scope["task_run_id"] == "tr-9"
    assert scope["task_type"] == "code"
    assert scope["tags"] == ["auth", "api"]


def test_scope_task_from_context_handles_missing_keys():
    scope = scope_task_from_context({}, {})
    assert scope == {"task_run_id": None, "task_type": "", "tags": []}


# ── #6: corrupt/hand-edited applies_to must not crash ─────────────────────────

def test_existing_rule_with_null_applies_to_does_not_crash(tmp_path):
    task = {"task_run_id": "tr-1", "task_type": "code", "tags": ["auth"]}
    # Simulate a hand-edited learned file whose applies_to was blanked to null.
    rule_path = tmp_path / "learned-code.yaml"
    rule_path.write_text(
        "name: learned-code\napplies_to: null\nfile_rules: []\nskill_rules: []\n",
        encoding="utf-8",
    )
    # Must coerce applies_to to a dict instead of raising TypeError.
    result = learn_from_failure(
        conn=None, run_id="run-1", task=task, rules_dir=tmp_path,
        source="verification", report=_report("HAS_FAIL", [_fail_criterion()]),
    )
    assert result is not None
    assert "code" in result.applies_to["task_types"]


def test_written_rule_is_schema_valid(tmp_path):
    task = {"task_run_id": "tr-1", "task_type": "code", "tags": ["auth"]}
    learn_from_failure(
        conn=None, run_id="run-1", task=task, rules_dir=tmp_path,
        source="verification", report=_report("HAS_FAIL", [_fail_criterion()]),
    )
    (path,) = list(tmp_path.glob("*.yaml"))
    rule = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert rule["name"]
    assert rule["applies_to"]["task_types"] or rule["applies_to"]["tags"]
    assert rule["file_rules"] or rule["skill_rules"]
    # No leftover temp file from the atomic write.
    assert list(tmp_path.glob("*.tmp")) == []


# ── DO-NOT-SAVE guardrail (transient/infra lesson filter) ────────────────────

class _Crit:
    def __init__(self, verdict, reasoning):
        self.verdict = verdict
        self.reasoning = reasoning
        self.criterion_text = reasoning


class _Report:
    overall = "HAS_FAIL"

    def __init__(self, criteria):
        self.criteria = criteria


def test_is_transient_lesson_flags_infra():
    assert _is_transient_lesson("connection timed out to localhost:5432")
    assert _is_transient_lesson("npm: command not found")
    assert not _is_transient_lesson("returns None instead of the computed total")


def test_verification_drops_transient_keeps_real():
    report = _Report([
        _Crit("FAIL", "connection timed out to the database"),
        _Crit("FAIL", "the function returns None instead of the computed total"),
    ])
    lessons = lessons_from_verification(report)
    assert any("computed total" in l for l in lessons)
    assert not any("timed out" in l for l in lessons)


def test_rejection_drops_transient():
    assert lesson_from_rejection("the build is flaky, just rerun it") == []


def test_rejection_keeps_real():
    assert lesson_from_rejection("missing input validation on the email field")
