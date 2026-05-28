"""Tests for brief_metrics — Layer 1 of eval harness."""
from __future__ import annotations

import pytest

from ai_dev_system.eval.metrics.brief_metrics import (
    CRITICAL_FIELDS,
    SECTIONS,
    THRESHOLDS,
    compute_ai_suggest_acceptance,
    compute_assumption_count,
    compute_brief_metrics,
    compute_consistency_violations,
    compute_critical_fill_rate,
    compute_field_coverage_per_section,
    compute_followup_question_count,
)


def _make_brief(fields: dict | None = None, **kwargs) -> dict:
    """Helper: build a brief v2 dict with given fields filled."""
    brief = {
        "brief_version": 2,
        "template_id": "generic_v1",
        "fields": fields or {},
        "assumptions": [],
        "audit": [],
    }
    brief.update(kwargs)
    return brief


def _filled(value, source="user"):
    """Helper: build a field entry dict."""
    return {"value": value, "source": source, "rationale": None}


# ============================================================================
# critical_fill_rate
# ============================================================================

class TestCriticalFillRate:
    def test_all_critical_filled(self):
        fields = {f: _filled(f"value_{f}") for f in CRITICAL_FIELDS}
        rate, missing = compute_critical_fill_rate(_make_brief(fields))
        assert rate == 1.0
        assert missing == []

    def test_none_filled(self):
        rate, missing = compute_critical_fill_rate(_make_brief({}))
        assert rate == 0.0
        assert set(missing) == set(CRITICAL_FIELDS)

    def test_partial(self):
        fields = {
            "problem_statement": _filled("a"),
            "scope_in": _filled(["x"]),
            "scope_out": _filled(["y"]),
            "success_metric": _filled("WAU 100"),
        }
        rate, missing = compute_critical_fill_rate(_make_brief(fields))
        assert rate == 4 / 8
        assert "primary_user" in missing
        assert "deployment_target" in missing

    def test_empty_string_is_not_filled(self):
        fields = {f: _filled("") for f in CRITICAL_FIELDS}
        rate, missing = compute_critical_fill_rate(_make_brief(fields))
        assert rate == 0.0
        assert len(missing) == 8

    def test_empty_list_is_not_filled(self):
        fields = {f: _filled([] if f.startswith("scope") else "v") for f in CRITICAL_FIELDS}
        rate, _ = compute_critical_fill_rate(_make_brief(fields))
        # scope_in and scope_out are empty list → not filled
        assert rate == 6 / 8

    def test_lenient_fallback(self):
        """Old briefs with flat keys (no 'fields' wrapper) should still work."""
        brief = {"problem_statement": "x", "scope_in": ["a"]}
        rate, _ = compute_critical_fill_rate(brief)
        assert rate == 2 / 8


# ============================================================================
# ai_suggest_acceptance
# ============================================================================

class TestAiSuggestAcceptance:
    def test_no_suggestions_is_vacuously_pass(self):
        brief = _make_brief({"foo": _filled("v", source="user")})
        assert compute_ai_suggest_acceptance(brief) == 1.0

    def test_all_confirmed(self):
        brief = _make_brief({
            "a": _filled("v1", source="ai_suggested_confirmed"),
            "b": _filled("v2", source="ai_suggested_confirmed"),
        })
        assert compute_ai_suggest_acceptance(brief) == 1.0

    def test_half_confirmed(self):
        brief = _make_brief({
            "a": _filled("v1", source="ai_suggested_confirmed"),
            "b": _filled("v2", source="ai_suggested_rejected"),
        })
        assert compute_ai_suggest_acceptance(brief) == 0.5

    def test_user_fields_not_counted(self):
        brief = _make_brief({
            "a": _filled("v1", source="user"),
            "b": _filled("v2", source="ai_suggested_confirmed"),
        })
        assert compute_ai_suggest_acceptance(brief) == 1.0  # 1 of 1 AI suggestion


# ============================================================================
# assumption_count
# ============================================================================

