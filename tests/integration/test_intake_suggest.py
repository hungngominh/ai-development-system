"""Intake wizard S3 integration: full ?-flow with stub LLM."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_dev_system.intake.runner import run_intake
from ai_dev_system.intake.template import load_template


class StubLLM:
    """Returns canned JSON per (field_id) cycle."""
    def __init__(self, replies_by_field: dict[str, str]):
        self.replies_by_field = dict(replies_by_field)
        self.calls = 0

    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        # Find which field id appears in the user message (case-insensitive substring)
        for fid, reply in self.replies_by_field.items():
            if f"id: {fid}" in user:
                return reply
        raise AssertionError(f"StubLLM got unexpected user message:\n{user[:500]}")


def _trivial(fld):
    if fld.type == "list_str":
        return "a, b"
    if fld.type == "enum":
        return fld.options[0]
    if fld.type == "number":
        return "1"
    return "x"


def _scripted(answers: list[str]):
    """Scripted prompt that auto-bypasses FOLLOWUP with `enough`."""
    idx = {"i": 0}
    def fn(prompt: str) -> str:
        if "Follow-up" in prompt:
            return "enough"
        i = idx["i"]
        assert i < len(answers), f"Out of scripted answers at idx={i}"
        idx["i"] += 1
        return answers[i]
    return fn


def test_suggest_question_mark_accepts_proposal(conn, config):
    """User types `?` on deployment_target → accepts → recorded as ai_suggested_confirmed."""
    tpl = load_template("generic_v1")
    answers = []
    for fld in tpl.fields:
        if fld.id == "deployment_target":
            answers.extend(["?", "a"])  # ask LLM, then accept
        else:
            answers.append(_trivial(fld))
    answers.append("confirm")

    llm = StubLLM({
        "deployment_target": '{"suggestion": "AWS ECS", "rationale": "compliance + low ops"}',
    })

    result = run_intake(
        conn=conn, config=config, project_id="proj-suggest-1",
        prompt_fn=_scripted(answers), llm=llm,
    )

    assert result.status == "intake_complete"
    assert llm.calls == 1

    # Verify brief
    art = conn.execute(
        "SELECT content_ref FROM artifacts WHERE artifact_id = ?", (result.brief_id,)
    ).fetchone()
    brief = json.loads((Path(art["content_ref"]) / "brief.json").read_text(encoding="utf-8"))
    dt = brief["fields"]["deployment_target"]
    assert dt["value"] == "AWS ECS"
    assert dt["source"] == "ai_suggested_confirmed"
    assert "compliance" in dt["rationale"]


def test_suggest_replace_keeps_user_source(conn, config):
    """User types `?` then `b <own value>` → recorded as user, not ai_confirmed."""
    tpl = load_template("generic_v1")
    answers = []
    for fld in tpl.fields:
        if fld.id == "deployment_target":
            answers.extend(["?", "b on-prem k8s"])
        else:
            answers.append(_trivial(fld))
    answers.append("confirm")

    llm = StubLLM({
        "deployment_target": '{"suggestion": "AWS", "rationale": "x"}',
    })

    result = run_intake(
        conn=conn, config=config, project_id="proj-suggest-2",
        prompt_fn=_scripted(answers), llm=llm,
    )
    assert result.status == "intake_complete"

    art = conn.execute(
        "SELECT content_ref FROM artifacts WHERE artifact_id = ?", (result.brief_id,)
    ).fetchone()
    brief = json.loads((Path(art["content_ref"]) / "brief.json").read_text(encoding="utf-8"))
    dt = brief["fields"]["deployment_target"]
    assert dt["value"] == "on-prem k8s"
    assert dt["source"] == "user"


def test_suggest_emits_field_suggested_event(conn, config):
    tpl = load_template("generic_v1")
    answers = []
    for fld in tpl.fields:
        if fld.id == "deployment_target":
            answers.extend(["?", "a"])
        else:
            answers.append(_trivial(fld))
    answers.append("confirm")

    llm = StubLLM({
        "deployment_target": '{"suggestion": "AWS", "rationale": "x"}',
    })
    result = run_intake(
        conn=conn, config=config, project_id="proj-suggest-3",
        prompt_fn=_scripted(answers), llm=llm,
    )

    events = conn.execute(
        "SELECT event_type, payload FROM events WHERE run_id = ? ORDER BY occurred_at",
        (result.run_id,),
    ).fetchall()
    types = [e["event_type"] for e in events]
    assert "INTAKE_FIELD_SUGGESTED" in types


def test_no_llm_flag_disables_suggest(conn, config):
    """When llm=None, `?` falls back to ASKING with 'no_suggest_fn' error message."""
    tpl = load_template("generic_v1")
    answers = []
    for fld in tpl.fields:
        if fld.id == "deployment_target":
            # `?` is rejected → engine reprompts → then we provide a real answer
            answers.extend(["?", "AWS"])
        else:
            answers.append(_trivial(fld))
    answers.append("confirm")

    result = run_intake(
        conn=conn, config=config, project_id="proj-no-llm",
        prompt_fn=_scripted(answers), llm=None,
    )
    assert result.status == "intake_complete"

    art = conn.execute(
        "SELECT content_ref FROM artifacts WHERE artifact_id = ?", (result.brief_id,)
    ).fetchone()
    brief = json.loads((Path(art["content_ref"]) / "brief.json").read_text(encoding="utf-8"))
    # Recorded as user-typed since `?` was rejected
    assert brief["fields"]["deployment_target"]["value"] == "AWS"
    assert brief["fields"]["deployment_target"]["source"] == "user"


def test_refused_field_question_mark_falls_back(conn, config):
    """`?` on a critical refuse-list field (problem_statement) → reprompt, then user answers."""
    tpl = load_template("generic_v1")
    answers = []
    for fld in tpl.fields:
        if fld.id == "problem_statement":
            answers.extend(["?", "Real problem"])
        else:
            answers.append(_trivial(fld))
    answers.append("confirm")

    # Use llm=None so neither suggest nor ambiguity scoring triggers.
    result = run_intake(
        conn=conn, config=config, project_id="proj-refuse",
        prompt_fn=_scripted(answers), llm=None,
    )
    assert result.status == "intake_complete"

    art = conn.execute(
        "SELECT content_ref FROM artifacts WHERE artifact_id = ?", (result.brief_id,)
    ).fetchone()
    brief = json.loads((Path(art["content_ref"]) / "brief.json").read_text(encoding="utf-8"))
    ps = brief["fields"]["problem_statement"]
    assert ps["value"] == "Real problem"
    assert ps["source"] == "user"
