"""Unit tests for gate.gate1_review.editor (G7).

Tests cover:
- apply_edit: editable scalar fields (set)
- apply_edit: editable list fields (set/append/remove)
- apply_edit: non-editable fields rejected
- apply_edit: unknown fields rejected
- apply_edit: scope-affecting fields flagged
- apply_edit: empty override payload edge cases
- check_editable: returns correct tuples
- parser: edit_brief action recognized
"""

from __future__ import annotations

import pytest

from ai_dev_system.gate.gate1_review.editor import (
    EDITABLE_FIELDS,
    NON_EDITABLE_FIELDS,
    BriefEdit,
    EditResult,
    apply_edit,
    check_editable,
)
from ai_dev_system.gate.gate1_review.parser import ParseResult, parse_user_input


# ---- apply_edit: scalar fields ----


def test_set_scalar_problem_statement():
    brief = {"problem_statement": "Old text"}
    result = apply_edit(brief, "problem_statement", "set", "New text")
    assert result.accepted is True
    assert brief["problem_statement"] == "New text"
    assert result.edit is not None
    assert result.edit.old_value == "Old text"
    assert result.edit.new_value == "New text"


def test_set_scalar_deadline():
    brief = {}
    result = apply_edit(brief, "deadline", "set", "2026-12-31")
    assert result.accepted is True
    assert brief["deadline"] == "2026-12-31"


def test_scalar_append_rejected():
    brief = {"problem_statement": "text"}
    result = apply_edit(brief, "problem_statement", "append", "extra")
    assert result.accepted is False
    assert "scalar" in result.message.lower() or "set" in result.message.lower()


# ---- apply_edit: list fields ----


def test_set_list_scope_in():
    brief = {"scope_in": ["chat"]}
    result = apply_edit(brief, "scope_in", "set", ["chat", "notifications"])
    assert result.accepted is True
    assert brief["scope_in"] == ["chat", "notifications"]


def test_append_scope_in():
    brief = {"scope_in": ["chat"]}
    result = apply_edit(brief, "scope_in", "append", "notifications")
    assert result.accepted is True
    assert "notifications" in brief["scope_in"]
    assert "chat" in brief["scope_in"]


def test_append_duplicate_rejected():
    brief = {"scope_in": ["chat", "notifications"]}
    result = apply_edit(brief, "scope_in", "append", "chat")
    assert result.accepted is False
    assert "đã có" in result.message


def test_remove_scope_out():
    brief = {"scope_out": ["mobile", "analytics"]}
    result = apply_edit(brief, "scope_out", "remove", "analytics")
    assert result.accepted is True
    assert "analytics" not in brief["scope_out"]
    assert "mobile" in brief["scope_out"]


def test_remove_nonexistent_item_rejected():
    brief = {"scope_out": ["mobile"]}
    result = apply_edit(brief, "scope_out", "remove", "analytics")
    assert result.accepted is False
    assert "không có" in result.message


def test_set_list_field_with_non_list_rejected():
    brief = {}
    result = apply_edit(brief, "scope_in", "set", "not a list")
    assert result.accepted is False


# ---- apply_edit: scope-affecting fields ----


def test_scope_in_edit_flags_scope_affected():
    brief = {"scope_in": ["chat"]}
    result = apply_edit(brief, "scope_in", "append", "moderation")
    assert result.scope_affected is True
    assert "scope" in result.message.lower() or "G8" in result.message


def test_scope_out_edit_flags_scope_affected():
    brief = {"scope_out": []}
    result = apply_edit(brief, "scope_out", "set", ["desktop"])
    assert result.scope_affected is True


def test_non_scope_field_not_scope_affected():
    brief = {}
    result = apply_edit(brief, "problem_statement", "set", "new")
    assert result.scope_affected is False


# ---- apply_edit: non-editable fields ----


@pytest.mark.parametrize("field", sorted(NON_EDITABLE_FIELDS))
def test_non_editable_field_rejected(field):
    brief = {field: "old"}
    result = apply_edit(brief, field, "set", "new")
    assert result.accepted is False
    assert field in result.message
    # Brief should be unchanged
    assert brief.get(field) == "old"


# ---- apply_edit: unknown field ----


def test_unknown_field_rejected():
    brief = {}
    result = apply_edit(brief, "unknown_field_xyz", "set", "value")
    assert result.accepted is False
    assert "unknown_field_xyz" in result.message


# ---- apply_edit: edit does not mutate on rejection ----


def test_rejection_leaves_brief_unchanged():
    brief = {"compliance": "GDPR"}
    result = apply_edit(brief, "compliance", "set", "HIPAA")
    assert brief["compliance"] == "GDPR"
    assert result.accepted is False


# ---- check_editable ----


def test_check_editable_returns_true_for_editable():
    ok, reason = check_editable("problem_statement")
    assert ok is True
    assert reason == ""


def test_check_editable_returns_false_for_non_editable():
    ok, reason = check_editable("compliance")
    assert ok is False
    assert "compliance" in reason


def test_check_editable_returns_false_for_unknown():
    ok, reason = check_editable("no_such_field")
    assert ok is False


# ---- BriefEdit dataclass ----


def test_brief_edit_fields():
    edit = BriefEdit(field="scope_in", operation="append", old_value=["a"], new_value=["a", "b"])
    assert edit.field == "scope_in"
    assert edit.operation == "append"
    assert edit.old_value == ["a"]
    assert edit.new_value == ["a", "b"]


# ---- parser: edit_brief action ----


def test_parser_recognizes_edit_brief_colon():
    r = parse_user_input("edit problem_statement: new problem text")
    assert r.action_type == "edit_brief"
    assert r.target == "problem_statement"
    assert r.payload == "new problem text"
    assert r.accepted is True


def test_parser_recognizes_edit_brief_equals():
    r = parse_user_input("edit scope_in = ['chat', 'notifications']")
    assert r.action_type == "edit_brief"
    assert r.target == "scope_in"


def test_parser_edit_brief_case_insensitive():
    r = parse_user_input("EDIT deadline: 2027-01-01")
    assert r.action_type == "edit_brief"
    assert r.target == "deadline"


def test_parser_edit_brief_message_contains_field():
    r = parse_user_input("edit who_feels_pain: small teams")
    assert "who_feels_pain" in r.message
    assert "small teams" in r.message or "Đúng không" in r.message
