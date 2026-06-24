"""Engine FOLLOWUP state transitions — pure, no I/O."""
from __future__ import annotations

import pytest

from ai_dev_system.intake.engine import (
    FieldAnswer,
    enter_followup,
    new_state,
    start,
    step,
)
from ai_dev_system.intake.template import load_template


@pytest.fixture
def tpl():
    return load_template("generic_v1")


@pytest.fixture
def state(tpl):
    return new_state(tpl, "r1", "p1")


def _gap_field(target: str, msg: str = "blank!") -> dict:
    return {
        "kind": "critical_blank",
        "message": msg,
        "target_field_id": target,
        "rule_id": None,
        "hint": None,
    }


def _gap_warning(rule_id: str = "scope_vs_deadline", msg: str = "scope too big") -> dict:
    return {
        "kind": "scope_mismatch",
        "message": msg,
        "target_field_id": None,
        "rule_id": rule_id,
        "hint": None,
    }


# ---------------------------------------------------------------------------
# enter_followup
# ---------------------------------------------------------------------------

def test_enter_followup_sets_stage_and_gaps(state):
    enter_followup(state, [_gap_field("scope_in")])
    assert state.stage == "FOLLOWUP"
    assert len(state.pending_gaps) == 1
    assert state.followup_idx == 0


def test_enter_followup_noop_on_empty_list(state):
    enter_followup(state, [])
    assert state.stage == "ASKING"
    assert state.pending_gaps == []


# ---------------------------------------------------------------------------
# Field-targeting gap: answer / skip / `?` / back
# ---------------------------------------------------------------------------

def test_followup_field_gap_renders_field_prompt(tpl, state):
    enter_followup(state, [_gap_field("scope_in", "scope_in chưa có")])
    r = start(tpl, state)
    assert "Follow-up 1/1" in r.prompt
    assert "scope_in" in r.prompt
    assert r.current_field is not None
    assert r.current_field.id == "scope_in"


def test_followup_text_answer_records_user_and_advances(tpl, state):
    enter_followup(state, [_gap_field("scope_in"), _gap_field("success_metric")])
    r = step(tpl, state, "search, browse")
    assert state.answers["scope_in"].value == ["search", "browse"]
    assert state.answers["scope_in"].source == "user"
    assert state.followup_idx == 1
    assert "success_metric" in r.prompt


def test_followup_skip_records_skipped_and_advances(tpl, state):
    enter_followup(state, [_gap_field("scope_in"), _gap_field("success_metric")])
    step(tpl, state, "skip")
    assert state.answers["scope_in"].source == "skipped"
    assert state.followup_idx == 1


def test_followup_answer_after_last_gap_transitions_to_confirm(tpl, state):
    enter_followup(state, [_gap_field("scope_in")])
    r = step(tpl, state, "search")
    assert state.stage == "CONFIRM"
    assert "confirm" in r.prompt.lower()


def test_followup_question_mark_calls_suggest(tpl, state):
    calls = []
    def fn(fld, answers):
        calls.append(fld.id)
        return {"suggestion": "AWS", "rationale": "compliance"}

    enter_followup(state, [_gap_field("deployment_target")])
    r = step(tpl, state, "?", suggest_fn=fn)
    assert state.stage == "SUGGESTING"
    assert state.suggesting_return_stage == "FOLLOWUP"
    assert calls == ["deployment_target"]
    # Accept the suggestion → returns to FOLLOWUP and advances past the gap
    r2 = step(tpl, state, "a", suggest_fn=fn)
    assert state.stage == "CONFIRM"  # no more gaps
    assert state.answers["deployment_target"].source == "ai_suggested_confirmed"


def test_followup_question_mark_back_returns_to_followup(tpl, state):
    def fn(fld, answers):
        return {"suggestion": "AWS", "rationale": "x"}

    enter_followup(state, [_gap_field("deployment_target")])
    step(tpl, state, "?", suggest_fn=fn)
    r = step(tpl, state, "back", suggest_fn=fn)
    assert state.stage == "FOLLOWUP"
    assert state.followup_idx == 0  # still on the same gap


