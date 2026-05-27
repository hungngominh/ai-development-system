# src/ai_dev_system/gate/gate1_review/nlu.py
"""Gate 1 review LLM NLU fallback (G9).

Called by parse_user_input() when no regex pattern matches. Uses a small LLM
to parse the user's intent into a structured action.

Input to LLM:
  - User message (raw text)
  - Valid action schema

Output from LLM (JSON):
  {
    "action_type": "answer"|"expand"|"edit_brief"|"approve_all"|"confirm"|"abort"|"unknown",
    "target": "Q1" or null,
    "choice": "agent_a"|"agent_b"|"moderator"|"override" or null,
    "payload": "override text" or null
  }

Falls back to action_type="unknown" if LLM fails, returns invalid JSON,
or returns an unsupported action_type.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai_dev_system.gate.gate1_review.parser import ParseResult

_VALID_ACTION_TYPES = frozenset({
    "answer", "expand", "edit_brief", "approve_all", "confirm", "abort", "unknown",
})
_VALID_CHOICES = frozenset({"agent_a", "agent_b", "moderator", "override", None})

_SYSTEM_PROMPT = """\
You are a parser for a structured Gate 1 debate review CLI.
The user types commands to resolve AI debate questions. Parse their intent.

Valid action_type values:
  - answer:      user resolves a question (QID + choice)
  - expand:      user wants to see more detail (show Q1 / show brief / expand optional)
  - edit_brief:  user edits a brief field (field_name + new value)
  - approve_all: user approves all consensus/auto-resolved questions at once
  - confirm:     user finalises the review (writes artifacts)
  - abort:       user cancels the review session
  - unknown:     cannot determine intent

Valid choice values (for action_type=answer):
  - agent_a:   accept Agent A's position
  - agent_b:   accept Agent B's position
  - moderator: accept the Moderator's conclusion
  - override:  write a custom answer (payload field contains the text)

Respond ONLY with a JSON object, no markdown, no explanation:
{"action_type": "...", "target": "Q1" or null, "choice": "..." or null, "payload": "..." or null}
"""


def llm_parse(text: str, llm_client) -> "ParseResult":
    """Try to parse text with LLM. Falls back to unknown on any failure."""
    from ai_dev_system.gate.gate1_review.parser import ParseResult

    try:
        user_msg = f"User input: {text!r}\n\nReturn JSON only."
        response = llm_client.complete(system=_SYSTEM_PROMPT, user=user_msg)
        data = json.loads(response.strip())

        action_type = data.get("action_type", "unknown")
        if action_type not in _VALID_ACTION_TYPES:
            action_type = "unknown"

        choice = data.get("choice")
        if choice not in _VALID_CHOICES:
            choice = None

        target = data.get("target")
        if target:
            target = str(target).upper()

        payload = data.get("payload")

        if action_type == "unknown":
            return ParseResult(
                action_type="unknown",
                accepted=False,
                message=f"LLM cũng không hiểu lệnh: <<{text[:80]}>>.",
            )

        return ParseResult(
            action_type=action_type,  # type: ignore[arg-type]
            target=target,
            choice=choice,  # type: ignore[arg-type]
            payload=payload,
            message=f"[NLU] Hiểu là: {action_type} target={target!r} choice={choice!r}",
        )

    except Exception:
        return ParseResult(
            action_type="unknown",
            accepted=False,
            message=f"Không hiểu lệnh: <<{text[:80]}>>.",
        )
