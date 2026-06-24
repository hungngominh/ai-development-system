"""Intake wizard integration tests — full runner + DB + artifact promotion."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_dev_system.intake.runner import run_intake
from ai_dev_system.intake.template import load_template


def _scripted_prompt(answers: list[str]):
    """Build a prompt_fn that returns answers[idx] in order.

    If the engine enters FOLLOWUP (gap detection found something), this helper
    sends `enough` to skip the follow-up stage in one shot. S1+S2 tests treat
    FOLLOWUP as transparent; S4 has dedicated tests that exercise it.
    """
    idx = {"i": 0}

    def fn(prompt: str) -> str:
        if "Follow-up" in prompt:
            return "enough"
        i = idx["i"]
        assert i < len(answers), f"Prompt ran past scripted answers (idx={i}): {prompt[:80]}"
        idx["i"] += 1
        return answers[i]

    return fn


def _trivial_answer(fld) -> str:
    if fld.type == "list_str":
        return "a, b"
    if fld.type == "enum":
        return fld.options[0]
    if fld.type == "number":
        return "1"
    return "x"


def test_full_intake_completes_and_promotes_brief(conn, config):
    template = load_template("generic_v1")
    # Build a script: one answer per field + "confirm" at end
    answers = [_trivial_answer(f) for f in template.fields] + ["confirm"]

    result = run_intake(
        conn=conn, config=config, project_id="proj-1",
        prompt_fn=_scripted_prompt(answers),
    )

    assert result.status == "intake_complete"
    assert result.brief_id is not None
    assert result.fields_answered == len(template.fields)
    assert result.critical_missing == []

    # DB invariants
    row = conn.execute(
        "SELECT status, intake_brief_id, intake_state FROM runs WHERE run_id = ?",
        (result.run_id,),
    ).fetchone()
    assert row["status"] == "READY_FOR_DEBATE"
    assert row["intake_brief_id"] == result.brief_id
    assert row["intake_state"] is None  # cleared on promote

    # Artifact on disk
    art = conn.execute(
        "SELECT artifact_type, content_ref FROM artifacts WHERE artifact_id = ?",
        (result.brief_id,),
    ).fetchone()
    assert art["artifact_type"] == "INTAKE_BRIEF"
    brief = json.loads((Path(art["content_ref"]) / "brief.json").read_text(encoding="utf-8"))
    assert brief["brief_version"] == 2
    assert brief["template_id"] == "generic_v1"
    assert brief["source_hash"]


def test_save_pauses_and_keeps_state(conn, config):
    """User typing `save` after 2 answers → status intake_paused, intake_state persists."""
    template = load_template("generic_v1")
    answers = [_trivial_answer(template.fields[0]),
               _trivial_answer(template.fields[1]),
               "save"]

    result = run_intake(
        conn=conn, config=config, project_id="proj-2",
        prompt_fn=_scripted_prompt(answers),
    )

    assert result.status == "intake_paused"
    assert result.brief_id is None
    assert result.fields_answered == 2

    row = conn.execute(
        "SELECT status, intake_state FROM runs WHERE run_id = ?", (result.run_id,)
    ).fetchone()
    assert row["status"] == "COLLECTING_INTAKE"
    state = json.loads(row["intake_state"])
    assert state["stage"] == "PAUSED"
    assert state["field_idx"] == 2  # paused right after 2 answers


def test_skip_critical_records_assumption(conn, config):
    """Skipping `problem_statement` (critical) shows up in critical_missing + brief.assumptions."""
    template = load_template("generic_v1")
    answers = []
    for fld in template.fields:
        answers.append("skip" if fld.id == "problem_statement" else _trivial_answer(fld))
    answers.append("confirm")

    result = run_intake(
        conn=conn, config=config, project_id="proj-3",
        prompt_fn=_scripted_prompt(answers),
    )

    assert result.status == "intake_complete"
    assert "problem_statement" in result.critical_missing

    art = conn.execute(
        "SELECT content_ref FROM artifacts WHERE artifact_id = ?", (result.brief_id,)
    ).fetchone()
    brief = json.loads((Path(art["content_ref"]) / "brief.json").read_text(encoding="utf-8"))
    assert "problem_statement" in brief["assumptions"]
    assert brief["fields"]["problem_statement"]["source"] == "skipped"


def test_resume_after_save_completes_from_paused_field(conn, config):
    """Save after 2 fields, resume the same run_id, finish all remaining fields → brief promoted."""
    template = load_template("generic_v1")

    # Phase 1: save after 2 answers
    pause_answers = [_trivial_answer(template.fields[0]),
                     _trivial_answer(template.fields[1]),
                     "save"]
    paused = run_intake(
        conn=conn, config=config, project_id="proj-resume",
        prompt_fn=_scripted_prompt(pause_answers),
    )
    assert paused.status == "intake_paused"
    run_id = paused.run_id

    # Phase 2: resume on the same run_id, answer fields[2:] + confirm.
    # The runner reads state.field_idx (=2) on load so the script only covers
    # the remaining fields.
    remaining = [_trivial_answer(f) for f in template.fields[2:]] + ["confirm"]
    resumed = run_intake(
        conn=conn, config=config, project_id="proj-resume",
        prompt_fn=_scripted_prompt(remaining),
        run_id=run_id,
    )

    assert resumed.status == "intake_complete"
    assert resumed.run_id == run_id
    assert resumed.brief_id is not None
    # All fields answered: the 2 from phase 1 + the rest from phase 2.
    assert resumed.fields_answered == len(template.fields)

    row = conn.execute(
        "SELECT status, intake_brief_id, intake_state FROM runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    assert row["status"] == "READY_FOR_DEBATE"
    assert row["intake_brief_id"] == resumed.brief_id
    assert row["intake_state"] is None  # cleared on promote

    # Audit event INTAKE_RESUMED is recorded
    types = [
        e["event_type"]
        for e in conn.execute(
            "SELECT event_type FROM events WHERE run_id = ? ORDER BY occurred_at",
            (run_id,),
        ).fetchall()
    ]
    assert "INTAKE_RESUMED" in types
    assert types.count("INTAKE_STARTED") == 1  # not re-emitted on resume


class _NonClosingConn:
    """Proxy around a sqlite3.Connection that no-ops `close()` so an in-memory
    DB survives across multiple CLI invocations that each call conn.close()."""

    def __init__(self, conn):
        self._c = conn

    def close(self):
        return None

    def __getattr__(self, name):
        return getattr(self._c, name)


def _patch_cli_db(monkeypatch, conn, config):
    """Make the CLI commands reuse our in-memory `conn` instead of opening a new one."""
    proxy = _NonClosingConn(conn)
    monkeypatch.setattr("ai_dev_system.config.Config.from_env", staticmethod(lambda: config))
    monkeypatch.setattr(
        "ai_dev_system.db.connection.get_connection", lambda _url: proxy
    )
    monkeypatch.setattr("ai_dev_system.db.migrator.apply_schema", lambda _c: None)


def test_intake_abort_via_cli_marks_run_aborted_and_clears_state(conn, config, monkeypatch):
    """`ai-dev intake abort` flips status to ABORTED, drops intake_state, emits event."""
    import typer

    from ai_dev_system.cli.commands.intake import intake_abort

    template = load_template("generic_v1")
    paused = run_intake(
        conn=conn, config=config, project_id="proj-abort",
        prompt_fn=_scripted_prompt([_trivial_answer(template.fields[0]), "save"]),
    )
    run_id = paused.run_id

    _patch_cli_db(monkeypatch, conn, config)

    with pytest.raises(typer.Exit) as exc_info:
        intake_abort(run_id=run_id, reason="test_abort", json_output=True)
    assert exc_info.value.exit_code == 0

    row = conn.execute(
        "SELECT status, intake_state FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    assert row["status"] == "ABORTED"
    assert row["intake_state"] is None

    types = [
        e["event_type"]
        for e in conn.execute(
            "SELECT event_type, payload FROM events WHERE run_id = ?", (run_id,)
        ).fetchall()
    ]
    assert "INTAKE_ABORTED" in types
    abort_evt = conn.execute(
        "SELECT payload FROM events WHERE run_id = ? AND event_type = 'INTAKE_ABORTED'",
        (run_id,),
    ).fetchone()
    assert json.loads(abort_evt["payload"])["reason"] == "test_abort"


def test_intake_abort_rejects_non_collecting_run(conn, config, monkeypatch):
    """Abort refuses to touch a run that already moved past COLLECTING_INTAKE."""
    import typer

    from ai_dev_system.cli.commands.intake import intake_abort

    template = load_template("generic_v1")
    answers = [_trivial_answer(f) for f in template.fields] + ["confirm"]
    done = run_intake(
        conn=conn, config=config, project_id="proj-abort-2",
        prompt_fn=_scripted_prompt(answers),
    )
    assert done.status == "intake_complete"  # status == READY_FOR_DEBATE now

    _patch_cli_db(monkeypatch, conn, config)

    with pytest.raises(typer.Exit) as exc_info:
        intake_abort(run_id=done.run_id, reason="user_requested", json_output=True)
    assert exc_info.value.exit_code == 1

    row = conn.execute(
        "SELECT status FROM runs WHERE run_id = ?", (done.run_id,)
    ).fetchone()
    assert row["status"] == "READY_FOR_DEBATE"  # untouched


def test_intake_completed_event_emitted(conn, config):
    template = load_template("generic_v1")
    answers = [_trivial_answer(f) for f in template.fields] + ["confirm"]
    result = run_intake(
        conn=conn, config=config, project_id="proj-4",
        prompt_fn=_scripted_prompt(answers),
    )
    events = conn.execute(
        "SELECT event_type FROM events WHERE run_id = ? ORDER BY occurred_at",
        (result.run_id,),
    ).fetchall()
    types = [e["event_type"] for e in events]
    assert "INTAKE_STARTED" in types
    assert "INTAKE_COMPLETED" in types