def test_followup_question_mark_on_refuse_list_falls_back(tpl, state):
    enter_followup(state, [_gap_field("problem_statement")])
    r = step(tpl, state, "?", suggest_fn=lambda *a, **k: {"suggestion": "x", "rationale": "y"})
    assert state.stage == "FOLLOWUP"  # didn't enter SUGGESTING
    assert r.error == "ai_cannot_suggest"


# ---------------------------------------------------------------------------
# Warning gap (no target field): continue / edit / enough
# ---------------------------------------------------------------------------

def test_followup_warning_continue_advances(tpl, state):
    enter_followup(state, [_gap_warning(), _gap_field("scope_in")])
    r = step(tpl, state, "continue")
    assert state.followup_idx == 1
    assert "scope_in" in r.prompt


def test_followup_warning_only_no_field_no_answer(tpl, state):
    enter_followup(state, [_gap_warning()])
    r = step(tpl, state, "random text not a command")
    assert r.error == "bad_followup_cmd"
    assert state.followup_idx == 0


def test_followup_edit_field_jumps_to_asking(tpl, state):
    enter_followup(state, [_gap_warning(), _gap_field("scope_in")])
    r = step(tpl, state, "edit scope_in")
    assert state.stage == "ASKING"
    assert r.current_field.id == "scope_in"


def test_followup_edit_unknown_field_errors(tpl, state):
    enter_followup(state, [_gap_warning()])
    r = step(tpl, state, "edit not_real")
    assert r.error == "unknown_field"
    assert state.stage == "FOLLOWUP"


# ---------------------------------------------------------------------------
# `enough` escape hatch
# ---------------------------------------------------------------------------

def test_enough_records_remaining_as_assumptions_and_confirms(tpl, state):
    enter_followup(state, [
        _gap_field("scope_in"),
        _gap_field("success_metric"),
        _gap_warning(),
    ])
    r = step(tpl, state, "enough")
    assert state.stage == "CONFIRM"
    # Field-targeting gaps got their fields set to skipped
    assert state.answers["scope_in"].source == "skipped"
    assert state.answers["success_metric"].source == "skipped"


def test_enough_after_some_answers_only_skips_remaining(tpl, state):
    enter_followup(state, [
        _gap_field("scope_in"),
        _gap_field("success_metric"),
    ])
    step(tpl, state, "search")  # answer first
    r = step(tpl, state, "enough")
    assert state.stage == "CONFIRM"
    assert state.answers["scope_in"].value == ["search"]
    assert state.answers["scope_in"].source == "user"
    assert state.answers["success_metric"].source == "skipped"


# ---------------------------------------------------------------------------
# save / show during FOLLOWUP
# ---------------------------------------------------------------------------

def test_save_during_followup_pauses(tpl, state):
    enter_followup(state, [_gap_field("scope_in")])
    r = step(tpl, state, "save")
    assert r.terminal
    assert r.terminal_reason == "paused"
    assert state.stage == "PAUSED"


def test_show_during_followup_keeps_position(tpl, state):
    enter_followup(state, [_gap_field("scope_in")])
    r = step(tpl, state, "show")
    assert state.followup_idx == 0
    assert "Current brief" in r.prompt


# ---------------------------------------------------------------------------
# back at first gap
# ---------------------------------------------------------------------------

def test_back_from_first_gap_errors(tpl, state):
    enter_followup(state, [_gap_field("scope_in")])
    r = step(tpl, state, "back")
    assert r.error == "cannot_back_from_first"
    assert state.followup_idx == 0


def test_back_returns_to_previous_gap(tpl, state):
    enter_followup(state, [_gap_field("scope_in"), _gap_field("success_metric")])
    step(tpl, state, "search")            # advance to gap 2
    assert state.followup_idx == 1
    r = step(tpl, state, "back")
    assert state.followup_idx == 0


# ---------------------------------------------------------------------------
# Serialization roundtrip including pending_gaps
# ---------------------------------------------------------------------------

def test_state_with_followup_roundtrips(state, tpl):
    enter_followup(state, [_gap_field("scope_in"), _gap_warning()])
    state.followup_idx = 1
    raw = state.to_json()
    from ai_dev_system.intake.engine import IntakeState
    restored = IntakeState.from_json(raw)
    assert restored.stage == "FOLLOWUP"
    assert restored.followup_idx == 1
    assert len(restored.pending_gaps) == 2
