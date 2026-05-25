"""Stage 1 — Decision Inventory.

Reads `brief_v2`, asks the LLM to enumerate the atomic decisions a
human must approve before debate begins. Outputs `list[Decision]`,
each tagged with `brief_field_refs` for G8 re-trigger support
(locked decision #36).

Validation (spec M4.1):
- MIN_DECISIONS <= len(decisions) <= MAX_DECISIONS
- `id` unique
- `classification` ∈ {REQUIRED, STRATEGIC, OPTIONAL}
- `domain_hints` resolved via `debate.domains.resolve_domain`;
  unrecognized values warn DOMAIN_UNRECOGNIZED but do not fail.
- `blocks_what` items SHOULD appear in `brief.scope_in`; mismatches
  warn but do not fail (LLM paraphrasing is common).

Failure mode: 1 retry with error feedback appended to the user
message, then `InventoryError` (or its `InventoryCountError`
subclass for count violations).
"""

import json
import warnings
from pathlib import Path

from ai_dev_system.debate.domains import resolve_domain
from ai_dev_system.debate.questions._prompt_utils import split_prompt as _split_prompt
from ai_dev_system.debate.questions.models import Decision

PROMPT_PATH = Path(__file__).parent / "prompts" / "inventory.txt"

MIN_DECISIONS = 8
MAX_DECISIONS = 25
VALID_CLASSIFICATIONS = ("REQUIRED", "STRATEGIC", "OPTIONAL")


class InventoryError(RuntimeError):
    """Inventory stage failed after retry."""


class InventoryCountError(InventoryError):
    """Decision count outside [MIN_DECISIONS, MAX_DECISIONS]."""


def load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _parse_response(raw: str) -> list[dict]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise InventoryError(f"Inventory response is not valid JSON: {e}") from e
    if not isinstance(data, list):
        raise InventoryError(
            f"Inventory response must be a JSON array, got {type(data).__name__}"
        )
    return data


def _validate(raw_items: list[dict], brief_v2: dict) -> list[Decision]:
    count = len(raw_items)
    if count < MIN_DECISIONS or count > MAX_DECISIONS:
        raise InventoryCountError(
            f"Inventory returned {count} decisions; expected "
            f"{MIN_DECISIONS}..{MAX_DECISIONS}"
        )

    scope_in_lower = {str(s).lower() for s in (brief_v2.get("scope_in") or [])}
    seen_ids: set[str] = set()
    decisions: list[Decision] = []

    for idx, item in enumerate(raw_items):
        if not isinstance(item, dict):
            raise InventoryError(
                f"Decision[{idx}] must be a JSON object, got {type(item).__name__}"
            )
        try:
            decision_id = str(item["id"])
            summary = str(item["summary"])
            classification = item["classification"]
        except KeyError as e:
            raise InventoryError(f"Decision[{idx}] missing required field {e}") from e

        if classification not in VALID_CLASSIFICATIONS:
            raise InventoryError(
                f"Decision[{idx}] {decision_id!r} has invalid classification "
                f"{classification!r}; expected one of {VALID_CLASSIFICATIONS}"
            )
        if decision_id in seen_ids:
            raise InventoryError(f"Decision id {decision_id!r} appears more than once")
        seen_ids.add(decision_id)

        canonical_hints: list[str] = []
        for hint in (item.get("domain_hints") or []):
            canonical, recognized = resolve_domain(str(hint))
            if not recognized:
                warnings.warn(
                    f"DOMAIN_UNRECOGNIZED: decision {decision_id!r} hint="
                    f"{hint!r}; defaulted to {canonical!r}",
                    stacklevel=2,
                )
            if canonical not in canonical_hints:
                canonical_hints.append(canonical)

        raw_blocks = [str(b) for b in (item.get("blocks_what") or [])]
        if scope_in_lower:
            for blk in raw_blocks:
                if blk.lower() not in scope_in_lower:
                    warnings.warn(
                        f"Decision {decision_id!r} blocks_what entry "
                        f"{blk!r} not in brief.scope_in",
                        stacklevel=2,
                    )

        decisions.append(Decision(
            id=decision_id,
            summary=summary,
            classification=classification,
            domain_hints=canonical_hints,
            blocks_what=raw_blocks,
            has_safe_default=bool(item.get("has_safe_default", False)),
            brief_field_refs=[str(f) for f in (item.get("brief_field_refs") or [])],
        ))

    return decisions


def run(brief_v2: dict, llm_client) -> list[Decision]:
    """Execute Stage 1.

    Args:
        brief_v2: Promoted intake brief (brief_version == 2).
        llm_client: object with `.complete(system, user) -> str`.

    Returns:
        Validated `list[Decision]`.

    Raises:
        InventoryError: parse or validation failed on both attempts.
        InventoryCountError: count outside the configured range on the
            final attempt (still an `InventoryError`).
    """
    system, user_template = _split_prompt(load_prompt())
    brief_json = json.dumps(brief_v2, ensure_ascii=False, indent=2)
    user = user_template.replace("{brief_v2_json}", brief_json)

    last_error: InventoryError | None = None
    for attempt in range(2):
        try:
            response = llm_client.complete(system=system, user=user)
            raw_items = _parse_response(response)
            return _validate(raw_items, brief_v2)
        except InventoryError as e:
            last_error = e
            if attempt == 0:
                user = (
                    f"{user}\n\n"
                    f"PREVIOUS ATTEMPT FAILED: {e}\n"
                    f"Fix the issue and return only the corrected JSON array."
                )
                continue
            raise

    raise InventoryError("Inventory stage reached unreachable branch") from last_error
