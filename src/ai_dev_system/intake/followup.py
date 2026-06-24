"""Followup gap detection — 4 logics per intake-wizard-design.md §"Followup Module".

Output: a list of `Gap` objects, surfaced by the runner to the engine's FOLLOWUP
stage. Each gap is either *field-targeting* (user can answer to fill the field)
or a *warning* (user reads, can edit later or continue).

Detection logic kinds:
1. critical_blank   — any critical field with value=None / source=skipped
2. inconsistency    — fires when a consistency_rules entry detects a conflict
3. ambiguity        — text_long user answer is too vague (LLM-scored, stub-mode=skip)
4. scope_mismatch   — scope_in count vs deadline (delegated to a consistency rule
                      so users see the message uniformly)

The 2nd and 4th are folded into consistency_rules.RULES so the rule registry is
the single source of truth for cross-field heuristics. `kind` is preserved on
each gap so the UI can group / colour-code if it wants to.

LLM ambiguity scoring is OPTIONAL — pass an LLMClient or omit to skip. In stub
mode the spec says all ambiguity = 0.7 (above the default 0.5 threshold), so
the ambiguity stage emits nothing when `llm=None`.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Protocol

from ai_dev_system.intake.consistency_rules import ConsistencyHit, check_all
from ai_dev_system.intake.engine import FieldAnswer, IntakeState
from ai_dev_system.intake.template import Template, TemplateField


logger = logging.getLogger(__name__)


GapKind = Literal["critical_blank", "inconsistency", "ambiguity", "scope_mismatch"]


@dataclass
class Gap:
    """One issue surfaced to the FOLLOWUP stage.

    `target_field_id` drives engine rendering:
      - non-None → render the field's ASKING prompt with gap.message as preamble,
        user can answer / skip / `?` / `enough`.
      - None     → render the message as a warning, user can `continue` /
        `edit <field>` / `enough`.
    """
    kind: GapKind
    message: str
    target_field_id: Optional[str] = None
    rule_id: Optional[str] = None       # only set for inconsistency/scope_mismatch
    hint: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "message": self.message,
            "target_field_id": self.target_field_id,
            "rule_id": self.rule_id,
            "hint": self.hint,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "Gap":
        return cls(
            kind=raw["kind"],
            message=raw["message"],
            target_field_id=raw.get("target_field_id"),
            rule_id=raw.get("rule_id"),
            hint=raw.get("hint"),
        )


# ---------------------------------------------------------------------------
# LLM protocol (minimal — duck-typed against existing clients)
# ---------------------------------------------------------------------------

class AmbiguityScorer(Protocol):
    def complete(self, system: str, user: str) -> str: ...  # pragma: no cover


_AMBIGUITY_SYSTEM = (
    "Bạn đang đánh giá độ rõ ràng (specificity) của một câu trả lời. "
    "Trả về DUY NHẤT JSON object dạng "
    '{"score": <0.0..1.0>, "hint": "<một câu giải thích nếu vague, hoặc rỗng>"}. '
    "Score 1.0 = rất cụ thể, có metric / scope / role rõ. "
    "Score 0.5 = thiếu 1 chiều thông tin. "
    "Score 0.0 = mơ hồ, không actionable."
)


def _score_ambiguity(llm: AmbiguityScorer, prompt: str, answer: str) -> tuple[float, str]:
    """Single LLM call. Falls back to 1.0 (treat as fine) on any failure."""
    user_msg = f"# Câu hỏi\n{prompt}\n\n# Câu trả lời\n{answer}"
    try:
        raw = llm.complete(_AMBIGUITY_SYSTEM, user_msg)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Ambiguity scoring failed: %s", exc)
        return 1.0, ""
    text = raw.strip()
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    try:
        data = json.loads(text)
        score = float(data.get("score", 1.0))
        hint = str(data.get("hint", "")).strip()
        return max(0.0, min(1.0, score)), hint
    except (json.JSONDecodeError, ValueError, TypeError):
        return 1.0, ""


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def _brief_dict(state: IntakeState) -> dict[str, Any]:
    """Flatten answers → {field_id: value} with skipped/null entries omitted."""
    out: dict[str, Any] = {}
    for fid, ans in state.answers.items():
        if ans.source == "skipped" or ans.value is None:
            continue
        out[fid] = ans.value
    return out


def _detect_critical_blanks(state: IntakeState, template: Template) -> list[Gap]:
    gaps: list[Gap] = []
    for fld in template.fields:
        if not fld.critical:
            continue
        ans = state.answers.get(fld.id)
        if ans is None or ans.source == "skipped" or ans.value is None:
            gaps.append(Gap(
                kind="critical_blank",
                message=f"Critical field '{fld.id}' chưa được trả lời.",
                target_field_id=fld.id,
            ))
    return gaps


def _detect_consistency(state: IntakeState) -> list[Gap]:
    """Cross-field rules → inconsistency / scope_mismatch gaps."""
    brief = _brief_dict(state)
    hits: list[ConsistencyHit] = check_all(brief)
    out: list[Gap] = []
    for h in hits:
        kind: GapKind = "scope_mismatch" if h.rule_id == "scope_vs_deadline" else "inconsistency"
        out.append(Gap(
            kind=kind,
            message=h.message,
            target_field_id=h.target_field_id,
            rule_id=h.rule_id,
        ))
    return out


def _detect_ambiguity(
    state: IntakeState,
    template: Template,
    llm: AmbiguityScorer | None,
    threshold: float,
) -> list[Gap]:
    if llm is None:
        return []
    gaps: list[Gap] = []
    for fld in template.fields:
        if fld.type != "text_long":
            continue
        ans = state.answers.get(fld.id)
        if ans is None or ans.source != "user" or not isinstance(ans.value, str):
            continue
        if len(ans.value.strip()) < 10:
            # Too short to even score — counts as ambiguous
            gaps.append(Gap(
                kind="ambiguity",
                message=f"Field '{fld.id}' câu trả lời quá ngắn ({len(ans.value)} ký tự).",
                target_field_id=fld.id,
                hint="Thêm metric / scope / role để cụ thể hơn.",
            ))
            continue
        score, hint = _score_ambiguity(llm, fld.prompt, ans.value)
        if score < threshold:
            gaps.append(Gap(
                kind="ambiguity",
                message=(
                    f"Field '{fld.id}' có thể chưa đủ cụ thể (score {score:.2f}). "
                    + (hint if hint else "Thêm chi tiết để spec không phải đoán.")
                ),
                target_field_id=fld.id,
                hint=hint or None,
            ))
    return gaps


def detect_gaps(
    state: IntakeState,
    template: Template,
    llm: AmbiguityScorer | None = None,
    ambiguity_threshold: float = 0.5,
) -> list[Gap]:
    """Run all 4 detection logics and concatenate results.

    Order: critical_blank → inconsistency → scope_mismatch (via rules)
           → ambiguity (last, since it costs LLM calls).
    """
    gaps: list[Gap] = []
    gaps.extend(_detect_critical_blanks(state, template))
    gaps.extend(_detect_consistency(state))
    gaps.extend(_detect_ambiguity(state, template, llm, ambiguity_threshold))
    return gaps
