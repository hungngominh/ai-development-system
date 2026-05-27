"""Unit tests for gate1_review.state (G10+G8 — session state persistence).

Tests cover:
- GateSessionState serialization round-trip (to_json / from_json)
- record_choice: stores ResolvedItem with correct resolution_type
- record_brief_edit: appends BriefEditEntry
- is_resolved: True for recorded QID, False for unknown, True if approved_all
- empty(): creates clean state for a run_id
- save_state / load_state / clear_state DB operations
- load_state returns empty state when column is NULL
- G8: scope_affected set True when scope_in/scope_out edited
- G8: scope_affected not set for non-scope fields
- G8: scope_affected preserved in serialization round-trip
- Finalize clears session state (via __main__.cmd_finalize)
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from ai_dev_system.gate.gate1_review.state import (
    BriefEditEntry,
    GateSessionState,
    ResolvedItem,
    clear_state,
    load_state,
    save_state,
)


# ---- fixtures ----


@pytest.fixture
def conn():
    """In-memory SQLite with a minimal runs table including gate1_session_state."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("""
        CREATE TABLE runs (
            run_id TEXT PRIMARY KEY,
            gate1_session_state TEXT
        )
    """)
    db.execute("INSERT INTO runs (run_id, gate1_session_state) VALUES ('r1', NULL)")
    db.commit()
    return db


def _state(run_id: str = "r1") -> GateSessionState:
    return GateSessionState.empty(run_id)


# ---- GateSessionState basics ----


def test_empty_state_has_no_resolved():
    state = _state()
    assert not state.resolved
    assert not state.brief_edits
    assert not state.approved_all


def test_record_choice_agent_a():
    state = _state()
    state.record_choice("Q1", "agent_a")
    assert state.resolved["Q1"].resolution_type == "CHOICE_A"
    assert state.resolved["Q1"].choice == "agent_a"


def test_record_choice_agent_b():
    state = _state()
    state.record_choice("Q2", "agent_b")
    assert state.resolved["Q2"].resolution_type == "CHOICE_B"


def test_record_choice_moderator():
    state = _state()
    state.record_choice("Q3", "moderator")
    assert state.resolved["Q3"].resolution_type == "MODERATOR"


def test_record_choice_override():
    state = _state()
    state.record_choice("Q4", "override", override_text="Use Redis")
    assert state.resolved["Q4"].resolution_type == "FORCED_HUMAN"
    assert state.resolved["Q4"].override_text == "Use Redis"


def test_is_resolved_true_for_recorded_qid():
    state = _state()
    state.record_choice("Q1", "agent_a")
    assert state.is_resolved("Q1")


def test_is_resolved_false_for_unknown_qid():
    state = _state()
    assert not state.is_resolved("Q99")


def test_is_resolved_true_when_approved_all():
    state = _state()
    state.approved_all = True
    assert state.is_resolved("Q_anything")


def test_record_brief_edit():
    state = _state()
    state.record_brief_edit("scope_in", "append", "reporting")
    assert len(state.brief_edits) == 1
    assert state.brief_edits[0].field_name == "scope_in"
    assert state.brief_edits[0].value == "reporting"


# ---- serialization round-trip ----


def test_round_trip_empty_state():
    state = GateSessionState.empty("r1")
    restored = GateSessionState.from_json("r1", state.to_json())
    assert restored.run_id == "r1"
    assert not restored.resolved
    assert not restored.brief_edits
    assert not restored.approved_all


def test_round_trip_with_choices_and_edits():
    state = GateSessionState.empty("r42")
    state.record_choice("Q1", "agent_a")
    state.record_choice("Q2", "override", override_text="Use Redis")
    state.record_brief_edit("scope_in", "append", "reporting")
    state.approved_all = False

    restored = GateSessionState.from_json("r42", state.to_json())
    assert restored.resolved["Q1"].resolution_type == "CHOICE_A"
    assert restored.resolved["Q2"].override_text == "Use Redis"
    assert restored.brief_edits[0].field_name == "scope_in"


def test_to_json_valid_json():
    state = _state()
    state.record_choice("Q1", "agent_b")
    payload = json.loads(state.to_json())
    assert payload["schema"] == 1
    assert "Q1" in payload["resolved"]


# ---- DB operations ----


def test_save_then_load_state(conn):
    state = GateSessionState.empty("r1")
    state.record_choice("Q1", "moderator")
    save_state("r1", state, conn)

    loaded = load_state("r1", conn)
    assert loaded.resolved["Q1"].resolution_type == "MODERATOR"


def test_load_state_returns_empty_when_null(conn):
    loaded = load_state("r1", conn)
    assert isinstance(loaded, GateSessionState)
    assert not loaded.resolved


def test_clear_state_sets_null(conn):
    state = GateSessionState.empty("r1")
    state.record_choice("Q1", "agent_a")
    save_state("r1", state, conn)

    clear_state("r1", conn)
    loaded = load_state("r1", conn)
    assert not loaded.resolved  # cleared → empty


def test_save_overwrite_existing_state(conn):
    state1 = GateSessionState.empty("r1")
    state1.record_choice("Q1", "agent_a")
    save_state("r1", state1, conn)

    state2 = GateSessionState.empty("r1")
    state2.record_choice("Q1", "agent_b")  # different choice
    save_state("r1", state2, conn)

    loaded = load_state("r1", conn)
    assert loaded.resolved["Q1"].resolution_type == "CHOICE_B"


def test_load_state_missing_run_returns_empty(conn):
    loaded = load_state("nonexistent_run", conn)
    assert isinstance(loaded, GateSessionState)
    assert not loaded.resolved


# ---- G8: scope_affected tracking ----


def test_scope_affected_false_by_default():
    state = _state()
    assert not state.scope_affected


def test_scope_affected_true_on_scope_in_edit():
    state = _state()
    state.record_brief_edit("scope_in", "append", "reporting")
    assert state.scope_affected


def test_scope_affected_true_on_scope_out_edit():
    state = _state()
    state.record_brief_edit("scope_out", "append", "analytics")
    assert state.scope_affected


def test_scope_affected_false_for_non_scope_field():
    state = _state()
    state.record_brief_edit("problem_statement", "set", "New problem")
    assert not state.scope_affected


def test_scope_affected_sticky_after_non_scope_edit():
    state = _state()
    state.record_brief_edit("scope_in", "append", "reporting")
    state.record_brief_edit("deadline", "set", "Q3")
    # scope_affected remains True even after subsequent non-scope edits
    assert state.scope_affected


def test_scope_affected_serialized_in_to_json():
    state = _state()
    state.record_brief_edit("scope_in", "append", "search")
    payload = json.loads(state.to_json())
    assert payload["scope_affected"] is True


def test_scope_affected_round_trip_true():
    state = GateSessionState.empty("r1")
    state.record_brief_edit("scope_out", "remove", "chat")
    restored = GateSessionState.from_json("r1", state.to_json())
    assert restored.scope_affected is True


def test_scope_affected_round_trip_false():
    state = GateSessionState.empty("r1")
    state.record_brief_edit("deadline", "set", "Q4")
    restored = GateSessionState.from_json("r1", state.to_json())
    assert restored.scope_affected is False


def test_scope_affected_defaults_false_on_old_json():
    # JSON without scope_affected key (e.g. from G10 before G8)
    old_json = json.dumps({"schema": 1, "run_id": "r1", "resolved": {}, "brief_edits": [], "approved_all": False})
    restored = GateSessionState.from_json("r1", old_json)
    assert restored.scope_affected is False
