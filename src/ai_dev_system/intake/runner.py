"""Intake runner — glue between the engine state machine, DB, and a prompt I/O.

The runner is the orchestrator the CLI calls. It owns:
- creating/loading a run in COLLECTING_INTAKE status
- the read/save loop around `engine.step`
- promoting the brief when the user confirms

The actual prompt I/O is injected as `prompt_fn(prompt: str) -> str`, so unit
tests can feed scripted inputs and the CLI can wire stdin.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from ai_dev_system.config import Config
from ai_dev_system.db.helpers import dump_json, new_uuid
from ai_dev_system.db.repos.events import EventRepo
from ai_dev_system.intake.brief import promote_intake_brief
from ai_dev_system.intake.engine import (
    FieldAnswer, IntakeState, SuggestFn,
    asking_completed, enter_followup, new_state, start, step,
)
from ai_dev_system.intake.followup import detect_gaps
from ai_dev_system.intake.repo import IntakeRepo
from ai_dev_system.intake.suggest import LLMClient, Suggester
from ai_dev_system.intake.template import Template, load_template


PromptFn = Callable[[str], str]


def _build_suggest_fn(template: Template, llm: LLMClient | None) -> SuggestFn | None:
    """Wrap a Suggester into the (field, answers) → dict callable the engine expects.

    Returns None if no LLM was provided — the engine will then show a fallback
    message when the user types `?`. One Suggester per call so the cache is
    scoped to a single wizard run (decision #1).
    """
    if llm is None:
        return None
    sug = Suggester(llm)

    def _fn(fld, answers):
        proposal = sug.propose(template, fld, answers)
        return {
            "suggestion": proposal.suggestion,
            "rationale": proposal.rationale,
            "cache_hit": proposal.cache_hit,
        }
    return _fn


@dataclass
class IntakeResult:
    run_id: str
    status: str       # "intake_complete" | "intake_paused" | "intake_aborted"
    brief_id: Optional[str] = None
    fields_answered: int = 0
    critical_missing: list[str] = None  # type: ignore[assignment]


def _ensure_run(conn: sqlite3.Connection, project_id: str, run_id: Optional[str]) -> str:
    """Create a new run in COLLECTING_INTAKE or verify existing one."""
    if run_id is not None:
        row = conn.execute("SELECT status FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise ValueError(f"Run {run_id!r} not found")
        return run_id

    rid = new_uuid()
    initial_artifacts = {
        "initial_brief_id": None, "intake_brief_id": None,
        "debate_report_id": None, "decision_log_id": None,
        "approved_answers_id": None, "approved_brief_id": None,
        "spec_bundle_id": None, "task_graph_gen_id": None,
        "task_graph_approved_id": None,
    }
    conn.execute(
        """
        INSERT INTO runs (run_id, project_id, status, title, current_artifacts, metadata)
        VALUES (?, ?, 'COLLECTING_INTAKE', ?, ?, '{}')
        """,
        (rid, project_id, "Intake wizard", dump_json(initial_artifacts)),
    )
    EventRepo(conn).insert(rid, "INTAKE_STARTED", "intake_wizard", payload={"project_id": project_id})
    return rid


def run_intake(
    conn: sqlite3.Connection,
    config: Config,
    project_id: str,
    prompt_fn: PromptFn,
    template_id: str = "generic_v1",
    run_id: Optional[str] = None,
    intro_writer: Optional[Callable[[str], None]] = None,
    llm: LLMClient | None = None,
    context_dir: Optional[str] = None,
) -> IntakeResult:
    """Run the intake wizard to completion or pause.

    Args:
        conn:          open SQLite connection (caller owns lifetime)
        config:        Config (used by brief promoter for storage paths)
        project_id:    project to attach the new run to
        prompt_fn:     called once per turn; receives the engine prompt, returns user input
        template_id:   intake template to load
        run_id:        if provided, resume (else create new run)
        intro_writer:  optional callable for stderr-style intro messages (no return value)

    Returns:
        IntakeResult with outcome status + brief_id (if promoted).
    """
    template = load_template(template_id)
    intake_repo = IntakeRepo(conn)
    event_repo = EventRepo(conn)

    rid = _ensure_run(conn, project_id, run_id)
    conn.commit()

    state = intake_repo.load_state(rid) if run_id else None
    if state is None:
        state = new_state(template, run_id=rid, project_id=project_id)
        if context_dir:
            _apply_context_dir(state, template, context_dir, llm, intro_writer)
        intake_repo.save_state(state)
        conn.commit()
    elif state.stage == "PAUSED":
        # Resume: flip back to ASKING so the engine loop progresses normally.
        # The field_idx already points at the question the user paused on.
        state.stage = "ASKING"
        event_repo.insert(rid, "INTAKE_RESUMED", "intake_wizard",
                          payload={"field_idx": state.field_idx})

    if intro_writer:
        intro_writer(
            f"Intake started for run {rid}.\n"
            f"Template: {template.id} v{template.version} ({len(template.fields)} fields, "
            f"{len(template.critical_field_ids)} critical).\n"
            f"Commands: skip / back / save / show. `save` để tạm dừng, resume sau.\n"
        )

    suggest_fn = _build_suggest_fn(template, llm)

    def _maybe_intercept_for_followup(result):
        """If engine just landed on CONFIRM for the first time, detect gaps and
        possibly switch to FOLLOWUP. Returns a (possibly replaced) result.

        `state.pending_gaps` is the sentinel: empty means we haven't yet done
        gap detection for this confirm transition.
        """
        if state.stage != "CONFIRM" or state.pending_gaps:
            return result
        gaps = detect_gaps(state, template, llm=llm)
        if not gaps:
            return result
        enter_followup(state, [g.to_dict() for g in gaps])
        event_repo.insert(
            rid, "INTAKE_FIELD_SUGGESTED", "intake_wizard",
            payload={"gap_count": len(gaps), "stage": "FOLLOWUP"},
        )
        # Re-render via start() (no user input consumed)
        return start(template, state)

    # First render — handle the resume-past-ASKING case
    result = start(template, state)
    result = _maybe_intercept_for_followup(result)

    while not result.terminal:
        user_input = prompt_fn(result.prompt)
        result = step(template, state, user_input, suggest_fn=suggest_fn)
        intake_repo.save_state(state)
        if result.suggest_called:
            event_repo.insert(
                rid, "INTAKE_FIELD_SUGGESTED", "intake_wizard",
                payload={
                    "field": result.current_field.id if result.current_field else None,
                    "had_value": (state.pending_suggestion or {}).get("suggestion") is not None,
                },
            )
        if result.current_field is not None and not result.suggest_called:
            event_repo.insert(
                rid, "INTAKE_FIELD_ANSWERED", "intake_wizard",
                payload={"field": result.current_field.id, "stage": state.stage},
            )
        result = _maybe_intercept_for_followup(result)
        conn.commit()

    # Terminal
    if result.terminal_reason == "paused":
        return IntakeResult(
            run_id=rid, status="intake_paused",
            fields_answered=sum(1 for a in state.answers.values() if a.source == "user"),
            critical_missing=[
                fid for fid in template.critical_field_ids
                if fid not in state.answers or state.answers[fid].source == "skipped"
            ],
        )

    # Completed → promote
    artifact_id = promote_intake_brief(conn, config, state, template)
    event_repo.insert(rid, "INTAKE_COMPLETED", "intake_wizard",
                      payload={"brief_id": artifact_id})
    conn.commit()

    answered = sum(1 for a in state.answers.values() if a.source == "user")
    missing = [
        fid for fid in template.critical_field_ids
        if state.answers[fid].source == "skipped"
    ]
    return IntakeResult(
        run_id=rid, status="intake_complete",
        brief_id=artifact_id, fields_answered=answered, critical_missing=missing,
    )


def _apply_context_dir(
    state: IntakeState,
    template: Template,
    context_dir: str,
    llm: LLMClient | None,
    intro_writer: Callable[[str], None] | None,
) -> None:
    from ai_dev_system.intake.context_loader import scan_context_dir
    from ai_dev_system.intake.engine import _iso_now

    path = Path(context_dir)
    if not path.is_dir():
        if intro_writer:
            intro_writer(f"Warning: --context-dir '{context_dir}' not found, skipping.\n")
        return

    prefills = scan_context_dir(path, template, llm=llm)
    if not prefills:
        if intro_writer:
            intro_writer(f"No recognizable project files found in '{context_dir}'.\n")
        return

    for fid, val in prefills.items():
        if fid not in state.answers:
            state.answers[fid] = FieldAnswer(value=val, source="context_loaded")

    state.audit.append({
        "ts": _iso_now(), "event": "context_loaded",
        "count": len(prefills), "fields": list(prefills.keys()),
    })

    if intro_writer:
        intro_writer(
            f"Context loaded: {len(prefills)} fields pre-filled from '{context_dir}'\n"
            f"  Fields: {', '.join(prefills.keys())}\n"
            f"  Nhan Enter o tung field de chap nhan, hoac go moi de override.\n"
        )
