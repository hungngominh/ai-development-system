# src/ai_dev_system/gate/gate1_review/state.py
"""Gate 1 review session state (G10+G8) — persist + resume mid-review.

GateSessionState stores per-question decisions made so far. It is serialised
as JSON into the `runs.gate1_session_state` column so the review can be
resumed if the skill is closed before `finalize`.

G8: scope_affected=True when scope_in or scope_out were edited during this
session. cmd_finalize includes this flag in its response so the skill can
warn the user and optionally re-trigger the debate/question pipeline.

Format (JSON):
{
  "schema": 1,
  "run_id": "...",
  "resolved": {
    "Q1": {"choice": "agent_a", "override": null, "resolution_type": "CHOICE_A"},
    ...
  },
  "brief_edits": [
    {"field": "scope_in", "operation": "append", "value": "reporting"},
    ...
  ],
  "approved_all": false,
  "scope_affected": false
}
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field


_SCHEMA_VERSION = 1

# Fields whose edit marks scope_affected=True (G8)
_SCOPE_AFFECTING_FIELDS: frozenset[str] = frozenset({"scope_in", "scope_out"})


@dataclass
class ResolvedItem:
    question_id: str
    choice: str | None          # agent_a | agent_b | moderator | override | None
    override_text: str | None   # populated when choice == "override"
    resolution_type: str        # CHOICE_A | CHOICE_B | MODERATOR | FORCED_HUMAN | APPROVED_ALL


@dataclass
class BriefEditEntry:
    field_name: str
    operation: str   # set | append | remove
    value: str


@dataclass
class GateSessionState:
    run_id: str
    resolved: dict[str, ResolvedItem] = field(default_factory=dict)
    brief_edits: list[BriefEditEntry] = field(default_factory=list)
    approved_all: bool = False
    scope_affected: bool = False   # G8: True if scope_in/scope_out edited

    def record_choice(
        self,
        question_id: str,
        choice: str,
        override_text: str | None = None,
    ) -> None:
        _CHOICE_TO_TYPE = {
            "agent_a": "CHOICE_A",
            "agent_b": "CHOICE_B",
            "moderator": "MODERATOR",
            "override": "FORCED_HUMAN",
        }
        self.resolved[question_id] = ResolvedItem(
            question_id=question_id,
            choice=choice,
            override_text=override_text,
            resolution_type=_CHOICE_TO_TYPE.get(choice, "FORCED_HUMAN"),
        )

    def record_brief_edit(self, field_name: str, operation: str, value: str) -> None:
        self.brief_edits.append(BriefEditEntry(
            field_name=field_name,
            operation=operation,
            value=value,
        ))
        if field_name in _SCOPE_AFFECTING_FIELDS:
            self.scope_affected = True

    def is_resolved(self, question_id: str) -> bool:
        return question_id in self.resolved or self.approved_all

    def to_json(self) -> str:
        data = {
            "schema": _SCHEMA_VERSION,
            "run_id": self.run_id,
            "resolved": {
                qid: {
                    "choice": r.choice,
                    "override": r.override_text,
                    "resolution_type": r.resolution_type,
                }
                for qid, r in self.resolved.items()
            },
            "brief_edits": [
                {"field": e.field_name, "operation": e.operation, "value": e.value}
                for e in self.brief_edits
            ],
            "approved_all": self.approved_all,
            "scope_affected": self.scope_affected,
        }
        return json.dumps(data, ensure_ascii=False)

    @classmethod
    def from_json(cls, run_id: str, raw: str) -> "GateSessionState":
        data = json.loads(raw)
        state = cls(run_id=run_id)
        state.approved_all = data.get("approved_all", False)
        state.scope_affected = data.get("scope_affected", False)
        for qid, r in (data.get("resolved") or {}).items():
            state.resolved[qid] = ResolvedItem(
                question_id=qid,
                choice=r.get("choice"),
                override_text=r.get("override"),
                resolution_type=r.get("resolution_type", "FORCED_HUMAN"),
            )
        for e in (data.get("brief_edits") or []):
            state.brief_edits.append(BriefEditEntry(
                field_name=e["field"],
                operation=e["operation"],
                value=e["value"],
            ))
        return state

    @classmethod
    def empty(cls, run_id: str) -> "GateSessionState":
        return cls(run_id=run_id)


def save_state(run_id: str, state: GateSessionState, conn) -> None:
    """Persist GateSessionState to runs.gate1_session_state column."""
    conn.execute(
        "UPDATE runs SET gate1_session_state = ? WHERE run_id = ?",
        (state.to_json(), run_id),
    )
    conn.commit()


def load_state(run_id: str, conn) -> GateSessionState:
    """Load GateSessionState from DB. Returns empty state if none saved."""
    row = conn.execute(
        "SELECT gate1_session_state FROM runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    if row is None:
        return GateSessionState.empty(run_id)
    raw = row[0] if isinstance(row, (list, tuple)) else row["gate1_session_state"]
    if not raw:
        return GateSessionState.empty(run_id)
    return GateSessionState.from_json(run_id, raw)


def clear_state(run_id: str, conn) -> None:
    """Clear session state (called after finalize)."""
    conn.execute(
        "UPDATE runs SET gate1_session_state = NULL WHERE run_id = ?",
        (run_id,),
    )
    conn.commit()
