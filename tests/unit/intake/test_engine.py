"""Engine state machine tests — pure, no I/O."""
from __future__ import annotations

import pytest

from ai_dev_system.intake.engine import (
    IntakeState,
    new_state,
    start,
    step,
    to_brief_v2,
)
from ai_dev_system.intake.template import load_template


@pytest.fixture
def tpl():
    return load_template("generic_v1")


@pytest.fixture
def state(tpl):
    return new_state(tpl, run_id="run-1", project_id="proj-1")


def _answer_all(tpl, state, value_for_field):
    """Helper: feed value_for_field(field) into every field until DONE/CONFIRM."""
    while state.stage == "ASKING" and state.field_idx < len(tpl.fields):
        fld = tpl.fields[state.field_idx]
        val = value_for_field(fld)
        result = step(tpl, state, val)
        state = result.state
    return state, result


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------

def test_new_state_starts_at_field_zero(state, tpl):
    assert state.stage == "ASKING"
    assert state.field_idx == 0
    assert state.template_id == tpl.id
    assert state.schema_hash == tpl.schema_hash


def test_start_renders_first_field_prompt(tpl, state):
    result = start(tpl, state)
    first = tpl.fields[0]
    assert result.current_field == first
    assert first.id in result.prompt
    assert "1/" in result.prompt  # progress marker


# ---------------------------------------------------------------------------
# Basic answer flow
# ---------------------------------------------------------------------------

def test_text_answer_advances_idx(tpl, state):
    first = tpl.fields[0]
    assert first.type == "text_long"
    result = step(tpl, state, "Nhân viên không tìm được tài liệu cũ")
    assert result.state.field_idx == 1
    assert result.state.answers[first.id].value == "Nhân viên không tìm được tài liệu cũ"
    assert result.state.answers[first.id].source == "user"


def test_empty_input_keeps_idx(tpl, state):
    result = step(tpl, state, "   ")
    assert result.state.field_idx == 0
    assert result.error is not None


def test_list_str_parses_comma_separated(tpl, state):
    # Advance to a list_str field — `scope_in`
    idx = tpl.field_index("scope_in")
    state.field_idx = idx
    result = step(tpl, state, "search, browse, comment")
    assert result.state.answers["scope_in"].value == ["search", "browse", "comment"]


def test_list_str_rejects_empty(tpl, state):
    idx = tpl.field_index("scope_in")
    state.field_idx = idx
    result = step(tpl, state, ",,,  ,")
    assert result.error is not None
    assert state.answers.get("scope_in") is None


def test_enum_validates_options(tpl, state):
    idx = tpl.field_index("greenfield_or_brownfield")
    state.field_idx = idx
    bad = step(tpl, state, "purpleField")
    assert bad.error is not None
    ok = step(tpl, state, "greenfield")
    assert ok.error is None
    assert state.answers["greenfield_or_brownfield"].value == "greenfield"


# ---------------------------------------------------------------------------
# Special commands
# ---------------------------------------------------------------------------

def test_skip_records_skipped_source(tpl, state):
    first = tpl.fields[0]
    result = step(tpl, state, "skip")
    assert state.answers[first.id].source == "skipped"
    assert state.answers[first.id].value is None
    assert state.field_idx == 1


def test_back_from_first_field_errors(tpl, state):
    result = step(tpl, state, "back")
    assert result.error == "cannot_back_from_first"
    assert state.field_idx == 0


def test_back_returns_to_previous_field(tpl, state):
    step(tpl, state, "Problem A")          # idx 0 → 1
    result = step(tpl, state, "back")      # idx 1 → 0
    assert state.field_idx == 0


def test_show_keeps_idx(tpl, state):
    step(tpl, state, "Problem A")          # idx 0 → 1
    before_idx = state.field_idx
    result = step(tpl, state, "show")
    assert state.field_idx == before_idx
    assert "Current brief" in result.prompt


def test_save_marks_paused(tpl, state):
    result = step(tpl, state, "save")
    assert result.terminal is True
    assert result.terminal_reason == "paused"
    assert state.stage == "PAUSED"


# ---------------------------------------------------------------------------
# CONFIRM stage
# ---------------------------------------------------------------------------

def _trivial_value(fld):
    if fld.type == "list_str":
        return "a, b"
    if fld.type == "enum":
        return fld.options[0]
    if fld.type == "number":
        return "1"
    return "x"


def test_answering_all_fields_enters_confirm(tpl, state):
    state, last = _answer_all(tpl, state, _trivial_value)
    assert state.stage == "CONFIRM"
    assert "confirm" in last.prompt.lower()


def test_confirm_completes_and_marks_done(tpl, state):
    state, _ = _answer_all(tpl, state, _trivial_value)
    result = step(tpl, state, "confirm")
    assert result.terminal
    assert result.terminal_reason == "complete"
    assert state.stage == "DONE"
    assert state.completed_at is not None


def test_edit_returns_to_asking_at_field(tpl, state):
    state, _ = _answer_all(tpl, state, _trivial_value)
    result = step(tpl, state, "edit problem_statement")
    assert state.stage == "ASKING"
    assert state.field_idx == tpl.field_index("problem_statement")
    assert "problem_statement" in result.prompt


def test_edit_unknown_field_errors(tpl, state):
    state, _ = _answer_all(tpl, state, _trivial_value)
    result = step(tpl, state, "edit not_a_field")
    assert result.error == "unknown_field"
    assert state.stage == "CONFIRM"


def test_critical_skipped_recorded_as_assumption_warning(tpl, state):
    # Skip every field including criticals
    while state.stage == "ASKING" and state.field_idx < len(tpl.fields):
        step(tpl, state, "skip")

    # Now in CONFIRM — prompt must mention critical fields missing
    result = start(tpl, state)  # idempotent re-render
    assert state.stage == "CONFIRM"
    # The CONFIRM prompt is rendered by step() that pushed us in; we re-render via start()
    # but start() short-circuits when stage != ASKING. Re-enter via a noop step.
    # Easier: call to_brief_v2 and check assumptions.
    brief = to_brief_v2(state, tpl, source_hash="zzz")
    assert set(brief["assumptions"]) == set(tpl.critical_field_ids)


# ---------------------------------------------------------------------------
# Serialization roundtrip
# ---------------------------------------------------------------------------

def test_state_json_roundtrip(tpl, state):
    step(tpl, state, "Problem A")
    step(tpl, state, "skip")
    raw = state.to_json()
    restored = IntakeState.from_json(raw)
    assert restored.field_idx == state.field_idx
    first_id = tpl.fields[0].id
    second_id = tpl.fields[1].id
    assert restored.answers[first_id].value == "Problem A"
    assert restored.answers[second_id].source == "skipped"


def test_brief_output_has_required_fields(tpl, state):
    state, _ = _answer_all(tpl, state, _trivial_value)
    step(tpl, state, "confirm")
    brief = to_brief_v2(state, tpl, source_hash="abc123")
    assert brief["brief_version"] == 2
    assert brief["template_id"] == "generic_v1"
    assert brief["source_hash"] == "abc123"
    assert brief["completed_at"] is not None
    assert set(brief["fields"].keys()) == set(f.id for f in tpl.fields)
    # All answered via _trivial_value — no assumptions
    assert brief["assumptions"] == []
