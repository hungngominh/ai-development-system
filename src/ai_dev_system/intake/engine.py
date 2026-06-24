"""Intake wizard state machine — pure logic; I/O injected by caller.

S1+S2 scope: ASKING / CONFIRM / DONE / PAUSED, commands skip/back/save/show/edit.
S3 additions: SUGGESTING state. When user types `?` (or `không biết`) on a field
with `ai_can_suggest=true`, the engine asks the caller-provided `suggest_fn` for
a proposal and switches to SUGGESTING. The next step() accepts `a` (accept),
`b <text>` (replace), `c`/`skip` (decline), `?` (regenerate).

Design notes:
- `IntakeState` is fully serializable (json-safe), including a `pending_suggestion`
  payload so `show` mid-SUGGESTING does not lose context if state is reloaded.
- `step()` accepts an optional `suggest_fn`. Pure unit tests pass a stub; the
  runner wires it to `Suggester.propose`.
- `back` from SUGGESTING returns to ASKING at the same field (cancel suggestion).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Literal, Optional

from ai_dev_system.intake.template import Template, TemplateField


Source = Literal["user", "ai_suggested_confirmed", "skipped", "context_loaded"]


# ---------------------------------------------------------------------------
# Brief storage
# ---------------------------------------------------------------------------

@dataclass
class FieldAnswer:
    value: Any  # str | list[str] | int | None
    source: Source
    rationale: str | None = None


@dataclass
class IntakeState:
    """Live wizard state. Serializable to JSON for runs.intake_state."""
    template_id: str
    schema_hash: str
    run_id: str
    project_id: str
    stage: Literal["ASKING", "SUGGESTING", "FOLLOWUP", "CONFIRM", "DONE", "PAUSED"] = "ASKING"
    field_idx: int = 0
    answers: dict[str, FieldAnswer] = field(default_factory=dict)
    # Pending suggestion (used in SUGGESTING stage). None outside SUGGESTING.
    # Shape: {"field_id": str, "suggestion": Any, "rationale": str}
    pending_suggestion: Optional[dict] = None
    # Pending gaps (used in FOLLOWUP stage). Stored as list of dicts so the
    # state remains JSON-serializable; the followup module converts to/from Gap.
    pending_gaps: list[dict] = field(default_factory=list)
    followup_idx: int = 0
    # Field id that SUGGESTING returns to (e.g., "FOLLOWUP" to re-enter, or None for ASKING).
    suggesting_return_stage: Optional[str] = None
    audit: list[dict] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: _iso_now())
    completed_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "template_id": self.template_id,
            "schema_hash": self.schema_hash,
            "run_id": self.run_id,
            "project_id": self.project_id,
            "stage": self.stage,
            "field_idx": self.field_idx,
            "answers": {
                fid: {"value": fa.value, "source": fa.source, "rationale": fa.rationale}
                for fid, fa in self.answers.items()
            },
            "pending_suggestion": self.pending_suggestion,
            "pending_gaps": list(self.pending_gaps),
            "followup_idx": self.followup_idx,
            "suggesting_return_stage": self.suggesting_return_stage,
            "audit": list(self.audit),
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "IntakeState":
        answers = {
            fid: FieldAnswer(value=a["value"], source=a["source"], rationale=a.get("rationale"))
            for fid, a in (raw.get("answers") or {}).items()
        }
        return cls(
            template_id=raw["template_id"],
            schema_hash=raw["schema_hash"],
            run_id=raw["run_id"],
            project_id=raw["project_id"],
            stage=raw.get("stage", "ASKING"),
            field_idx=int(raw.get("field_idx", 0)),
            answers=answers,
            pending_suggestion=raw.get("pending_suggestion"),
            pending_gaps=list(raw.get("pending_gaps") or []),
            followup_idx=int(raw.get("followup_idx", 0)),
            suggesting_return_stage=raw.get("suggesting_return_stage"),
            audit=list(raw.get("audit") or []),
            created_at=raw.get("created_at") or _iso_now(),
            completed_at=raw.get("completed_at"),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_json(cls, s: str) -> "IntakeState":
        return cls.from_dict(json.loads(s))


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_state(template: Template, run_id: str, project_id: str) -> IntakeState:
    return IntakeState(
        template_id=template.id,
        schema_hash=template.schema_hash,
        run_id=run_id,
        project_id=project_id,
    )


# ---------------------------------------------------------------------------
# Step result + suggest callback
# ---------------------------------------------------------------------------

@dataclass
class StepResult:
    state: IntakeState
    prompt: str
    current_field: TemplateField | None = None
    error: str | None = None
    terminal: bool = False
    terminal_reason: Literal["complete", "paused"] | None = None
    suggest_called: bool = False  # True when this step triggered an LLM call


# Caller-injected: returns dict with {suggestion, rationale, cache_hit?}.
# When None, '?' input falls back to "no LLM wired" message.
SuggestFn = Callable[[TemplateField, dict[str, FieldAnswer]], dict]


# ---------------------------------------------------------------------------
# Input parsing
# ---------------------------------------------------------------------------

SPECIAL_COMMANDS = frozenset({"back", "save", "show", "skip"})
SUGGEST_TRIGGERS = frozenset({"?", "không biết", "khong biet", "idk"})


def _parse_list(raw: str) -> list[str]:
    parts: list[str] = []
    for chunk in raw.replace("\n", ",").split(","):
        s = chunk.strip()
        if s:
            parts.append(s)
    return parts


def _coerce(field_spec: TemplateField, raw: str) -> tuple[Any | None, str | None]:
    raw = raw.strip()
    if not raw:
        return None, "Câu trả lời trống. Gõ `skip` nếu thật sự không muốn trả lời."
    t = field_spec.type
    if t in ("text_short", "text_long"):
        return raw, None
    if t == "list_str":
        items = _parse_list(raw)
        if not items:
            return None, "Cần ít nhất 1 item. Gõ `skip` nếu muốn bỏ qua."
        return items, None
    if t == "enum":
        if raw not in field_spec.options:
            return None, f"Giá trị phải là một trong: {' | '.join(field_spec.options)}"
        return raw, None
    if t == "number":
        try:
            return int(raw), None
        except ValueError:
            return None, f"Cần số nguyên, nhận: {raw!r}"
    return raw, None


# ---------------------------------------------------------------------------
# Render helpers
# ---------------------------------------------------------------------------

def _render_prompt(field_spec: TemplateField, idx: int, total: int, prefilled: Any = None) -> str:
    crit = " [critical]" if field_spec.critical else ""
    hint = f"\n  Hint: {field_spec.examples_hint}" if field_spec.examples_hint else ""
    opts = ""
    if field_spec.type == "enum":
        opts = f"\n  Options: {' | '.join(field_spec.options)}"
    suggest_hint = ""
    if field_spec.ai_can_suggest:
        suggest_hint = " / ? (đề xuất)"
    if prefilled is not None:
        val_str = ", ".join(str(x) for x in prefilled) if isinstance(prefilled, list) else str(prefilled)
        footer = f"\n  [Context: {val_str[:120]}]\n  (Enter để dùng / gõ mới / skip / back / save / show)"
    else:
        footer = f"\n  (Commands: skip / back / save / show{suggest_hint})"
    return (
        f"[{idx + 1}/{total}] {field_spec.id}{crit}\n"
        f"  {field_spec.prompt}{opts}{hint}"
        f"{footer}"
    )


_SOURCE_MARKER = {
    "user": "U",
    "ai_suggested_confirmed": "AI",
    "skipped": "-",
    "context_loaded": "C",
}


def _render_state(state: IntakeState, template: Template) -> str:
    lines = ["Current brief:"]
    for f in template.fields:
        ans = state.answers.get(f.id)
        if ans is None:
            lines.append(f"  - {f.id}: <pending>")
        else:
            marker = _SOURCE_MARKER.get(ans.source, "?")
            val = ans.value if not isinstance(ans.value, list) else ", ".join(str(x) for x in ans.value)
            lines.append(f"  - {f.id}: [{marker}] {val}")
    return "\n".join(lines)


def _get_prefilled(state: IntakeState, fld: TemplateField) -> Any:
    """Return the pre-filled value if field has source 'context_loaded', else None."""
    ans = state.answers.get(fld.id)
    if ans is not None and ans.source == "context_loaded":
        return ans.value
    return None


def _render_suggestion(state: IntakeState) -> str:
    p = state.pending_suggestion or {}
    sug = p.get("suggestion")
    rat = p.get("rationale", "")
    if isinstance(sug, list):
        sug_str = ", ".join(str(s) for s in sug)
    elif sug is None:
        sug_str = "(LLM không đề xuất được)"
    else:
        sug_str = str(sug)
    return (
        f"🤖 Tôi đề xuất: {sug_str}\n"
        f"  Lý do: {rat}\n"
        f"  (a) chấp nhận  /  (b) <giá trị khác>  /  (c) skip  /  ? để gen lại  /  back để hủy"
    )


# ---------------------------------------------------------------------------
# State transitions
# ---------------------------------------------------------------------------

def start(template: Template, state: IntakeState) -> StepResult:
    if state.stage == "DONE":
        return StepResult(state=state, prompt="Intake already complete.",
                          terminal=True, terminal_reason="complete")
    if state.stage == "SUGGESTING":
        fld = template.fields[state.field_idx]
        return StepResult(state=state, prompt=_render_suggestion(state), current_field=fld)
    if state.stage == "FOLLOWUP":
        return _render_followup_step(template, state)
    if state.stage == "CONFIRM":
        return _render_confirm(state, template)
    if state.field_idx >= len(template.fields):
        # End of ASKING reached but runner hasn't called enter_followup yet.
        # Render a placeholder; runner will call enter_followup or enter_confirm.
        return StepResult(
            state=state,
            prompt="(awaiting gap analysis...)",
        )
    fld = template.fields[state.field_idx]
    return StepResult(
        state=state,
        prompt=_render_prompt(fld, state.field_idx, len(template.fields),
                              prefilled=_get_prefilled(state, fld)),
        current_field=fld,
    )


def asking_completed(state: IntakeState, template: Template) -> bool:
    """True iff ASKING has reached past the last field. Runner uses this to
    decide whether to invoke gap detection before continuing the loop."""
    return state.stage == "ASKING" and state.field_idx >= len(template.fields)


def enter_followup(state: IntakeState, gaps: list[dict]) -> None:
    """Switch state into FOLLOWUP with the given gap list. Caller (runner) is
    responsible for running gap detection and serializing each Gap via .to_dict().

    If `gaps` is empty, state stays at ASKING-complete; caller should then call
    enter_confirm() directly (or just let step() fall through to CONFIRM).
    """
    if not gaps:
        return
    state.stage = "FOLLOWUP"
    state.pending_gaps = list(gaps)
    state.followup_idx = 0
    state.audit.append({"ts": _iso_now(), "event": "followup_started",
                        "field": None, "gap_count": len(gaps)})


def enter_confirm(state: IntakeState, template: Template) -> StepResult:
    """Public wrapper so the runner can transition to CONFIRM after FOLLOWUP."""
    return _enter_confirm(state, template)


def step(
    template: Template,
    state: IntakeState,
    user_input: str,
    suggest_fn: SuggestFn | None = None,
) -> StepResult:
    """Advance the wizard by one user input.

    `suggest_fn` is invoked when the user types `?` on an `ai_can_suggest=true`
    field. Pass None when no LLM is wired — the engine will show a fallback message.
    """
    if state.stage == "DONE":
        return StepResult(state=state, prompt="Already done.", terminal=True,
                          terminal_reason="complete")

    if state.stage == "CONFIRM":
        return _handle_confirm(template, state, user_input)

    if state.stage == "SUGGESTING":
        return _handle_suggesting(template, state, user_input, suggest_fn)

    if state.stage == "FOLLOWUP":
        return _handle_followup(template, state, user_input, suggest_fn)

    # ASKING stage
    if state.field_idx >= len(template.fields):
        # Runner should have called enter_followup or enter_confirm by now;
        # if not, default to CONFIRM (no followup detected).
        return _enter_confirm(state, template)

    fld = template.fields[state.field_idx]
    raw = (user_input or "").strip()
    cmd = raw.lower()

    if cmd == "save":
        state.stage = "PAUSED"
        return StepResult(
            state=state,
            prompt=f"💾 Đã lưu trạng thái. Resume bằng `ai-dev intake resume --run-id {state.run_id}`.",
            terminal=True, terminal_reason="paused",
        )

    if cmd == "show":
        return StepResult(
            state=state,
            prompt=_render_state(state, template) + "\n\n"
                   + _render_prompt(fld, state.field_idx, len(template.fields)),
            current_field=fld,
        )

    if cmd == "back":
        if state.field_idx == 0:
            return StepResult(
                state=state,
                prompt="Đang ở field đầu tiên — không back được nữa.\n\n"
                       + _render_prompt(fld, state.field_idx, len(template.fields)),
                current_field=fld, error="cannot_back_from_first",
            )
        state.field_idx -= 1
        prev = template.fields[state.field_idx]
        state.audit.append({"ts": _iso_now(), "event": "back", "field": prev.id})
        return StepResult(
            state=state,
            prompt=_render_prompt(prev, state.field_idx, len(template.fields)),
            current_field=prev,
        )

    if cmd == "skip":
        state.answers[fld.id] = FieldAnswer(value=None, source="skipped")
        state.audit.append({"ts": _iso_now(), "event": "skipped", "field": fld.id})
        state.field_idx += 1
        return _next_or_confirm(template, state)

    # `?` trigger → SUGGESTING
    if cmd in SUGGEST_TRIGGERS:
        return _try_enter_suggesting(template, state, fld, suggest_fn)

    # Empty input on a context_loaded field → accept pre-filled value
    if not raw:
        existing = state.answers.get(fld.id)
        if existing is not None and existing.source == "context_loaded":
            state.audit.append({
                "ts": _iso_now(), "event": "context_confirmed",
                "field": fld.id, "value_preview": _preview(existing.value),
            })
            state.field_idx += 1
            return _next_or_confirm(template, state)

    # Real answer
    value, err = _coerce(fld, raw)
    if err is not None:
        return StepResult(
            state=state,
            prompt=err + "\n\n" + _render_prompt(fld, state.field_idx, len(template.fields),
                                                  prefilled=_get_prefilled(state, fld)),
            current_field=fld, error=err,
        )

    state.answers[fld.id] = FieldAnswer(value=value, source="user")
    state.audit.append({
        "ts": _iso_now(), "event": "answered", "field": fld.id,
        "value_preview": _preview(value),
    })
    state.field_idx += 1
    return _next_or_confirm(template, state)


# ---------------------------------------------------------------------------
# SUGGESTING-specific handlers
# ---------------------------------------------------------------------------

def _try_enter_suggesting(
    template: Template,
    state: IntakeState,
    fld: TemplateField,
    suggest_fn: SuggestFn | None,
) -> StepResult:
    if not fld.ai_can_suggest:
        return StepResult(
            state=state,
            prompt=(
                "Field này không thể đoán hộ — chỉ bạn biết.\n"
                "Gõ `skip` nếu thật sự chưa rõ, hoặc trả lời trực tiếp.\n\n"
                + _render_prompt(fld, state.field_idx, len(template.fields))
            ),
            current_field=fld, error="ai_cannot_suggest",
        )
    if suggest_fn is None:
        return StepResult(
            state=state,
            prompt=(
                "Suggest mode chưa được wire (LLM client trống). "
                "Gõ `skip` hoặc trả lời trực tiếp.\n\n"
                + _render_prompt(fld, state.field_idx, len(template.fields))
            ),
            current_field=fld, error="no_suggest_fn",
        )
    return _call_suggest_and_render(template, state, fld, suggest_fn)


def _call_suggest_and_render(
    template: Template,
    state: IntakeState,
    fld: TemplateField,
    suggest_fn: SuggestFn,
    return_stage: str | None = None,
) -> StepResult:
    """`return_stage` records where to go back when SUGGESTING resolves —
    typically None (back to ASKING+1) or 'FOLLOWUP' (back to the same gap)."""
    try:
        proposal = suggest_fn(fld, state.answers)
    except Exception as exc:  # noqa: BLE001 — engine never crashes on LLM failure
        return StepResult(
            state=state,
            prompt=(
                f"⚠ Không gọi được LLM ({exc}). "
                "Gõ `skip` hoặc trả lời trực tiếp.\n\n"
                + _render_prompt(fld, state.field_idx, len(template.fields))
            ),
            current_field=fld, error="suggest_failed",
        )

    state.stage = "SUGGESTING"
    state.pending_suggestion = {
        "field_id": fld.id,
        "suggestion": proposal.get("suggestion"),
        "rationale": proposal.get("rationale", ""),
    }
    state.suggesting_return_stage = return_stage
    state.audit.append({
        "ts": _iso_now(), "event": "suggested", "field": fld.id,
        "value_preview": _preview(proposal.get("suggestion")),
    })
    return StepResult(
        state=state, prompt=_render_suggestion(state),
        current_field=fld, suggest_called=True,
    )


def _handle_suggesting(
    template: Template,
    state: IntakeState,
    user_input: str,
    suggest_fn: SuggestFn | None,
) -> StepResult:
    fld = template.fields[state.field_idx]
    raw = (user_input or "").strip()
    cmd = raw.lower()
    proposal = state.pending_suggestion or {}

    if cmd in ("a", "ok", "accept"):
        sug = proposal.get("suggestion")
        if sug is None:
            return StepResult(
                state=state,
                prompt="LLM không đề xuất được giá trị cụ thể. "
                       "Gõ `b <text>` để tự nhập, hoặc `c` để skip.\n\n"
                       + _render_suggestion(state),
                current_field=fld, error="suggestion_was_null",
            )
        state.answers[fld.id] = FieldAnswer(
            value=sug, source="ai_suggested_confirmed",
            rationale=proposal.get("rationale"),
        )
        state.audit.append({"ts": _iso_now(), "event": "confirmed_suggestion",
                            "field": fld.id, "value_preview": _preview(sug)})
        state.pending_suggestion = None
        return _resolve_suggesting_return(template, state)

    if cmd in ("c", "skip"):
        state.answers[fld.id] = FieldAnswer(value=None, source="skipped")
        state.audit.append({"ts": _iso_now(), "event": "skipped_after_suggest",
                            "field": fld.id})
        state.pending_suggestion = None
        return _resolve_suggesting_return(template, state)

    if cmd == "back":
        # Cancel SUGGESTING, return to the stage we came from.
        state.pending_suggestion = None
        if state.suggesting_return_stage == "FOLLOWUP":
            state.stage = "FOLLOWUP"
            state.suggesting_return_stage = None
            return _render_followup_step(template, state)
        state.stage = "ASKING"
        state.suggesting_return_stage = None
        return StepResult(
            state=state,
            prompt=_render_prompt(fld, state.field_idx, len(template.fields)),
            current_field=fld,
        )

    if cmd == "show":
        return StepResult(
            state=state,
            prompt=_render_state(state, template) + "\n\n" + _render_suggestion(state),
            current_field=fld,
        )

    if cmd in SUGGEST_TRIGGERS:
        # Regenerate (cache will return same if brief unchanged)
        if suggest_fn is None:
            return StepResult(
                state=state, prompt=_render_suggestion(state),
                current_field=fld, error="no_suggest_fn",
            )
        return _call_suggest_and_render(template, state, fld, suggest_fn)

    # "b <text>" or raw value (allow either "b answer" or just "answer")
    if cmd.startswith("b ") or cmd == "b":
        raw_value = raw[1:].strip() if cmd == "b" else raw[2:].strip()
    else:
        raw_value = raw

    if not raw_value:
        return StepResult(
            state=state,
            prompt="Cần giá trị. Gõ `a` để chấp nhận đề xuất, `b <text>` để tự nhập, "
                   "`c` để skip, hoặc `back` để hủy.\n\n" + _render_suggestion(state),
            current_field=fld, error="empty_replacement",
        )

    value, err = _coerce(fld, raw_value)
    if err is not None:
        return StepResult(
            state=state, prompt=err + "\n\n" + _render_suggestion(state),
            current_field=fld, error=err,
        )

    state.answers[fld.id] = FieldAnswer(value=value, source="user")
    state.audit.append({"ts": _iso_now(), "event": "answered_after_suggest",
                        "field": fld.id, "value_preview": _preview(value)})
    state.pending_suggestion = None
    return _resolve_suggesting_return(template, state)


def _resolve_suggesting_return(template: Template, state: IntakeState) -> StepResult:
    """After a SUGGESTING is resolved (accept/replace/skip), return to the
    stage indicated by `suggesting_return_stage`. Default: advance ASKING."""
    if state.suggesting_return_stage == "FOLLOWUP":
        state.stage = "FOLLOWUP"
        state.suggesting_return_stage = None
        state.followup_idx += 1
        return _render_followup_step(template, state)
    state.stage = "ASKING"
    state.suggesting_return_stage = None
    state.field_idx += 1
    return _next_or_confirm(template, state)


# ---------------------------------------------------------------------------
# FOLLOWUP handlers
# ---------------------------------------------------------------------------

ENOUGH_TRIGGERS = frozenset({"enough", "đủ rồi", "du roi", "done"})


def _record_remaining_as_assumptions(state: IntakeState) -> None:
    for gap in state.pending_gaps[state.followup_idx:]:
        target = gap.get("target_field_id")
        if target and target not in state.answers:
            state.answers[target] = FieldAnswer(value=None, source="skipped")
        state.audit.append({
            "ts": _iso_now(), "event": "followup_assumed",
            "field": target,
            "value_preview": (gap.get("rule_id") or gap.get("kind") or "")[:80],
        })
    state.followup_idx = len(state.pending_gaps)


def _render_followup_step(template: Template, state: IntakeState) -> StepResult:
    """Render the current gap or, if exhausted, transition to CONFIRM."""
    if state.followup_idx >= len(state.pending_gaps):
        return _enter_confirm(state, template)

    gap = state.pending_gaps[state.followup_idx]
    n = len(state.pending_gaps)
    progress = f"[Follow-up {state.followup_idx + 1}/{n}]"
    kind_label = {
        "critical_blank": "Critical chưa trả lời",
        "inconsistency": "Inconsistency",
        "ambiguity": "Có thể chưa đủ rõ",
        "scope_mismatch": "Scope/Deadline mismatch",
    }.get(gap.get("kind", ""), gap.get("kind", ""))

    target = gap.get("target_field_id")
    message = gap.get("message", "")
    if target:
        try:
            fld = template.field_by_id(target)
        except KeyError:
            fld = None
        if fld is not None:
            field_prompt = _render_prompt(fld, template.field_index(fld.id), len(template.fields))
            return StepResult(
                state=state,
                prompt=(
                    f"{progress} {kind_label}\n"
                    f"⚠ {message}\n\n"
                    f"{field_prompt}\n"
                    f"  (Hoặc gõ `enough` để bỏ qua follow-up còn lại.)"
                ),
                current_field=fld,
            )

    # Warning gap (no field target)
    return StepResult(
        state=state,
        prompt=(
            f"{progress} {kind_label}\n"
            f"⚠ {message}\n\n"
            f"  Gõ `continue` để bỏ qua, `edit <field>` để chỉnh sửa, "
            f"hoặc `enough` để skip tất cả."
        ),
    )


def _handle_followup(
    template: Template,
    state: IntakeState,
    user_input: str,
    suggest_fn: SuggestFn | None,
) -> StepResult:
    raw = (user_input or "").strip()
    cmd = raw.lower()

    if state.followup_idx >= len(state.pending_gaps):
        return _enter_confirm(state, template)

    gap = state.pending_gaps[state.followup_idx]
    target = gap.get("target_field_id")

    if cmd in ENOUGH_TRIGGERS:
        _record_remaining_as_assumptions(state)
        return _enter_confirm(state, template)

    if cmd == "save":
        state.stage = "PAUSED"
        return StepResult(
            state=state,
            prompt=f"💾 Đã lưu trạng thái. Resume bằng `ai-dev intake resume --run-id {state.run_id}`.",
            terminal=True, terminal_reason="paused",
        )

    if cmd == "show":
        return StepResult(
            state=state,
            prompt=_render_state(state, template) + "\n\n"
                   + _render_followup_step(template, state).prompt,
        )

    if cmd == "continue" and target is None:
        # Warning-only gap; advance without altering answers
        state.followup_idx += 1
        return _render_followup_step(template, state)

    if cmd.startswith("edit "):
        new_target = raw[5:].strip()
        try:
            fld = template.field_by_id(new_target)
        except KeyError:
            return StepResult(
                state=state,
                prompt=f"Không có field {new_target!r}. Gõ `continue` hoặc `enough`.",
                error="unknown_field",
            )
        # Switch to ASKING for that field; followup_idx unchanged so we resume
        # the gap when ASKING is satisfied. Simpler: just answer or skip.
        state.stage = "ASKING"
        state.field_idx = template.field_index(fld.id)
        # We'll come back to FOLLOWUP next gap via re-detect; for now, treat
        # an edit as advancing past the current gap.
        state.followup_idx += 1
        state.audit.append({"ts": _iso_now(), "event": "followup_edit",
                            "field": fld.id})
        return StepResult(
            state=state,
            prompt=_render_prompt(fld, state.field_idx, len(template.fields)),
            current_field=fld,
        )

    if cmd == "skip":
        if target:
            state.answers[target] = FieldAnswer(value=None, source="skipped")
        state.audit.append({"ts": _iso_now(), "event": "followup_skip",
                            "field": target})
        state.followup_idx += 1
        return _render_followup_step(template, state)

    if cmd in SUGGEST_TRIGGERS and target is not None:
        try:
            fld = template.field_by_id(target)
        except KeyError:
            return _render_followup_step(template, state)
        if not fld.ai_can_suggest:
            return StepResult(
                state=state,
                prompt="Field này không thể đoán hộ. Trả lời trực tiếp, hoặc gõ `skip` / `enough`.\n\n"
                       + _render_followup_step(template, state).prompt,
                current_field=fld, error="ai_cannot_suggest",
            )
        if suggest_fn is None:
            return StepResult(
                state=state,
                prompt="Suggest mode chưa được wire.\n\n"
                       + _render_followup_step(template, state).prompt,
                current_field=fld, error="no_suggest_fn",
            )
        # Pivot field_idx to the gap's target so SUGGESTING renders the right field
        state.field_idx = template.field_index(target)
        return _call_suggest_and_render(template, state, fld, suggest_fn, return_stage="FOLLOWUP")

    if cmd == "back":
        if state.followup_idx == 0:
            return StepResult(
                state=state,
                prompt="Đang ở follow-up đầu tiên — không back được nữa.\n\n"
                       + _render_followup_step(template, state).prompt,
                error="cannot_back_from_first",
            )
        state.followup_idx -= 1
        return _render_followup_step(template, state)

    # Treat input as answer to the target field (if any)
    if target is None:
        return StepResult(
            state=state,
            prompt="Lệnh không hợp lệ cho warning. Gõ `continue`, `edit <field>`, hoặc `enough`.\n\n"
                   + _render_followup_step(template, state).prompt,
            error="bad_followup_cmd",
        )

    try:
        fld = template.field_by_id(target)
    except KeyError:
        state.followup_idx += 1
        return _render_followup_step(template, state)

    value, err = _coerce(fld, raw)
    if err is not None:
        return StepResult(
            state=state,
            prompt=err + "\n\n" + _render_followup_step(template, state).prompt,
            current_field=fld, error=err,
        )
    state.answers[fld.id] = FieldAnswer(value=value, source="user")
    state.audit.append({"ts": _iso_now(), "event": "followup_answered",
                        "field": fld.id, "value_preview": _preview(value)})
    state.followup_idx += 1
    return _render_followup_step(template, state)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _next_or_confirm(template: Template, state: IntakeState) -> StepResult:
    if state.field_idx >= len(template.fields):
        return _enter_confirm(state, template)
    nxt = template.fields[state.field_idx]
    return StepResult(
        state=state,
        prompt=_render_prompt(nxt, state.field_idx, len(template.fields)),
        current_field=nxt,
    )


def _enter_confirm(state: IntakeState, template: Template) -> StepResult:
    state.stage = "CONFIRM"
    return _render_confirm(state, template)


def _render_confirm(state: IntakeState, template: Template) -> StepResult:
    body = _render_state(state, template)
    crit_missing = [
        f.id for f in template.fields
        if f.critical and (
            state.answers.get(f.id) is None
            or state.answers[f.id].source == "skipped"
        )
    ]
    warn = ""
    if crit_missing:
        warn = (
            "\n\n⚠ Critical fields chưa trả lời: " + ", ".join(crit_missing)
            + "\nGõ `edit <field>` để quay lại, hoặc `confirm` để promote (sẽ thành assumption)."
        )
    return StepResult(
        state=state,
        prompt=body + warn + "\n\nGõ `confirm` để chốt brief, hoặc `edit <field>` để sửa.",
    )


def _handle_confirm(template: Template, state: IntakeState, user_input: str) -> StepResult:
    raw = (user_input or "").strip()
    cmd_lower = raw.lower()

    if cmd_lower == "confirm":
        state.stage = "DONE"
        state.completed_at = _iso_now()
        state.audit.append({"ts": _iso_now(), "event": "confirmed", "field": "*"})
        return StepResult(
            state=state, prompt="✅ Brief confirmed. Promoting INTAKE_BRIEF artifact.",
            terminal=True, terminal_reason="complete",
        )

    if cmd_lower.startswith("edit "):
        target = raw[5:].strip()
        try:
            idx = template.field_index(target)
        except KeyError:
            return StepResult(
                state=state,
                prompt=f"Không có field {target!r}. Gõ `confirm` hoặc `edit <field>`.",
                error="unknown_field",
            )
        state.stage = "ASKING"
        state.field_idx = idx
        fld = template.fields[idx]
        return StepResult(
            state=state, prompt=_render_prompt(fld, idx, len(template.fields)),
            current_field=fld,
        )

    return StepResult(
        state=state,
        prompt="Lệnh không hợp lệ. Gõ `confirm` để chốt, hoặc `edit <field>` để sửa.",
        error="bad_confirm_cmd",
    )


def _preview(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)[:80]
    if value is None:
        return "<none>"
    return str(value)[:80]


# ---------------------------------------------------------------------------
# Brief output (for promotion)
# ---------------------------------------------------------------------------

def to_brief_v2(state: IntakeState, template: Template, source_hash: str) -> dict:
    fields: dict[str, dict] = {}
    for f in template.fields:
        ans = state.answers.get(f.id)
        if ans is None:
            fields[f.id] = {"value": None, "source": "skipped", "rationale": None}
        else:
            fields[f.id] = {"value": ans.value, "source": ans.source, "rationale": ans.rationale}
    assumptions = [
        f.id for f in template.fields
        if f.critical and fields[f.id]["source"] == "skipped"
    ]
    return {
        "brief_version": 2,
        "template_id": template.id,
        "schema_hash": template.schema_hash,
        "run_id": state.run_id,
        "project_id": state.project_id,
        "source_hash": source_hash,
        "created_at": state.created_at,
        "completed_at": state.completed_at,
        "fields": fields,
        "assumptions": assumptions,
        "audit": list(state.audit),
    }
