# src/ai_dev_system/gate/gate1_review/sections.py
"""Gate 1 review — sections builder (G2).

Splits debate report results into 4 review sections based on resolution status.

Section assignment rules (spec gate1-skill-redesign §Sections Builder):

    ESCALATE_TO_HUMAN            → forced      (needs human decision)
    NEED_MORE_EVIDENCE           → forced      (treated same as ESCALATE after max rounds)
    MODERATOR_PARSE_FAILED       → parse_failed (different UI: show raw output)
    RESOLVED / RESOLVED_WITH_CAVEAT → consensus (AI agreed, human can override)
    auto-resolved OPTIONAL       → auto_resolved (auto_resolution_reason set by auto_resolve())

Detection of auto-resolved items: `final.auto_resolution_reason` is non-null. The
`auto_resolve()` function always populates this field for OPTIONAL questions; real
debate rounds leave it None, so the field cleanly discriminates the two paths.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from ai_dev_system.debate.questions.models import Decision
from ai_dev_system.gate.gate1_review.loader import GateReviewContext

SectionName = Literal["forced", "parse_failed", "consensus", "auto_resolved"]

_FORCED_STATUSES = frozenset({"ESCALATE_TO_HUMAN", "NEED_MORE_EVIDENCE"})
_CONSENSUS_STATUSES = frozenset({"RESOLVED", "RESOLVED_WITH_CAVEAT"})


@dataclass
class ReviewItem:
    question_id: str
    question_text: str
    classification: str
    domain: str
    decision_context: str        # decision.summary (empty string for legacy/no-match)
    blocks_what: list[str]       # from decision; empty list for legacy
    agent_a: str
    agent_b: str
    agent_a_position: str        # last real debate round's position
    agent_b_position: str
    moderator_summary: str
    confidence: float
    resolution_status: str       # includes MODERATOR_PARSE_FAILED
    caveat: str | None
    auto_resolution_reason: str | None   # non-null only for auto-resolved OPTIONAL
    raw_moderator_output: str | None     # parse-failed only (= moderator_summary for those)


@dataclass
class ReviewSection:
    name: SectionName
    items: list[ReviewItem] = field(default_factory=list)
    collapsed_by_default: bool = False

    def pending_count(self) -> int:
        """Number of items that still need a human decision."""
        if self.name in ("forced", "parse_failed"):
            return len(self.items)
        return 0


def build_sections(ctx: GateReviewContext) -> list[ReviewSection]:
    """Build the 4 Gate 1 review sections from a loaded GateReviewContext.

    Returns sections in this order: [forced, parse_failed, consensus, auto_resolved].
    Sections with no items are still returned (empty) so renderers can show
    "0 câu cần quyết định" rather than omitting the section entirely.
    """
    forced = ReviewSection(name="forced", collapsed_by_default=False)
    parse_failed = ReviewSection(name="parse_failed", collapsed_by_default=False)
    consensus = ReviewSection(name="consensus", collapsed_by_default=True)
    auto_resolved = ReviewSection(name="auto_resolved", collapsed_by_default=True)

    section_map: dict[SectionName, ReviewSection] = {
        "forced": forced,
        "parse_failed": parse_failed,
        "consensus": consensus,
        "auto_resolved": auto_resolved,
    }

    for qdr in ctx.debate_report.get("results", []):
        item = _build_review_item(qdr, ctx.decision_by_id)
        section_name = _classify_item(item)
        section_map[section_name].items.append(item)

    return [forced, parse_failed, consensus, auto_resolved]


def total_pending(sections: list[ReviewSection]) -> int:
    return sum(s.pending_count() for s in sections)


# ---- internal helpers ----


def _classify_item(item: ReviewItem) -> SectionName:
    """Map a ReviewItem to one of the 4 section names."""
    # Auto-resolved OPTIONAL: discriminated by non-null auto_resolution_reason
    if item.auto_resolution_reason is not None:
        return "auto_resolved"

    if item.resolution_status == "MODERATOR_PARSE_FAILED":
        return "parse_failed"

    if item.resolution_status in _FORCED_STATUSES:
        return "forced"

    # RESOLVED / RESOLVED_WITH_CAVEAT (and any future consensus-type statuses)
    return "consensus"


def _build_review_item(
    qdr: dict,
    decision_by_id: dict[str, Decision],
) -> ReviewItem:
    """Construct a ReviewItem from a raw QuestionDebateResult dict."""
    q = qdr["question"]
    final = qdr["final"]
    status = final["resolution_status"]

    # Decision context (empty string for legacy / unmatched)
    source_id = q.get("source_decision_id")
    decision = decision_by_id.get(source_id) if source_id else None
    decision_context = decision.summary if decision else ""
    blocks_what = list(decision.blocks_what) if decision else []

    # raw_moderator_output only meaningful for MODERATOR_PARSE_FAILED
    # In that case, moderator.py stores the raw text in moderator_summary[:500].
    raw_moderator_output: str | None = None
    if status == "MODERATOR_PARSE_FAILED":
        raw_moderator_output = final.get("moderator_summary", "")

    return ReviewItem(
        question_id=q["id"],
        question_text=q["text"],
        classification=q["classification"],
        domain=q["domain"],
        decision_context=decision_context,
        blocks_what=blocks_what,
        agent_a=q["agent_a"],
        agent_b=q["agent_b"],
        agent_a_position=final.get("agent_a_position", ""),
        agent_b_position=final.get("agent_b_position", ""),
        moderator_summary=final.get("moderator_summary", ""),
        confidence=float(final.get("confidence", 0.0)),
        resolution_status=status,
        caveat=final.get("caveat"),
        auto_resolution_reason=final.get("auto_resolution_reason"),
        raw_moderator_output=raw_moderator_output,
    )
