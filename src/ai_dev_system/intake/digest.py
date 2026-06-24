"""Brief v2 → ~500-token digest (locked decision #2).

The full brief v2 JSON is ~5–10KB. Streaming it into every downstream LLM call
(debate rounds, materializer, spec generator) burns tokens. This module emits a
compact markdown digest meant to fit in ~500 tokens (≈ 2000 chars at ~4 char/
token for mixed Vietnamese/English) while keeping every critical decision.

Output shape (stable, deterministic):

    # Brief — <project_id> (template <template_id>, src <hash8>)

    ## Critical
    - problem_statement: <value>  [user]
    - scope_in: <items>           [user]
    - …

    ## Context
    - nfr_priority: <ranked list>
    - constraints: <one-line summary>
    - known_unknowns: …

    ## Assumptions
    - <field_id>: skipped (no answer)
    - …

Each line is one field. Long values get truncated to a per-field cap with a
"…" suffix. Lists collapse to "a, b, c (+N more)" when long. Source markers:
  [user]  → typed by human
  [ai]    → AI-suggested + confirmed (treat as ground truth)
  [skip]  → user skipped → also surfaced under "## Assumptions"

The function is pure (no I/O, no LLM, no DB). Determinism is required: same
brief → identical string. Tests rely on this for fixture comparison.
"""
from __future__ import annotations

from typing import Any

# Critical fields (must always appear in the digest, in this order).
CRITICAL_ORDER: tuple[str, ...] = (
    "problem_statement",
    "primary_user",
    "scope_in",
    "scope_out",
    "success_metric",
    "deployment_target",
    "compliance",
    "current_workaround",
)

# Context fields (optional — included if the budget permits, in this order).
CONTEXT_ORDER: tuple[str, ...] = (
    "nfr_priority",
    "must_use_stack",
    "must_not_use",
    "data_residency",
    "budget_infra",
    "existing_auth",
    "must_integrate_with",
    "expected_rps",
    "availability_target",
    "latency_target",
    "known_unknowns",
    "deadline",
    "user_count_now",
)

# Hard char budgets. ~4 chars/token, target 500 tokens → ~2000 chars total.
DEFAULT_MAX_CHARS = 2000

# Per-field value truncation (before considering the whole-digest budget).
PER_FIELD_TRUNC = 220

# Maximum items shown for list fields before collapsing "+N more".
LIST_TAIL_LIMIT = 4

# Source-marker labels (exposed to the LLM, keep short).
_SOURCE_MARKER = {
    "user": "user",
    "ai_suggested_confirmed": "ai",
    "skipped": "skip",
}


def _short(s: str, cap: int = PER_FIELD_TRUNC) -> str:
    """Trim a string to at most `cap` chars, appending '…' if truncated.

    Whitespace is collapsed to single spaces so multi-line answers fit on one
    line in the digest.
    """
    if s is None:
        return ""
    flat = " ".join(s.split())
    if len(flat) <= cap:
        return flat
    return flat[: cap - 1].rstrip() + "…"


def _format_value(value: Any) -> str:
    """Render a brief field value as a single line, deterministic."""
    if value is None:
        return ""
    if isinstance(value, str):
        return _short(value)
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        items = [_short(str(item), 60) for item in value if item is not None]
        if not items:
            return ""
        head, tail = items[:LIST_TAIL_LIMIT], items[LIST_TAIL_LIMIT:]
        extra = f" (+{len(tail)} more)" if tail else ""
        return _short(", ".join(head) + extra)
    # Dict / other — render JSON-ish but trimmed.
    return _short(str(value))


def _field_entry(brief: dict, field_id: str) -> tuple[str | None, str]:
    """Return (formatted_line, source_label) for one field, or (None, "") if
    the field is absent from brief.fields entirely (template mismatch)."""
    fields = brief.get("fields") or {}
    entry = fields.get(field_id)
    if entry is None:
        return None, ""
    if not isinstance(entry, dict):
        # Lenient: plain value
        return _format_value(entry), "user"

    src = entry.get("source") or "user"
    label = _SOURCE_MARKER.get(src, src)
    if src == "skipped":
        return "(skipped)", label
    return _format_value(entry.get("value")), label


def _header(brief: dict) -> str:
    pid = brief.get("project_id", "?")
    tpl = brief.get("template_id", "?")
    src_hash = brief.get("source_hash") or ""
    short_hash = src_hash[:8] if src_hash else "?"
    return f"# Brief — {pid} (template {tpl}, src {short_hash})"


def brief_digest(brief: dict, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """Compact markdown digest of a brief v2 dict, deterministic + budgeted.

    Returns at most `max_chars` characters. If the constructed digest would
    overflow, context fields are dropped one at a time (least-important last)
    until it fits. Critical fields and assumptions are NEVER dropped.

    Caller is expected to pass a dict matching brief v2 (`brief_version == 2`).
    The function tolerates legacy v1 briefs by best-effort lookup, but produces
    a less useful digest in that case (most fields will be missing).
    """
    if not isinstance(brief, dict):
        raise TypeError(f"brief must be a dict, got {type(brief).__name__}")

    header = _header(brief)
    critical_lines = _section_lines(brief, CRITICAL_ORDER, omit_empty=False)
    context_lines = _section_lines(brief, CONTEXT_ORDER, omit_empty=True)
    assumption_lines = _assumption_lines(brief)

    fixed_parts = [header, "", "## Critical", *critical_lines]
    if assumption_lines:
        fixed_parts.extend(["", "## Assumptions", *assumption_lines])

    # Try shrinking context section until the whole digest fits the budget.
    ctx_lines = list(context_lines)
    while True:
        ctx_block: list[str] = []
        if ctx_lines:
            ctx_block = ["", "## Context", *ctx_lines]
        parts = list(fixed_parts)
        # Insert context block just before Assumptions for readability.
        if assumption_lines and ctx_block:
            ass_idx = parts.index("## Assumptions") - 1  # the blank line
            parts = parts[:ass_idx] + ctx_block + parts[ass_idx:]
        elif ctx_block:
            parts = parts + ctx_block

        out = "\n".join(parts).rstrip() + "\n"
        if len(out) <= max_chars or not ctx_lines:
            break
        ctx_lines.pop()  # drop the least-important context field, retry

    return out


def _section_lines(
    brief: dict, field_order: tuple[str, ...], *, omit_empty: bool,
) -> list[str]:
    """Render a section as a list of '- field: value  [src]' lines.

    `omit_empty=True` skips fields whose value rendered to empty string (useful
    for the Context section, where missing fields just shouldn't appear at
    all). `omit_empty=False` always emits an entry (used for Critical, so a
    reader sees the explicit gap).
    """
    lines: list[str] = []
    for fid in field_order:
        rendered, src = _field_entry(brief, fid)
        if rendered is None:
            if omit_empty:
                continue
            lines.append(f"- {fid}: (missing from template)")
            continue
        if rendered == "" and omit_empty:
            continue
        body = rendered if rendered != "" else "(empty)"
        marker = f"  [{src}]" if src else ""
        lines.append(f"- {fid}: {body}{marker}")
    return lines


def _assumption_lines(brief: dict) -> list[str]:
    raw = brief.get("assumptions") or []
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for fid in raw:
        out.append(f"- {fid}: skipped (no answer)")
    return out


def estimate_tokens(digest: str) -> int:
    """Rough token estimate: 1 token ≈ 4 chars (mixed VN/EN). Off by ±20%.

    Useful for callers that want to assert "this fits in the 500-token slot".
    Not a substitute for a real tokenizer — never use for billing.
    """
    return max(1, (len(digest) + 3) // 4)