class TestAssumptionCount:
    def test_empty(self):
        assert compute_assumption_count(_make_brief()) == 0

    def test_three(self):
        brief = _make_brief(assumptions=["x", "y", "z"])
        assert compute_assumption_count(brief) == 3

    def test_non_list(self):
        brief = _make_brief(assumptions="not a list")
        assert compute_assumption_count(brief) == 0


# ============================================================================
# consistency_violations
# ============================================================================

class TestConsistencyViolations:
    def test_no_rules(self):
        assert compute_consistency_violations(_make_brief(), None) == 0

    def test_no_violations(self):
        rules = [lambda b: False, lambda b: False]
        assert compute_consistency_violations(_make_brief(), rules) == 0

    def test_one_violation(self):
        rules = [lambda b: True, lambda b: False]
        assert compute_consistency_violations(_make_brief(), rules) == 1

    def test_rule_error_is_swallowed(self):
        def bad_rule(b):
            raise RuntimeError("oops")
        rules = [bad_rule, lambda b: True]
        # bad rule swallowed, good rule fires → 1
        assert compute_consistency_violations(_make_brief(), rules) == 1


# ============================================================================
# field_coverage_per_section
# ============================================================================

class TestFieldCoveragePerSection:
    def test_all_filled(self):
        fields = {}
        for section_fields in SECTIONS.values():
            for f in section_fields:
                fields[f] = _filled("v")
        min_cov, per_section = compute_field_coverage_per_section(_make_brief(fields))
        assert min_cov == 1.0
        for cov in per_section.values():
            assert cov == 1.0

    def test_one_section_empty(self):
        fields = {}
        for name, section_fields in SECTIONS.items():
            if name == "context":
                continue  # leave context empty
            for f in section_fields:
                fields[f] = _filled("v")
        min_cov, per_section = compute_field_coverage_per_section(_make_brief(fields))
        assert per_section["context"] == 0.0
        assert min_cov == 0.0


# ============================================================================
# followup_question_count
# ============================================================================

class TestFollowupCount:
    def test_no_audit(self):
        assert compute_followup_question_count(_make_brief()) == 0

    def test_three_followups(self):
        brief = _make_brief(audit=[
            {"event": "answered", "field": "x"},
            {"event": "followup_asked", "field": "y"},
            {"event": "followup_asked", "field": "z"},
            {"event": "followup_asked", "field": "w"},
        ])
        assert compute_followup_question_count(brief) == 3


# ============================================================================
# compute_brief_metrics — integration
# ============================================================================

class TestComputeBriefMetrics:
    def test_perfect_brief_passes_all(self):
        # Fill every field in every section
        fields = {}
        for section_fields in SECTIONS.values():
            for f in section_fields:
                fields[f] = _filled("value")
        report = compute_brief_metrics(_make_brief(fields))
        assert report.overall_pass()
        assert report.critical_fill_rate == 1.0

    def test_legacy_skeleton_fails_critical(self):
        """A v1-style empty brief should fail critical_fill threshold."""
        report = compute_brief_metrics(_make_brief({}))
        assert not report.pass_critical_fill
        assert report.critical_fill_rate == 0.0
        assert len(report.missing_critical) == 8

    def test_threshold_exact_pass(self):
        """7/8 critical fields filled meets threshold 0.875."""
        fields = {f: _filled("v") for f in CRITICAL_FIELDS[:7]}
        report = compute_brief_metrics(_make_brief(fields))
        assert report.critical_fill_rate == pytest.approx(0.875)
        assert report.pass_critical_fill

    def test_report_serializable(self):
        report = compute_brief_metrics(_make_brief())
        d = report.to_dict()
        assert "critical_fill_rate" in d
        assert "section_coverage" in d
        assert "missing_critical" in d


class TestThresholdConfig:
    def test_critical_threshold_documented(self):
        assert THRESHOLDS["critical_fill_rate"] == 0.875

    def test_all_thresholds_present(self):
        expected_keys = {
            "critical_fill_rate",
            "ai_suggest_acceptance",
            "assumption_count_max",
            "consistency_violations_max",
            "field_coverage_per_section",
            "followup_question_count_max",
        }
        assert set(THRESHOLDS.keys()) == expected_keys
