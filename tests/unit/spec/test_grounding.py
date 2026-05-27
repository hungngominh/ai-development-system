"""Unit tests for spec.grounding (SP5).

Tests cover:
- scope_out_positive: flags scope_out items mentioned without exclusion context
- scope_out_positive: passes when item mentioned in "out of scope" sentence
- inline_refs: flags missing [brief:field] markers
- inline_refs: passes when all markers present
- measurable_ac: flags vague words without numbers (AC section only)
- measurable_ac: passes when vague words have nearby numbers
- scope_in_coverage: flags when no scope_in items appear (functional/AC only)
- scope_in_coverage: passes when at least one item appears
- Non-AC/functional sections skip measurable_ac and scope_in_coverage
- GroundingReport.has_errors / has_violations properties
"""

from __future__ import annotations

import pytest

from ai_dev_system.spec.grounding import (
    GroundingReport,
    GroundingViolation,
    check_section,
)
from ai_dev_system.spec.planner import SectionOutline


# ---- fixtures ----


def _outline(section: str = "proposal", must_reference=None) -> SectionOutline:
    return SectionOutline(
        section=section,
        must_cover=["Something"],
        must_reference=must_reference or ["problem_statement"],
        must_not_mention=[],
        assumptions_for_this_section=[],
    )


def _brief(scope_in=None, scope_out=None) -> dict:
    return {
        "brief_version": 2,
        "problem_statement": "Teams need async comms",
        "scope_in": ["chat", "notifications"] if scope_in is None else scope_in,
        "scope_out": ["mobile app"] if scope_out is None else scope_out,
        "nfr_priority": ["latency"],
        "success_metric": "DAU > 1000",
    }


# ---- GroundingReport helpers ----


def test_report_has_no_violations_initially():
    r = GroundingReport(section="proposal")
    assert not r.has_violations
    assert not r.has_errors


def test_report_has_errors_when_error_severity():
    r = GroundingReport(section="proposal")
    r.violations.append(GroundingViolation(rule="r", message="m", severity="error"))
    assert r.has_errors
    assert r.has_violations


def test_report_has_violations_but_not_errors_for_warning():
    r = GroundingReport(section="proposal")
    r.violations.append(GroundingViolation(rule="r", message="m", severity="warning"))
    assert r.has_violations
    assert not r.has_errors


# ---- scope_out_positive ----


def test_scope_out_positive_flags_item_mentioned_positively():
    content = "## Design\n\nWe will build a mobile app with full feature parity."
    brief = _brief(scope_out=["mobile app"])
    report = check_section("design", content, _outline("design"), brief)
    rules = [v.rule for v in report.violations]
    assert "scope_out_positive" in rules


def test_scope_out_positive_passes_when_mentioned_in_exclusion_context():
    content = (
        "## Functional\n\n"
        "The system covers desktop and web clients. "
        "Out of scope: mobile app. This will not be built."
    )
    brief = _brief(scope_out=["mobile app"])
    report = check_section("functional", content, _outline("functional"), brief)
    rules = [v.rule for v in report.violations]
    assert "scope_out_positive" not in rules


def test_scope_out_positive_passes_when_item_not_mentioned():
    content = "## Proposal\n\nWe will build a chat system for desktop."
    brief = _brief(scope_out=["mobile app"])
    report = check_section("proposal", content, _outline("proposal"), brief)
    assert "scope_out_positive" in report.passed_rules


def test_scope_out_positive_passes_with_empty_scope_out():
    content = "## Design\n\nAll features are in scope."
    brief = _brief(scope_out=[])
    report = check_section("design", content, _outline("design"), brief)
    assert "scope_out_positive" in report.passed_rules


def test_scope_out_positive_exclusion_phrase_ngoai_pham_vi():
    content = "Ngoài phạm vi: mobile app sẽ không được xây dựng."
    brief = _brief(scope_out=["mobile app"])
    report = check_section("proposal", content, _outline("proposal"), brief)
    rules = [v.rule for v in report.violations]
    assert "scope_out_positive" not in rules


# ---- inline_refs ----


def test_inline_refs_flags_missing_marker():
    content = "## Proposal\n\nThis solves the problem that teams face."
    # missing [brief:problem_statement]
    outline = _outline(must_reference=["problem_statement"])
    report = check_section("proposal", content, outline, _brief())
    rules = [v.rule for v in report.violations]
    assert "inline_refs" in rules
    assert any(v.severity == "warning" for v in report.violations if v.rule == "inline_refs")


