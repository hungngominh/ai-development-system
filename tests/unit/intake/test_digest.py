"""Unit tests for `intake/digest.py` (M2.10)."""
from __future__ import annotations

import pytest

from ai_dev_system.intake.digest import (
    CRITICAL_ORDER,
    DEFAULT_MAX_CHARS,
    brief_digest,
    estimate_tokens,
)


def _brief(**field_overrides) -> dict:
    """Build a brief v2 fixture; pass field_id=value or field_id=(value, source)."""
    fields = {}
    for fid in CRITICAL_ORDER:
        fields[fid] = {"value": f"placeholder for {fid}", "source": "user", "rationale": None}
    for fid, val in field_overrides.items():
        if isinstance(val, tuple):
            value, source = val
            fields[fid] = {"value": value, "source": source, "rationale": None}
        else:
            fields[fid] = {"value": val, "source": "user", "rationale": None}
    return {
        "brief_version": 2,
        "template_id": "generic_v1",
        "schema_hash": "abc",
        "run_id": "r1",
        "project_id": "proj-x",
        "source_hash": "deadbeefcafebabe1234567890abcdef",
        "created_at": "2026-05-23T10:00:00+00:00",
        "completed_at": "2026-05-23T10:15:00+00:00",
        "fields": fields,
        "assumptions": [],
        "audit": [],
    }


def test_digest_starts_with_header_and_project_id():
    out = brief_digest(_brief())
    assert out.startswith("# Brief — proj-x")
    # Source hash short-form (first 8 chars) appears.
    assert "deadbeef" in out
    assert "template generic_v1" in out


def test_digest_includes_every_critical_field():
    out = brief_digest(_brief())
    for fid in CRITICAL_ORDER:
        assert f"- {fid}:" in out, f"critical field {fid} missing from digest"


def test_digest_respects_default_char_budget():
    out = brief_digest(_brief())
    assert len(out) <= DEFAULT_MAX_CHARS


def test_digest_is_deterministic_for_same_brief():
    b = _brief()
    assert brief_digest(b) == brief_digest(b)


def test_digest_marks_ai_suggested_fields():
    """`source = ai_suggested_confirmed` renders as `[ai]` marker."""
    b = _brief(deployment_target=("Azure SEA", "ai_suggested_confirmed"))
    out = brief_digest(b)
    line = [ln for ln in out.splitlines() if ln.startswith("- deployment_target:")][0]
    assert "[ai]" in line
    assert "Azure SEA" in line


def test_digest_skipped_field_shows_in_critical_and_assumptions():
    b = _brief(primary_user=(None, "skipped"))
    b["assumptions"] = ["primary_user"]
    out = brief_digest(b)
    # Critical section line:
    crit = [ln for ln in out.splitlines() if ln.startswith("- primary_user:")][0]
    assert "(skipped)" in crit
    assert "[skip]" in crit
    # Assumptions section line:
    assert "## Assumptions" in out
    assert "- primary_user: skipped (no answer)" in out


def test_digest_collapses_long_lists():
    """Lists longer than LIST_TAIL_LIMIT collapse with '(+N more)'."""
    long_list = [f"item-{i}" for i in range(10)]
    b = _brief(scope_in=long_list)
    out = brief_digest(b)
    scope_line = [ln for ln in out.splitlines() if ln.startswith("- scope_in:")][0]
    assert "+6 more" in scope_line  # 10 items, 4 shown → 6 collapsed


def test_digest_truncates_very_long_string_values():
    huge = "x" * 5000
    b = _brief(problem_statement=huge)
    out = brief_digest(b)
    # The whole digest must still fit the budget.
    assert len(out) <= DEFAULT_MAX_CHARS
    # The problem_statement line must include the truncation marker '…'.
    prob_line = [ln for ln in out.splitlines() if ln.startswith("- problem_statement:")][0]
    assert "…" in prob_line
    # And it must NOT contain the full 5000 x's (sanity).
    assert "x" * 1000 not in prob_line


def test_digest_drops_context_fields_under_tight_budget():
    """Critical + assumptions are preserved; context section shrinks first."""
    b = _brief()
    b["fields"]["nfr_priority"] = {"value": ["A", "B", "C"], "source": "user", "rationale": None}
    b["fields"]["must_use_stack"] = {"value": ["Py"], "source": "user", "rationale": None}

    tight = brief_digest(b, max_chars=600)
    assert len(tight) <= 600
    # Critical fields survive
    for fid in CRITICAL_ORDER:
        assert f"- {fid}:" in tight


def test_digest_omits_context_section_entirely_when_no_context_fields():
    b = _brief()  # only critical fields set, no context fields
    out = brief_digest(b)
    assert "## Context" not in out


def test_digest_includes_context_section_when_context_fields_present():
    b = _brief()
    b["fields"]["nfr_priority"] = {"value": ["TTM", "Maintainability"], "source": "user", "rationale": None}
    out = brief_digest(b)
    assert "## Context" in out
    assert "- nfr_priority:" in out


def test_digest_tolerates_missing_assumptions_key():
    b = _brief()
    b.pop("assumptions", None)
    out = brief_digest(b)
    # No crash; no Assumptions section emitted.
    assert "## Assumptions" not in out


def test_digest_tolerates_non_list_assumptions_field():
    b = _brief()
    b["assumptions"] = "not-a-list"  # bad input
    out = brief_digest(b)
    assert "## Assumptions" not in out


def test_digest_raises_for_non_dict():
    with pytest.raises(TypeError):
        brief_digest("not a brief")  # type: ignore[arg-type]


def test_estimate_tokens_roughly_matches_500_for_full_digest():
    """A typical digest at the default budget should land near the 500-token target."""
    b = _brief()
    b["fields"]["nfr_priority"] = {"value": ["TTM", "Maintainability"], "source": "user", "rationale": None}
    out = brief_digest(b)
    tokens = estimate_tokens(out)
    # ~4 chars per token; 2000-char budget → ≤500 tokens.
    assert tokens <= 500
    assert tokens >= 50  # not a stub — actual content present


def test_digest_works_with_empty_fields_block():
    b = _brief()
    b["fields"] = {}
    out = brief_digest(b)
    # Critical fields render as "(missing from template)" rows, not crashes.
    assert "missing from template" in out
