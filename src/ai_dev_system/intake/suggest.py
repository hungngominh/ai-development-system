"""LLM-driven field-value suggester for the intake wizard.

Decision #1 (locked): cache suggestions in session keyed by `(field_id, source_hash)`
of the partial brief. Cache invalidates implicitly when the brief changes (a new
hash is computed). Scope: a single wizard run — no on-disk cache, no cross-run
sharing.

LLM protocol: the suggester only needs `LLMClient.complete(system, user) -> str`
which matches the existing `RealLLMClient` and `StubDebateLLMClient`. Tests can
pass any object with that method.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Protocol

from ai_dev_system.intake.suggest_deps import resolve_dependencies
from ai_dev_system.intake.template import Template, TemplateField

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LLM protocol
# ---------------------------------------------------------------------------

class LLMClient(Protocol):
    def complete(self, system: str, user: str) -> str:  # pragma: no cover - protocol
        ...


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class SuggestionProposal:
    """What the Suggester returns to the engine when a `?` is requested."""
    field_id: str
    suggestion: Any            # str | list[str] depending on field type
    rationale: str
    cache_hit: bool = False


class SuggestRefusedError(Exception):
    """Raised when a field's template flags it as `ai_can_suggest: false`."""


class SuggestParseError(Exception):
    """LLM returned something we couldn't parse into {suggestion, rationale}."""


# ---------------------------------------------------------------------------
# Brief hashing for cache keys
# ---------------------------------------------------------------------------

def _stable_hash_brief(answers: Mapping[str, Any]) -> str:
    """Hash the set of *user* answers so the cache stays sound under edits.

    `answers` here is the {field_id → FieldAnswer} dict from IntakeState. We
    extract `(field_id, value, source)` tuples sorted by id and hash the JSON.
    Skipped/null answers count toward the hash so caches invalidate when a user
    fills a previously-skipped field.
    """
    parts = []
    for fid in sorted(answers.keys()):
        ans = answers[fid]
        # ans may be a FieldAnswer dataclass; serialize uniformly
        value = getattr(ans, "value", None)
        source = getattr(ans, "source", None)
        parts.append([fid, value, source])
    blob = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "Bạn đang giúp user xác định một field trong brief dự án phần mềm. "
    "Trả về DUY NHẤT một JSON object dạng "
    '{"suggestion": <value>, "rationale": "<lý do, ≤2 câu>"} '
    "— không có text nào khác, không có markdown code fence. "
    "Nếu field type là `list_str`, suggestion phải là JSON array of strings. "
    "Nếu field type là `enum`, suggestion phải là một trong các options đã cho. "
    "Nếu thật sự không đủ context để đề xuất hợp lý, trả về "
    '{"suggestion": null, "rationale": "<giải thích>"}.'
)


def _render_context(
    field: TemplateField,
    template: Template,
    answers: Mapping[str, Any],
) -> str:
    """Build the user-message context: relevant fields + target field spec."""
    dep_ids = resolve_dependencies(field.id)
    lines: list[str] = ["# Context user đã cung cấp"]
    shown = 0
    for dep_id in dep_ids:
        ans = answers.get(dep_id)
        if ans is None:
            continue
        value = getattr(ans, "value", None)
        if value is None or value == "":
            continue
        try:
            dep_field = template.field_by_id(dep_id)
            label = dep_field.prompt
        except KeyError:
            label = dep_id
        if isinstance(value, list):
            value_str = ", ".join(str(v) for v in value)
        else:
            value_str = str(value)
        lines.append(f"- **{dep_id}** ({label}): {value_str}")
        shown += 1
    if shown == 0:
        lines.append("(chưa có field nào liên quan được trả lời)")

    lines.append("")
    lines.append("# Field cần đề xuất")
    lines.append(f"- id: {field.id}")
    lines.append(f"- prompt: {field.prompt}")
    lines.append(f"- type: {field.type}")
    if field.type == "enum":
        lines.append(f"- options: {' | '.join(field.options)}")
    if field.examples_hint:
        lines.append(f"- hint: {field.examples_hint}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _parse_response(raw: str, field: TemplateField) -> tuple[Any, str]:
    """Parse the LLM reply into (suggestion, rationale).

    Raises SuggestParseError on any structural problem.
    """
    text = raw.strip()
    m = _FENCE_RE.search(text)
    if m:
        text = m.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SuggestParseError(f"non-JSON reply: {raw[:200]!r}") from exc
    if not isinstance(data, dict) or "suggestion" not in data or "rationale" not in data:
        raise SuggestParseError(f"missing keys in reply: {data!r}")

    suggestion = data["suggestion"]
    rationale = str(data["rationale"]).strip()
    if not rationale:
        raise SuggestParseError("empty rationale")

    # Type coercion / validation
    if suggestion is None:
        return None, rationale

    if field.type == "list_str":
        if isinstance(suggestion, str):
            # Tolerate "a, b" string for a list field
            items = [s.strip() for s in re.split(r"[,\n]", suggestion) if s.strip()]
            suggestion = items
        if not isinstance(suggestion, list) or not all(isinstance(s, str) for s in suggestion):
            raise SuggestParseError(f"list_str field needs array of strings, got {suggestion!r}")
        if not suggestion:
            raise SuggestParseError("list_str suggestion is empty list")

    elif field.type == "enum":
        if suggestion not in field.options:
            raise SuggestParseError(
                f"enum suggestion {suggestion!r} not in options {field.options}"
            )

    elif field.type == "number":
        if isinstance(suggestion, str):
            try:
                suggestion = int(suggestion)
            except ValueError as exc:
                raise SuggestParseError(f"number field got non-numeric {suggestion!r}") from exc
        if not isinstance(suggestion, int):
            raise SuggestParseError(f"number field needs int, got {type(suggestion).__name__}")

    else:  # text_short / text_long
        if not isinstance(suggestion, str):
            raise SuggestParseError(f"text field needs string, got {type(suggestion).__name__}")
        suggestion = suggestion.strip()
        if not suggestion:
            raise SuggestParseError("empty text suggestion")

    return suggestion, rationale


# ---------------------------------------------------------------------------
# Suggester
# ---------------------------------------------------------------------------

class Suggester:
    """Generates a single-field proposal. Session-scoped cache.

    Usage:
        sug = Suggester(llm)
        try:
            proposal = sug.propose(template, field, answers_dict)
        except SuggestRefusedError:
            ...  # field marked ai_can_suggest: false
    """

    def __init__(self, llm: LLMClient):
        self._llm = llm
        self._cache: dict[tuple[str, str], SuggestionProposal] = {}

    def propose(
        self,
        template: Template,
        field: TemplateField,
        answers: Mapping[str, Any],
    ) -> SuggestionProposal:
        if not field.ai_can_suggest:
            raise SuggestRefusedError(
                f"Field {field.id!r} is marked ai_can_suggest=false in template"
            )

        brief_hash = _stable_hash_brief(answers)
        cache_key = (field.id, brief_hash)
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            return SuggestionProposal(
                field_id=cached.field_id,
                suggestion=cached.suggestion,
                rationale=cached.rationale,
                cache_hit=True,
            )

        user_msg = _render_context(field, template, answers)
        raw = self._llm.complete(_SYSTEM_PROMPT, user_msg)
        suggestion, rationale = _parse_response(raw, field)

        proposal = SuggestionProposal(
            field_id=field.id,
            suggestion=suggestion,
            rationale=rationale,
        )
        self._cache[cache_key] = proposal
        return proposal

    # Helpful for tests / introspection
    @property
    def cache_size(self) -> int:
        return len(self._cache)

    def clear_cache(self) -> None:
        self._cache.clear()