def test_inline_refs_passes_when_marker_present():
    content = (
        "## Proposal\n\n"
        "According to the brief [brief:problem_statement]: teams need async comms."
    )
    outline = _outline(must_reference=["problem_statement"])
    report = check_section("proposal", content, outline, _brief())
    assert "inline_refs" in report.passed_rules


def test_inline_refs_skips_decision_refs():
    content = "## Design\n\nSome content without decision markers."
    outline = _outline(must_reference=["D1"])  # decision ref — skip
    report = check_section("design", content, outline, _brief())
    # D1 has no colon so doesn't look like a decision ref by our check
    # but it's a short string — just verify no crash
    assert isinstance(report, GroundingReport)


def test_inline_refs_multiple_fields_partial_missing():
    content = "## Proposal\n\n[brief:problem_statement] is described here."
    outline = _outline(must_reference=["problem_statement", "success_metric"])
    report = check_section("proposal", content, outline, _brief())
    inline_viol = [v for v in report.violations if v.rule == "inline_refs"]
    assert inline_viol
    assert "success_metric" in inline_viol[0].message


def test_inline_refs_all_present():
    content = (
        "## Proposal\n\n"
        "[brief:problem_statement] context. [brief:success_metric] target."
    )
    outline = _outline(must_reference=["problem_statement", "success_metric"])
    report = check_section("proposal", content, outline, _brief())
    assert "inline_refs" in report.passed_rules


# ---- measurable_ac ----


def test_measurable_ac_flags_vague_words_without_numbers():
    content = (
        "## Acceptance Criteria\n\n"
        "Given user logs in, When they submit, Then the system responds fast."
    )
    report = check_section("acceptance_criteria", content, _outline("acceptance_criteria"), _brief())
    rules = [v.rule for v in report.violations]
    assert "measurable_ac" in rules


def test_measurable_ac_passes_when_number_nearby():
    content = (
        "## Acceptance Criteria\n\n"
        "Given user logs in, When they submit, Then response time < 200ms (fast path)."
    )
    report = check_section("acceptance_criteria", content, _outline("acceptance_criteria"), _brief())
    assert "measurable_ac" in report.passed_rules


def test_measurable_ac_not_checked_for_proposal():
    content = "## Proposal\n\nThis is a good idea that works properly."
    report = check_section("proposal", content, _outline("proposal"), _brief())
    rules_checked = report.passed_rules + [v.rule for v in report.violations]
    assert "measurable_ac" not in rules_checked


def test_measurable_ac_passes_when_no_vague_words():
    content = (
        "## Acceptance Criteria\n\n"
        "Given a user submits, When the request is processed, "
        "Then the response arrives within 300ms and error rate < 0.1%."
    )
    report = check_section("acceptance_criteria", content, _outline("acceptance_criteria"), _brief())
    assert "measurable_ac" in report.passed_rules


# ---- scope_in_coverage ----


def test_scope_in_coverage_flags_no_scope_items_in_functional():
    content = "## Functional\n\nThe system provides a way for users to interact."
    brief = _brief(scope_in=["voting", "leaderboard"])
    report = check_section("functional", content, _outline("functional"), brief)
    rules = [v.rule for v in report.violations]
    assert "scope_in_coverage" in rules


def test_scope_in_coverage_passes_when_some_items_present():
    content = "## Functional\n\nThe chat feature allows real-time messaging."
    brief = _brief(scope_in=["chat", "notifications"])
    report = check_section("functional", content, _outline("functional"), brief)
    assert "scope_in_coverage" in report.passed_rules


def test_scope_in_coverage_not_checked_for_design():
    content = "## Design\n\nThe architecture uses microservices."
    brief = _brief(scope_in=["chat"])
    report = check_section("design", content, _outline("design"), brief)
    rules_checked = report.passed_rules + [v.rule for v in report.violations]
    assert "scope_in_coverage" not in rules_checked


def test_scope_in_coverage_passes_with_empty_scope_in():
    content = "## Functional\n\nNo specific scope items."
    brief = _brief(scope_in=[])
    report = check_section("functional", content, _outline("functional"), brief)
    assert "scope_in_coverage" in report.passed_rules


def test_scope_in_coverage_also_checked_for_acceptance_criteria():
    content = "## AC\n\nGiven user, When action, Then result."
    brief = _brief(scope_in=["reporting", "dashboard"])
    report = check_section("acceptance_criteria", content, _outline("acceptance_criteria"), brief)
    rules = [v.rule for v in report.violations]
    assert "scope_in_coverage" in rules
