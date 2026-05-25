"""Moderator response parsing + retry orchestration (M5.C, spec D5).

The v1 inline `json.loads` failure path silently downgraded to
`NEED_MORE_EVIDENCE`, which became indistinguishable from a genuine
moderator verdict of "I need more evidence". M5.C separates the two
by:

1. Returning a typed ParseFailReason for each kind of validation
   failure (JSON_INVALID / MISSING_FIELDS / INVALID_STATUS /
   INVALID_CONFIDENCE).
2. Extracting JSON from prose-wrapped responses (LLMs frequently
   wrap their JSON in ```json fences) before declaring parse fail.
3. Retrying with an explicit error-feedback prompt up to
   `MAX_MODERATOR_RETRIES` (= 2) times.
4. When all retries are exhausted, returning a RoundResult with
   `resolution_status="MODERATOR_PARSE_FAILED"` so Gate 1 can route
   it through the parse-failed UI section instead of mixing it with
   normal escalations (locked decision #42).
"""

import json
import re
from enum import Enum

from ai_dev_system.debate.report import RoundResult

MAX_MODERATOR_RETRIES = 2

VALID_MODERATOR_STATUSES = frozenset(
    {"RESOLVED", "RESOLVED_WITH_CAVEAT", "ESCALATE_TO_HUMAN", "NEED_MORE_EVIDENCE"}
)
# MODERATOR_PARSE_FAILED is intentionally NOT in VALID_MODERATOR_STATUSES.
# It is reserved for the orchestrator to assign when retries exhaust;
# the moderator itself emitting that string would be invalid.

REQUIRED_FIELDS = ("status", "confidence", "summary")


class ParseFailReason(str, Enum):
    JSON_INVALID = "json_invalid"
    MISSING_FIELDS = "missing_fields"
    INVALID_STATUS = "invalid_status"
    INVALID_CONFIDENCE = "invalid_confidence"


_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)


def extract_json_block(text: str) -> dict | None:
    """Try to find a JSON object in `text`.

    Handles three shapes:
        1. Pure JSON: `{"a": 1}`
        2. Fenced JSON: ```` ```json\n{"a": 1}\n``` ````
        3. JSON embedded in prose: `Sure! Here is the result: {"a": 1}.`

    Returns the first dict it can decode, or None if nothing parses.
    """
    candidate = text.strip()

    # Pure attempt
    try:
        data = json.loads(candidate)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    # Fenced ```json block
    fence_match = _FENCE_RE.search(candidate)
    if fence_match:
        inner = fence_match.group(1).strip()
        try:
            data = json.loads(inner)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    # Scan for first {...} that decodes via raw_decode
    decoder = json.JSONDecoder()
    for i, ch in enumerate(candidate):
        if ch != "{":
            continue
        try:
            data, _end = decoder.raw_decode(candidate[i:])
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data

    return None


def parse_moderator_response(
    raw: str,
) -> tuple[dict | None, ParseFailReason | None]:
    """Parse + validate a moderator LLM response.

    Returns:
        (validated_dict, None) on success — dict has at least
            status (valid), confidence (0..1 float), summary (str),
            caveat (optional).
        (None, ParseFailReason) on any failure; the caller decides
            whether to retry or surface MODERATOR_PARSE_FAILED.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None, ParseFailReason.JSON_INVALID

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = extract_json_block(raw)
        if data is None:
            return None, ParseFailReason.JSON_INVALID

    if not isinstance(data, dict):
        return None, ParseFailReason.JSON_INVALID

    if not all(k in data for k in REQUIRED_FIELDS):
        return None, ParseFailReason.MISSING_FIELDS

    if data["status"] not in VALID_MODERATOR_STATUSES:
        return None, ParseFailReason.INVALID_STATUS

    try:
        confidence = float(data["confidence"])
    except (TypeError, ValueError):
        return None, ParseFailReason.INVALID_CONFIDENCE
    if not (0.0 <= confidence <= 1.0):
        return None, ParseFailReason.INVALID_CONFIDENCE
    data["confidence"] = confidence  # normalised float

    return data, None


def _build_round_result(
    parsed: dict,
    *,
    round_number: int,
    agent_a_position: str,
    agent_b_position: str,
) -> RoundResult:
    return RoundResult(
        round_number=round_number,
        agent_a_position=agent_a_position,
        agent_b_position=agent_b_position,
        moderator_summary=str(parsed["summary"]),
        resolution_status=parsed["status"],
        confidence=parsed["confidence"],
        caveat=parsed.get("caveat"),
    )


def _build_parse_failed_result(
    raw: str,
    reason: ParseFailReason,
    retries: int,
    *,
    round_number: int,
    agent_a_position: str,
    agent_b_position: str,
) -> RoundResult:
    return RoundResult(
        round_number=round_number,
        agent_a_position=agent_a_position,
        agent_b_position=agent_b_position,
        moderator_summary=raw[:500],
        resolution_status="MODERATOR_PARSE_FAILED",
        confidence=0.0,
        caveat=f"Moderator failed JSON {retries} times; last reason: {reason.value}",
    )


def run_moderator(
    llm_client,
    system_prompt: str,
    user_context: str,
    *,
    round_number: int,
    agent_a_position: str,
    agent_b_position: str,
    max_retries: int = MAX_MODERATOR_RETRIES,
) -> RoundResult:
    """Call the moderator LLM with retry on parse failure.

    Args:
        llm_client: object with `.complete(system, user) -> str`.
        system_prompt: moderator system instruction.
        user_context: initial debate context (question + positions).
        round_number / agent_a_position / agent_b_position: passed
            through to RoundResult so the caller does not need to
            re-attach them.
        max_retries: total attempts (initial + retries). Default 2.

    Returns:
        RoundResult. On exhausted retries the result has
        resolution_status="MODERATOR_PARSE_FAILED", confidence=0.0,
        moderator_summary=raw[:500], and a caveat describing the
        last parse-fail reason.
    """
    if max_retries < 1:
        raise ValueError("max_retries must be >= 1")

    current_context = user_context
    last_raw = ""
    last_reason: ParseFailReason | None = None

    for _ in range(max_retries):
        last_raw = llm_client.complete(system=system_prompt, user=current_context)
        parsed, reason = parse_moderator_response(last_raw)
        if parsed is not None:
            return _build_round_result(
                parsed,
                round_number=round_number,
                agent_a_position=agent_a_position,
                agent_b_position=agent_b_position,
            )
        last_reason = reason
        current_context = (
            f"{user_context}\n\n"
            f"Previous response failed parsing: {reason.value}. "
            f"Return STRICT JSON only with keys: "
            f"{', '.join(REQUIRED_FIELDS)} (+ optional caveat)."
        )

    return _build_parse_failed_result(
        last_raw,
        last_reason or ParseFailReason.JSON_INVALID,
        max_retries,
        round_number=round_number,
        agent_a_position=agent_a_position,
        agent_b_position=agent_b_position,
    )
