# src/ai_dev_system/gate/gate1_review/parser.py
"""Gate 1 review — input parser (G4).

Regex-first parsing of user commands in the Gate 1 review session.
NLU fallback (G9) is not implemented here — ambiguous input returns
`action_type="unknown"` so the skill can ask the user to rephrase.

Recognized actions (spec gate1-skill-redesign §Input Parser):

  answer      — record a decision for a specific question
  expand      — expand a question or section for detail
  show        — alias for expand
  approve_all — bulk-approve consensus (guard: no pending forced/parse_failed)
  confirm     — finalize and write artifacts
  abort       — abort Gate 1 session
  unknown     — unrecognized input

Choice values for `answer`:
  "agent_a"     — user picks Agent A's position
  "agent_b"     — user picks Agent B's position
  "moderator"   — user approves moderator summary
  "override"    — user provides their own text (payload = override text)

"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

ActionType = Literal[
    "answer",
    "expand",
    "approve_all",
    "confirm",
    "abort",
    "unknown",
]

ChoiceType = Literal["agent_a", "agent_b", "moderator", "override", None]


@dataclass
class ParseResult:
    action_type: ActionType
    target: str | None = None          # question_id or section name
    choice: ChoiceType = None          # for action_type == "answer"
    payload: str | None = None         # override text for choice=="override"
    message: str = ""                  # human-readable confirmation / error
    accepted: bool = True              # False when guard rejects the action


# ---- regex patterns ----

# Question ID pattern: e.g. Q1, Q_auth, Q1_jwt_choice (case-insensitive)
_QID = r"([Qq][\w_-]+)"

# Whitespace-tolerant separator: space, colon, dash, arrow
_SEP = r"[\s:→\-]+\s*"

_RE_CHOOSE_A = re.compile(
    rf"^{_QID}\s+(?:chọn|chon|option|agent|pick)\s+A\b",
    re.IGNORECASE,
)
_RE_CHOOSE_B = re.compile(
    rf"^{_QID}\s+(?:chọn|chon|option|agent|pick)\s+B\b",
    re.IGNORECASE,
)
_RE_APPROVE_MOD = re.compile(
    rf"^{_QID}\s+(?:approve|đồng ý|dong y|accept|ok|chấp nhận)\s+(?:moderator|mod)\b",
    re.IGNORECASE,
)
_RE_OVERRIDE = re.compile(
    rf"^{_QID}{_SEP}(.+)$",
    re.IGNORECASE | re.DOTALL,
)

_RE_SHOW = re.compile(
    rf"^(?:show|xem|expand|mở rộng)\s+{_QID}$",
    re.IGNORECASE,
)
_RE_SHOW_BRIEF = re.compile(
    r"^(?:show|xem)\s+brief\b",
    re.IGNORECASE,
)
_RE_EXPAND_OPTIONAL = re.compile(
    r"^(?:expand|mở rộng|xem)\s+optional\b",
    re.IGNORECASE,
)
_RE_APPROVE_ALL = re.compile(
    r"^approve\s+all\b",
    re.IGNORECASE,
)
_RE_CONFIRM = re.compile(
    r"^(?:confirm|xác nhận|done|finalize)\b",
    re.IGNORECASE,
)
_RE_ABORT = re.compile(
    r"^(?:abort|hủy|cancel|thoát)\b",
    re.IGNORECASE,
)


def parse_user_input(
    text: str,
    *,
    pending_forced: int = 0,
    pending_parse_failed: int = 0,
) -> ParseResult:
    """Parse a raw user message into a structured ParseResult.

    Args:
        text: the raw user input string.
        pending_forced: number of forced-section items not yet answered.
        pending_parse_failed: number of parse-failed items not yet answered.

    Returns:
        ParseResult with action_type and relevant fields populated.
        `accepted=False` when a guard rejects the action (e.g. approve_all
        when forced items remain).
    """
    stripped = text.strip()
    if not stripped:
        return ParseResult(
            action_type="unknown",
            accepted=False,
            message="(empty input — please enter a command)",
        )

    # Confirm / abort (check first — short, unambiguous)
    if _RE_CONFIRM.match(stripped):
        return ParseResult(action_type="confirm", message="Xác nhận và ghi artifacts.")

    if _RE_ABORT.match(stripped):
        return ParseResult(action_type="abort", message="Hủy Gate 1 session.")

    # approve all
    if _RE_APPROVE_ALL.match(stripped):
        total_pending = pending_forced + pending_parse_failed
        if total_pending > 0:
            return ParseResult(
                action_type="approve_all",
                accepted=False,
                message=(
                    f"Không thể approve all khi còn {total_pending} câu cần quyết định "
                    f"(forced: {pending_forced}, parse-failed: {pending_parse_failed})."
                ),
            )
        return ParseResult(
            action_type="approve_all",
            message="Approve toàn bộ câu consensus. Gõ `confirm` để ghi artifacts.",
        )

    # show brief
    if _RE_SHOW_BRIEF.match(stripped):
        return ParseResult(action_type="expand", target="brief", message="Hiển thị brief đầy đủ.")

    # expand optional
    if _RE_EXPAND_OPTIONAL.match(stripped):
        return ParseResult(
            action_type="expand", target="auto_resolved",
            message="Hiển thị toàn bộ câu OPTIONAL đã auto-resolve.",
        )

    # show / expand <QID>
    m = _RE_SHOW.match(stripped)
    if m:
        qid = m.group(1).upper()
        return ParseResult(
            action_type="expand", target=qid,
            message=f"Hiển thị chi tiết {qid}.",
        )

    # choose A
    m = _RE_CHOOSE_A.match(stripped)
    if m:
        qid = m.group(1).upper()
        return ParseResult(
            action_type="answer", target=qid, choice="agent_a",
            message=f"Hiểu là: {qid} = chọn quan điểm Agent A. Đúng không?",
        )

    # choose B
    m = _RE_CHOOSE_B.match(stripped)
    if m:
        qid = m.group(1).upper()
        return ParseResult(
            action_type="answer", target=qid, choice="agent_b",
            message=f"Hiểu là: {qid} = chọn quan điểm Agent B. Đúng không?",
        )

    # approve moderator
    m = _RE_APPROVE_MOD.match(stripped)
    if m:
        qid = m.group(1).upper()
        return ParseResult(
            action_type="answer", target=qid, choice="moderator",
            message=f"Hiểu là: {qid} = đồng ý kết luận của Moderator. Đúng không?",
        )

    # override — QID: <text>   (must come after the specific choices above)
    m = _RE_OVERRIDE.match(stripped)
    if m:
        qid = m.group(1).upper()
        override_text = m.group(2).strip()
        # Guard: reject obviously empty overrides
        if not override_text:
            return ParseResult(
                action_type="unknown",
                accepted=False,
                message=f"Override text cho {qid} không được để trống.",
            )
        return ParseResult(
            action_type="answer", target=qid, choice="override", payload=override_text,
            message=f"Hiểu là: {qid} = override với text: <<{override_text}>>. Đúng không?",
        )

    return ParseResult(
        action_type="unknown",
        accepted=False,
        message=(
            f"Không hiểu lệnh: <<{stripped[:80]}>>. "
            "Thử: `Q1 chọn A`, `Q1 approve moderator`, `Q1: text riêng`, "
            "`show Q1`, `approve all`, `confirm`, `abort`."
        ),
    )
