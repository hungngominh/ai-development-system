# src/ai_dev_system/gate/gate1_review/renderer.py
"""Gate 1 review — markdown renderer (G3).

Produces Vietnamese markdown blocks from ReviewSection / ReviewItem lists.
These blocks are displayed in the chat-based skill; Claude reads them and
presents them to the user.

Section rendering rules (spec gate1-skill-redesign §Rendering rules):

  forced      — full detail: context + agent positions + moderator + action prompt
  parse_failed — warn block + raw output (debug) + action prompt
  consensus   — one-line summary per item (expand on `show <id>`)
  auto_resolved — group header only (expand on `expand optional`)

Brief section always shown; uses brief v2 fields if available, falls back
to legacy raw_idea.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai_dev_system.gate.gate1_review.loader import GateReviewContext
    from ai_dev_system.gate.gate1_review.sections import ReviewItem, ReviewSection

_MAX_RAW_TRUNCATE = 300
_MAX_POSITION_CHARS = 200


def render_all(ctx: "GateReviewContext", sections: list["ReviewSection"]) -> str:
    """Render the full Gate 1 review as a markdown string."""
    by_name = {s.name: s for s in sections}

    parts: list[str] = [
        render_brief_header(ctx),
        render_forced_section(by_name["forced"]),
        render_parse_failed_section(by_name["parse_failed"]),
        render_consensus_section(by_name["consensus"]),
        render_auto_resolved_section(by_name["auto_resolved"]),
        _render_help_footer(),
    ]
    return "\n\n".join(p for p in parts if p.strip())


def render_brief_header(ctx: "GateReviewContext") -> str:
    """Render the brief summary block (collapsed by default)."""
    brief = ctx.brief
    if ctx.is_legacy_brief:
        raw_idea = brief.get("raw_idea", "(no brief)")
        return (
            f"## Brief — {ctx.project_name}\n"
            f"*(Legacy run — brief v2 không có)*\n\n"
            f"Raw idea: {raw_idea}"
        )

    ps = brief.get("problem_statement", "")[:120]
    scope_in = ", ".join(brief.get("scope_in", [])[:5])
    nfr = " > ".join(brief.get("nfr_priority", [])[:3])
    return (
        f"## Brief — {ctx.project_name}\n"
        f"Type `show brief` để xem chi tiết, `edit <field>` để sửa.\n\n"
        f"**Problem:** {ps}\n"
        f"**Scope IN:** {scope_in or '(chưa có)'}\n"
        f"**NFR:** {nfr or '(chưa có)'}"
    )


def render_forced_section(section: "ReviewSection") -> str:
    """Render the 'Cần quyết định' section (ESCALATE + NEED_MORE_EVIDENCE)."""
    if not section.items:
        return "## ✅ Không có câu nào cần quyết định bắt buộc"

    lines = [f"## 🔴 Cần quyết định — {len(section.items)} câu"]
    for item in section.items:
        lines.append(_render_forced_item(item))
    return "\n\n".join(lines)


def render_parse_failed_section(section: "ReviewSection") -> str:
    """Render the parse-failed section (moderator output unparseable)."""
    if not section.items:
        return ""  # hide section entirely when empty

    lines = [f"## ⚠️ Moderator parse lỗi — {len(section.items)} câu"]
    for item in section.items:
        lines.append(_render_parse_failed_item(item))
    return "\n\n".join(lines)


def render_consensus_section(section: "ReviewSection") -> str:
    """Render collapsed consensus section (one line per item)."""
    if not section.items:
        return "## ✅ Không có câu nào tự giải quyết (consensus)"

    lines = [f"## ✅ Đã resolve qua debate — {len(section.items)} câu"]
    for item in section.items:
        lines.append(_render_consensus_item(item))
    lines.append("\nType `approve all consensus` để xác nhận toàn bộ, hoặc `show <QID>` để xem chi tiết.")
    return "\n".join(lines)


def render_auto_resolved_section(section: "ReviewSection") -> str:
    """Render collapsed auto-resolved OPTIONAL section."""
    if not section.items:
        return ""  # hide when empty

    ids = ", ".join(i.question_id for i in section.items)
    return (
        f"## 🤖 Auto-resolved OPTIONAL — {len(section.items)} câu\n"
        f"Câu: {ids}\n"
        f"Type `expand optional` để xem lý do auto-resolve."
    )


def render_item_detail(item: "ReviewItem") -> str:
    """Render full detail for a single item (used by `show <QID>` command)."""
    header = f"**{item.question_id}** [{item.classification} · {item.domain}]"
    context = f"Context: {item.decision_context}" if item.decision_context else ""
    blocks = f"Sẽ block: {', '.join(item.blocks_what)}" if item.blocks_what else ""
    pos_a = _truncate(item.agent_a_position, _MAX_POSITION_CHARS)
    pos_b = _truncate(item.agent_b_position, _MAX_POSITION_CHARS)

    parts = [
        header,
        "\n".join(p for p in [context, blocks] if p),
        f"**{item.agent_a}:** {pos_a}",
        f"**{item.agent_b}:** {pos_b}",
        f"**Moderator:** {item.moderator_summary}",
        f"*(Confidence: {item.confidence:.2f})*" + (f" — {item.caveat}" if item.caveat else ""),
    ]
    if item.auto_resolution_reason:
        parts.append(f"*Auto-resolved: {item.auto_resolution_reason}*")
    return "\n".join(p for p in parts if p)


def render_optional_expanded(items: list["ReviewItem"]) -> str:
    """Render the full auto-resolved list (used by `expand optional` command)."""
    if not items:
        return "*(Không có câu OPTIONAL nào.)*"

    lines = [f"## 🤖 Auto-resolved OPTIONAL — {len(items)} câu"]
    for item in items:
        reason = item.auto_resolution_reason or "(không có lý do)"
        lines.append(f"- **{item.question_id}** [{item.domain}]: {reason}")
    return "\n".join(lines)


# ---- internal helpers ----


def _render_forced_item(item: "ReviewItem") -> str:
    header = f"**{item.question_id}** [{item.classification} · {item.domain}]"
    context = f"  Context: {item.decision_context}" if item.decision_context else ""
    blocks = f"  Sẽ block: {', '.join(item.blocks_what)}" if item.blocks_what else ""
    pos_a = _truncate(item.agent_a_position, _MAX_POSITION_CHARS)
    pos_b = _truncate(item.agent_b_position, _MAX_POSITION_CHARS)
    confidence_note = (
        f"  *(Confidence: {item.confidence:.2f} — agents fundamentally disagree)*"
        if item.confidence < 0.6
        else f"  *(Confidence: {item.confidence:.2f})*"
    )
    action = (
        f"  → Bạn quyết: `{item.question_id} chọn A` / "
        f"`{item.question_id} chọn B` / "
        f"`{item.question_id} approve moderator` / "
        f"`{item.question_id}: <text riêng>`"
    )
    parts = [
        header,
        "\n".join(p for p in [context, blocks] if p),
        f"  **{item.agent_a}:** {pos_a}",
        f"  **{item.agent_b}:** {pos_b}",
        f"  **Moderator:** {item.moderator_summary}",
        confidence_note,
        action,
    ]
    return "\n".join(p for p in parts if p)


def _render_parse_failed_item(item: "ReviewItem") -> str:
    header = f"⚠️ **{item.question_id}** [{item.classification} · {item.domain}] — Moderator response không parse được"
    context = f"  Context: {item.decision_context}" if item.decision_context else ""
    pos_a = _truncate(item.agent_a_position, _MAX_POSITION_CHARS)
    pos_b = _truncate(item.agent_b_position, _MAX_POSITION_CHARS)
    raw = _truncate(item.raw_moderator_output or "", _MAX_RAW_TRUNCATE)
    raw_block = f"  ```\n  {raw}\n  ```" if raw else ""
    action = (
        f"  → Bạn đọc 2 quan điểm trên và quyết: "
        f"`{item.question_id} chọn A` / "
        f"`{item.question_id} chọn B` / "
        f"`{item.question_id}: <text riêng>`"
    )
    parts = [
        header,
        "\n".join(p for p in [context] if p),
        f"  **{item.agent_a}:** {pos_a}",
        f"  **{item.agent_b}:** {pos_b}",
        "  *Raw moderator output (debug):*",
        raw_block,
        action,
    ]
    return "\n".join(p for p in parts if p)


def _render_consensus_item(item: "ReviewItem") -> str:
    caveat = f" (với caveat: {item.caveat})" if item.caveat else ""
    confidence_str = f"{item.confidence:.2f}"
    return (
        f"✅ **{item.question_id}** [{item.classification} · {item.domain}] "
        f"→ {item.moderator_summary}{caveat} "
        f"*(confidence {confidence_str})*"
    )


def _render_help_footer() -> str:
    return (
        "---\n"
        "**Lệnh:** `<QID> chọn A/B` · `<QID> approve moderator` · `<QID>: <text>` "
        "· `show <QID>` · `show brief` · `expand optional` · `approve all` · `confirm` · `abort`"
    )


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "…"
