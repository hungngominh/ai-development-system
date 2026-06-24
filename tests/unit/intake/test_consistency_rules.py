"""Consistency rule unit tests — pure functions, no I/O."""
from __future__ import annotations

import pytest

from ai_dev_system.intake.consistency_rules import (
    check_all,
    parse_availability_pct,
    parse_budget_usd,
    parse_deadline_weeks,
    RULES,
)


# ---------------------------------------------------------------------------
# Parse helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("6 tuần", 6.0),
    ("2 months", pytest.approx(8.66, rel=0.01)),
    ("14 days", 2.0),
    ("1 week", 1.0),
    ("", None),
    (None, None),
    ("không rõ", None),
])
def test_parse_deadline_weeks(text, expected):
    assert parse_deadline_weeks(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("99.99%", 99.99),
    ("99%", 99.0),
    ("three nines", None),
    (None, None),
])
def test_parse_availability_pct(text, expected):
    assert parse_availability_pct(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("$200", 200.0),
    ("1k USD", 1000.0),
    ("5tr VND", pytest.approx(200.0, rel=0.01)),  # 5,000,000 / 25,000
    ("không có budget", None),
    (None, None),
])
def test_parse_budget_usd(text, expected):
    got = parse_budget_usd(text)
    if expected is None:
        assert got is None
    else:
        assert got == expected


# ---------------------------------------------------------------------------
# Individual rules
# ---------------------------------------------------------------------------

def test_avail_vs_budget_fires_on_high_avail_low_budget():
    hits = check_all({
        "availability_target": "99.99%",
        "budget_infra": "$100/mo",
    })
    rule_ids = [h.rule_id for h in hits]
    assert "avail_vs_budget" in rule_ids


def test_avail_vs_budget_does_not_fire_on_match():
    hits = check_all({
        "availability_target": "99.99%",
        "budget_infra": "$5000/mo",
    })
    rule_ids = [h.rule_id for h in hits]
    assert "avail_vs_budget" not in rule_ids


def test_scope_vs_deadline_fires_on_excessive_scope():
    hits = check_all({
        "scope_in": ["a", "b", "c", "d", "e", "f", "g", "h"],
        "deadline": "2 tuần",
    })
    assert any(h.rule_id == "scope_vs_deadline" for h in hits)


def test_scope_vs_deadline_does_not_fire_when_balanced():
    hits = check_all({
        "scope_in": ["a", "b", "c"],
        "deadline": "8 tuần",
    })
    assert not any(h.rule_id == "scope_vs_deadline" for h in hits)


def test_residency_vs_deploy_fires():
    hits = check_all({
        "data_residency": "VN-only",
        "deployment_target": "AWS us-east-1",
    })
    assert any(h.rule_id == "residency_vs_deploy" for h in hits)


def test_residency_vs_deploy_excludes_explicit_vn_region():
    hits = check_all({
        "data_residency": "VN-only",
        "deployment_target": "AWS ap-southeast-1 (Singapore, VN data via lambda@edge)",
    })
    # No fire because deployment string mentions VN
    assert not any(h.rule_id == "residency_vs_deploy" for h in hits)


def test_team_vs_stack_fires_on_skill_gap():
    hits = check_all({
        "must_use_stack": ["rust", "kafka"],
        "team_skills": ["python", "postgres"],
    })
    assert any(h.rule_id == "team_vs_stack" for h in hits)


def test_team_vs_stack_no_fire_when_skills_cover_stack():
    hits = check_all({
        "must_use_stack": ["python"],
        "team_skills": ["python 5y", "postgres"],
    })
    assert not any(h.rule_id == "team_vs_stack" for h in hits)


def test_greenfield_vs_existing_auth_fires_on_inconsistency():
    hits = check_all({
        "greenfield_or_brownfield": "greenfield",
        "existing_auth": "Azure AD SSO",
    })
    assert any(h.rule_id == "greenfield_vs_existing_auth" for h in hits)


def test_greenfield_vs_existing_auth_no_fire_when_auth_none():
    hits = check_all({
        "greenfield_or_brownfield": "greenfield",
        "existing_auth": "none",
    })
    assert not any(h.rule_id == "greenfield_vs_existing_auth" for h in hits)


def test_brownfield_vs_data_sources_fires_when_missing():
    hits = check_all({
        "greenfield_or_brownfield": "brownfield",
        # data_sources missing
    })
    assert any(h.rule_id == "brownfield_vs_data_sources" for h in hits)


def test_user_count_year1_lt_now_fires():
    hits = check_all({
        "user_count_now": "1000",
        "user_count_year1": "500",
    })
    assert any(h.rule_id == "user_count_year1_lt_now" for h in hits)


def test_rps_vs_users_fires_on_implausible_rps():
    hits = check_all({
        "expected_rps": "5000",
        "user_count_now": "100",
    })
    assert any(h.rule_id == "rps_vs_users" for h in hits)


def test_accessibility_vs_user_facing_fires_when_blank():
    hits = check_all({
        "primary_user": "End customers using the mobile app",
        # accessibility missing
    })
    assert any(h.rule_id == "accessibility_vs_user_facing" for h in hits)


def test_latency_vs_availability_fires():
    hits = check_all({
        "latency_target": "50ms",
        "availability_target": "99%",
    })
    assert any(h.rule_id == "latency_vs_availability" for h in hits)


# ---------------------------------------------------------------------------
# Registry meta
# ---------------------------------------------------------------------------

def test_registry_has_at_least_ten_rules():
    assert len(RULES) >= 10


def test_no_rule_fires_on_empty_brief():
    hits = check_all({})
    assert hits == []


def test_no_rule_raises_on_arbitrary_input():
    """Robustness: rules accept None / wrong-type / missing keys without crashing."""
    weird = {
        "availability_target": 99,            # int instead of str
        "budget_infra": ["unexpected", "list"],
        "scope_in": "not a list",
        "deadline": object(),
        "expected_rps": True,
    }
    # Should not raise
    check_all(weird)
