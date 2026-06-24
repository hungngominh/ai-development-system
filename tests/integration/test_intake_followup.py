"""Intake wizard S4 integration: gap detection → FOLLOWUP → brief reflects decisions."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_dev_system.intake.runner import run_intake
from ai_dev_system.intake.template import load_template


def _trivial(fld) -> str:
    if fld.type == "list_str":
        return "a, b"
    if fld.type == "enum":
        return fld.options[0]
    if fld.type == "number":
        return "1"
    return "x"


def _script(answers: list[str]):
    """Linear script — feeds prompts in order, regardless of stage."""
    idx = {"i": 0}

    def fn(prompt: str) -> str:
        i = idx["i"]
        assert i < len(answers), (
            f"Ran out of scripted answers at idx={i}.\n"
            f"Last prompt:\n{prompt[:400]}"
        )
        idx["i"] += 1
        return answers[i]

    return fn


class StubLLM:
    """Routes by checking which `id: <field>` appears in the user message."""

    def __init__(self, replies_by_field: dict[str, str]):
        self.replies_by_field = dict(replies_by_field)
        self.calls = 0

    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        for fid, reply in self.replies_by_field.items():
            if f"id: {fid}" in user:
                return reply
        raise AssertionError(f"StubLLM got unexpected user message:\n{user[:500]}")


# ---------------------------------------------------------------------------
# Critical-blank followup
# ---------------------------------------------------------------------------

def test_followup_critical_blank_answered_recovers_field(conn, config):
    """Skip a critical field in ASKING → gap fires in FOLLOWUP → user answers → brief is complete."""
    tpl = load_template("generic_v1")

    # ASKING: skip success_metric (critical), trivial everything else
    asking = []
    for fld in tpl.fields:
        asking.append("skip" if fld.id == "success_metric" else _trivial(fld))

    # FOLLOWUP: real answer for the success_metric gap, then `enough` to skip any
    # remaining warnings (e.g. greenfield_vs_existing_auth) and reach CONFIRM.
    followup = ["80% adoption in 1 month", "enough"]

    answers = asking + followup + ["confirm"]

    result = run_intake(
        conn=conn, config=config, project_id="proj-fu-critical",
        prompt_fn=_script(answers),
    )

    assert result.status == "intake_complete"
    # success_metric was rescued during followup → no longer in critical_missing
    assert "success_metric" not in result.critical_missing

    art = conn.execute(
        "SELECT content_ref FROM artifacts WHERE artifact_id = ?", (result.brief_id,)
    ).fetchone()
    brief = json.loads((Path(art["content_ref"]) / "brief.json").read_text(encoding="utf-8"))
    sm = brief["fields"]["success_metric"]
    assert sm["value"] == "80% adoption in 1 month"
    assert sm["source"] == "user"

    # Audit should contain a followup_answered event for success_metric
    events = [e for e in brief["audit"] if e.get("event") == "followup_answered"]
    assert any(e.get("field") == "success_metric" for e in events)
    assert any(e.get("event") == "followup_started" for e in brief["audit"])


# ---------------------------------------------------------------------------
# `enough` escape hatch
# ---------------------------------------------------------------------------

def test_followup_enough_marks_remaining_gaps_as_assumptions(conn, config):
    """Multiple critical gaps + `enough` → remaining fields marked skipped and added to assumptions."""
    tpl = load_template("generic_v1")

    skipped_critical = {"success_metric", "primary_user", "current_workaround"}
    asking = [
        "skip" if fld.id in skipped_critical else _trivial(fld)
        for fld in tpl.fields
    ]
    # First followup prompt → `enough` (skips ALL remaining gaps).
    followup = ["enough"]

    answers = asking + followup + ["confirm"]

    result = run_intake(
        conn=conn, config=config, project_id="proj-fu-enough",
        prompt_fn=_script(answers),
    )

    assert result.status == "intake_complete"
    assert set(skipped_critical).issubset(set(result.critical_missing))

    art = conn.execute(
        "SELECT content_ref FROM artifacts WHERE artifact_id = ?", (result.brief_id,)
    ).fetchone()
    brief = json.loads((Path(art["content_ref"]) / "brief.json").read_text(encoding="utf-8"))

    # All skipped criticals appear in brief.assumptions
    for fid in skipped_critical:
        assert fid in brief["assumptions"], f"{fid} should be assumed"
        assert brief["fields"][fid]["source"] == "skipped"

    # Audit captures followup_assumed events for each field-targeting gap
    assumed = [e for e in brief["audit"] if e.get("event") == "followup_assumed"]
    assumed_fields = {e.get("field") for e in assumed}
    # At least the field-targeting gaps got logged (warnings have field=None)
    assert skipped_critical.issubset(assumed_fields)


# ---------------------------------------------------------------------------
# `?` from FOLLOWUP triggers SUGGESTING and returns
# ---------------------------------------------------------------------------

def test_followup_question_mark_routes_through_suggesting(conn, config):
    """`?` on a FOLLOWUP gap → enter SUGGESTING → accept → returns to FOLLOWUP and advances."""
    tpl = load_template("generic_v1")

    # Skip deployment_target (critical, ai_can_suggest=true) so it becomes a follow-up gap.
    asking = [
        "skip" if fld.id == "deployment_target" else _trivial(fld)
        for fld in tpl.fields
    ]
    # FOLLOWUP: `?` on the deployment_target gap, accept the suggestion, then `enough`
    # to bypass any remaining warning-only gaps.
    followup = ["?", "a", "enough"]

    answers = asking + followup + ["confirm"]

    llm = StubLLM({
        "deployment_target": '{"suggestion": "AWS ECS in ap-southeast-1",'
                             ' "rationale": "compliance + low ops"}',
    })

    result = run_intake(
        conn=conn, config=config, project_id="proj-fu-suggest",
        prompt_fn=_script(answers), llm=llm,
    )

    assert result.status == "intake_complete"
    assert llm.calls >= 1

    art = conn.execute(
        "SELECT content_ref FROM artifacts WHERE artifact_id = ?", (result.brief_id,)
    ).fetchone()
    brief = json.loads((Path(art["content_ref"]) / "brief.json").read_text(encoding="utf-8"))
    dt = brief["fields"]["deployment_target"]
    assert dt["value"] == "AWS ECS in ap-southeast-1"
    assert dt["source"] == "ai_suggested_confirmed"
    assert "compliance" in (dt.get("rationale") or "")


# ---------------------------------------------------------------------------
# Save during FOLLOWUP pauses with stage = FOLLOWUP
# ---------------------------------------------------------------------------

def test_save_during_followup_persists_pending_gaps(conn, config):
    tpl = load_template("generic_v1")

    asking = [
        "skip" if fld.id == "success_metric" else _trivial(fld)
        for fld in tpl.fields
    ]
    followup = ["save"]  # pause on first follow-up prompt

    answers = asking + followup

    result = run_intake(
        conn=conn, config=config, project_id="proj-fu-save",
        prompt_fn=_script(answers),
    )
    assert result.status == "intake_paused"
    assert result.brief_id is None

    row = conn.execute(
        "SELECT status, intake_state FROM runs WHERE run_id = ?", (result.run_id,)
    ).fetchone()
    assert row["status"] == "COLLECTING_INTAKE"
    state = json.loads(row["intake_state"])
    assert state["stage"] == "PAUSED"
    assert len(state["pending_gaps"]) >= 1
    assert any(g.get("target_field_id") == "success_metric"
               for g in state["pending_gaps"])


# ---------------------------------------------------------------------------
# INTAKE_FIELD_SUGGESTED event emitted when FOLLOWUP starts
# ---------------------------------------------------------------------------

def test_followup_entry_emits_event(conn, config):
    tpl = load_template("generic_v1")
    asking = [
        "skip" if fld.id == "success_metric" else _trivial(fld)
        for fld in tpl.fields
    ]
    answers = asking + ["enough", "confirm"]

    result = run_intake(
        conn=conn, config=config, project_id="proj-fu-event",
        prompt_fn=_script(answers),
    )
    assert result.status == "intake_complete"

    events = conn.execute(
        "SELECT event_type, payload FROM events WHERE run_id = ? ORDER BY occurred_at",
        (result.run_id,),
    ).fetchall()
    payloads = [
        (e["event_type"], json.loads(e["payload"] or "{}"))
        for e in events
    ]
    # The runner emits INTAKE_FIELD_SUGGESTED with stage=FOLLOWUP when intercepting
    assert any(
        et == "INTAKE_FIELD_SUGGESTED" and p.get("stage") == "FOLLOWUP"
        for et, p in payloads
    )
