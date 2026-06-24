"""Tests for context_loaded source behavior in the intake engine."""
from __future__ import annotations

import pytest

from ai_dev_system.intake.engine import (
    FieldAnswer,
    new_state,
    start,
    step,
    to_brief_v2,
    _render_state,
    _render_prompt,
    _get_prefilled,
)
from ai_dev_system.intake.template import load_template


@pytest.fixture
def tpl():
    return load_template("generic_v1")


@pytest.fixture
def state_with_prefill(tpl):
    state = new_state(tpl, run_id="run-ctx", project_id="proj-ctx")
    # Pre-fill first field with context_loaded
    first = tpl.fields[0]
    state.answers[first.id] = FieldAnswer(value="Pre-filled problem", source="context_loaded")
    return state


# ---------------------------------------------------------------------------
# _render_prompt shows context hint
# ---------------------------------------------------------------------------

def test_render_prompt_shows_context_when_prefilled(tpl):
    fld = tpl.fields[0]
    prompt = _render_prompt(fld, 0, len(tpl.fields), prefilled="Pre-filled value")
    assert "Context:" in prompt
    assert "Pre-filled value" in prompt
    assert "enter" in prompt.lower() or "de dung" in prompt.lower()


def test_render_prompt_shows_normal_commands_when_no_prefill(tpl):
    fld = tpl.fields[0]
    prompt = _render_prompt(fld, 0, len(tpl.fields), prefilled=None)
    assert "Context:" not in prompt
    assert "Commands:" in prompt


def test_render_prompt_list_prefill_joined(tpl):
    # Find a list_str field
    list_fld = next(f for f in tpl.fields if f.type == "list_str")
    prompt = _render_prompt(list_fld, 0, len(tpl.fields), prefilled=["item1", "item2"])
    assert "item1" in prompt
    assert "item2" in prompt


# ---------------------------------------------------------------------------
# start() passes prefill to prompt when field is context_loaded
# ---------------------------------------------------------------------------

def test_start_includes_context_hint_for_prefilled_field(tpl, state_with_prefill):
    result = start(tpl, state_with_prefill)
    assert "Context:" in result.prompt
    assert "Pre-filled problem" in result.prompt


def test_start_no_context_hint_for_normal_field(tpl):
    state = new_state(tpl, run_id="run-2", project_id="proj-2")
    result = start(tpl, state)
    assert "Context:" not in result.prompt


# ---------------------------------------------------------------------------
# step() — Enter accepts context_loaded value
# ---------------------------------------------------------------------------

def test_enter_on_context_loaded_field_accepts_value(tpl, state_with_prefill):
    first = tpl.fields[0]
    assert state_with_prefill.answers[first.id].source == "context_loaded"

    result = step(tpl, state_with_prefill, "")  # empty input = Enter
    # Should advance past field 0
    assert state_with_prefill.field_idx == 1
    # Source stays "context_loaded"
    assert state_with_prefill.answers[first.id].source == "context_loaded"
    # Audit event recorded
    audit_events = [e["event"] for e in state_with_prefill.audit]
    assert "context_confirmed" in audit_events


def test_enter_on_non_prefilled_field_shows_error(tpl):
    state = new_state(tpl, run_id="run-3", project_id="proj-3")
    # No pre-fill → empty input should give error
    result = step(tpl, state, "")
    assert result.error is not None
    assert state.field_idx == 0  # did not advance


def test_typing_value_on_context_loaded_field_overrides_to_user(tpl, state_with_prefill):
    first = tpl.fields[0]
    result = step(tpl, state_with_prefill, "My own answer")
    assert state_with_prefill.answers[first.id].value == "My own answer"
    assert state_with_prefill.answers[first.id].source == "user"
    assert state_with_prefill.field_idx == 1


def test_skip_on_context_loaded_field_marks_skipped(tpl, state_with_prefill):
    first = tpl.fields[0]
    step(tpl, state_with_prefill, "skip")
    assert state_with_prefill.answers[first.id].source == "skipped"
    assert state_with_prefill.field_idx == 1


# ---------------------------------------------------------------------------
# _render_state marker
# ---------------------------------------------------------------------------

def test_render_state_shows_context_marker(tpl, state_with_prefill):
    output = _render_state(state_with_prefill, tpl)
    first = tpl.fields[0]
    assert f"[C]" in output  # context_loaded marker


def test_render_state_shows_user_marker(tpl):
    state = new_state(tpl, run_id="run-4", project_id="proj-4")
    first = tpl.fields[0]
    state.answers[first.id] = FieldAnswer(value="user answer", source="user")
    output = _render_state(state, tpl)
    assert "[U]" in output


# ---------------------------------------------------------------------------
# _get_prefilled helper
# ---------------------------------------------------------------------------

def test_get_prefilled_returns_value_for_context_loaded(tpl, state_with_prefill):
    first = tpl.fields[0]
    val = _get_prefilled(state_with_prefill, first)
    assert val == "Pre-filled problem"


def test_get_prefilled_returns_none_for_user_answered(tpl):
    state = new_state(tpl, run_id="run-5", project_id="proj-5")
    first = tpl.fields[0]
    state.answers[first.id] = FieldAnswer(value="user", source="user")
    assert _get_prefilled(state, first) is None


def test_get_prefilled_returns_none_for_unanswered(tpl):
    state = new_state(tpl, run_id="run-6", project_id="proj-6")
    first = tpl.fields[0]
    assert _get_prefilled(state, first) is None


# ---------------------------------------------------------------------------
# to_brief_v2 serializes context_loaded
# ---------------------------------------------------------------------------

def test_to_brief_v2_serializes_context_loaded_source(tpl, state_with_prefill):
    brief = to_brief_v2(state_with_prefill, tpl, source_hash="abc123")
    first = tpl.fields[0]
    assert brief["fields"][first.id]["source"] == "context_loaded"
    assert brief["fields"][first.id]["value"] == "Pre-filled problem"
