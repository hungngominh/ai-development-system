"""Stage 2 — Question Materializer.

Converts each `Decision` into one `Question`. Runs in batch mode by
default (one LLM call for all decisions) and falls back to
per-decision calls when the batch response fails to parse.

Classification is *derived* from Decision properties — the LLM's
classification field is ignored:

- blocks_what non-empty AND has_safe_default = False → REQUIRED
- blocks_what non-empty AND has_safe_default = True  → STRATEGIC
- blocks_what empty                                  → OPTIONAL

Domain is resolved via `debate.domains.resolve_domain`; unknown values
warn DOMAIN_UNRECOGNIZED and fall back to backend. Invalid agent keys
fall back to DEFAULT_AGENT_A / DEFAULT_AGENT_B.

Modes:
- "fresh": materialize all decisions (called by `pipeline.run`).
- "retrigger": materialize the given subset (called by Gate 1 G8 per
  `2026-05-25-g8-brief-edit-retrigger.md`). The id suffix `-r{N}` is
  the caller's responsibility.
"""

import json
import warnings
from pathlib import Path
from typing import Literal

from ai_dev_system.debate.agents import VALID_AGENT_KEYS
from ai_dev_system.debate.domains import resolve_domain
from ai_dev_system.debate.questions._prompt_utils import split_prompt as _split_prompt
from ai_dev_system.debate.questions.models import Decision
from ai_dev_system.debate.report import Question

PROMPT_PATH = Path(__file__).parent / "prompts" / "materializer.txt"

DEFAULT_AGENT_A = "BackendArchitect"
DEFAULT_AGENT_B = "ProductManager"
DEFAULT_DOMAIN = "backend"

Mode = Literal["fresh", "retrigger"]


class MaterializerError(RuntimeError):
    """Materialization failed in both batch and per-decision fallback."""


def load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _derive_classification(decision: Decision) -> str:
    if not decision.blocks_what:
        return "OPTIONAL"
    if decision.has_safe_default:
        return "STRATEGIC"
    return "REQUIRED"


def _decision_to_payload(decision: Decision) -> dict:
    return {
        "id": decision.id,
        "summary": decision.summary,
        "blocks_what": decision.blocks_what,
        "has_safe_default": decision.has_safe_default,
        "domain_hints": decision.domain_hints,
    }


def _render_user(
    user_template: str, decisions: list[Decision], brief_digest: str
) -> str:
    payload = [_decision_to_payload(d) for d in decisions]
    decisions_json = json.dumps(payload, ensure_ascii=False, indent=2)
    return user_template.replace("{decisions_json}", decisions_json).replace(
        "{brief_digest}", brief_digest
    )


def _normalize(item: dict, decision: Decision, index: int) -> Question:
    """Build a Question from a raw LLM dict + the authoritative Decision."""
    try:
        text = str(item["text"])
    except KeyError as e:
        raise MaterializerError(
            f"Question for decision {decision.id!r} missing required field {e}"
        ) from e

    question_id = str(item.get("id") or f"Q{index + 1}")

    primary_hint = decision.domain_hints[0] if decision.domain_hints else DEFAULT_DOMAIN
    raw_domain = str(item.get("domain") or primary_hint)
    canonical_domain, recognized = resolve_domain(raw_domain)
    if not recognized:
        warnings.warn(
            f"DOMAIN_UNRECOGNIZED: question {question_id!r} domain="
            f"{raw_domain!r}; defaulted to {canonical_domain!r}",
            stacklevel=2,
        )

    agent_a = str(item.get("agent_a") or "")
    if agent_a not in VALID_AGENT_KEYS:
        agent_a = DEFAULT_AGENT_A
    agent_b = str(item.get("agent_b") or "")
    if agent_b not in VALID_AGENT_KEYS:
        agent_b = DEFAULT_AGENT_B

    return Question(
        id=question_id,
        text=text,
        classification=_derive_classification(decision),
        domain=canonical_domain,
        agent_a=agent_a,
        agent_b=agent_b,
        source_decision_id=decision.id,
    )


def _materialize_batch(
    decisions: list[Decision],
    brief_digest: str,
    llm_client,
    system: str,
    user_template: str,
) -> list[Question]:
    user = _render_user(user_template, decisions, brief_digest)
    response = llm_client.complete(system=system, user=user)

    try:
        raw = json.loads(response)
    except json.JSONDecodeError as e:
        raise MaterializerError(f"Batch response is not valid JSON: {e}") from e
    if not isinstance(raw, list):
        raise MaterializerError(
            f"Batch response must be a JSON array, got {type(raw).__name__}"
        )

    decision_by_id = {d.id: d for d in decisions}
    questions: list[Question] = []
    seen_decision_ids: set[str] = set()

    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            raise MaterializerError(
                f"Batch[{idx}] must be a JSON object, got {type(item).__name__}"
            )
        source_id = str(item.get("source_decision_id") or "")
        if source_id not in decision_by_id:
            raise MaterializerError(
                f"Batch[{idx}] source_decision_id={source_id!r} not in input decisions"
            )
        questions.append(_normalize(item, decision_by_id[source_id], idx))
        seen_decision_ids.add(source_id)

    missing = [d.id for d in decisions if d.id not in seen_decision_ids]
    if missing:
        warnings.warn(
            f"Materializer batch skipped decisions: {missing}",
            stacklevel=2,
        )

    return questions


def _materialize_per_decision(
    decisions: list[Decision],
    brief_digest: str,
    llm_client,
    system: str,
    user_template: str,
) -> list[Question]:
    questions: list[Question] = []
    last_error: Exception | None = None

    for idx, decision in enumerate(decisions):
        user = _render_user(user_template, [decision], brief_digest)
        try:
            response = llm_client.complete(system=system, user=user)
            raw = json.loads(response)
            if isinstance(raw, list) and raw:
                item = raw[0]
            elif isinstance(raw, dict):
                item = raw
            else:
                raise MaterializerError(
                    f"Per-decision response for {decision.id!r} not parseable: "
                    f"{type(raw).__name__}"
                )
            if not isinstance(item, dict):
                raise MaterializerError(
                    f"Per-decision item for {decision.id!r} must be dict"
                )
            questions.append(_normalize(item, decision, idx))
        except (json.JSONDecodeError, MaterializerError, KeyError) as e:
            last_error = e
            warnings.warn(
                f"Per-decision materialization for {decision.id!r} failed: {e}",
                stacklevel=2,
            )
            continue

    if not questions:
        raise MaterializerError(
            "Per-decision fallback produced zero questions"
        ) from last_error
    return questions


def run(
    decisions: list[Decision],
    brief_digest: str,
    llm_client,
    *,
    mode: Mode = "fresh",
) -> list[Question]:
    """Execute Stage 2.

    Args:
        decisions: Output of Stage 1, or a subset for retrigger mode.
        brief_digest: 500-token compressed brief (locked decision #2).
        llm_client: object with `.complete(system, user) -> str`.
        mode: "fresh" or "retrigger" (informational; caller handles
            ID suffixing for retrigger per G8 mini-spec).

    Returns:
        `list[Question]`. Empty input → empty output.

    Raises:
        MaterializerError: per-decision fallback also produced zero
            questions (catastrophic LLM failure).
    """
    if not decisions:
        return []

    system, user_template = _split_prompt(load_prompt())

    try:
        return _materialize_batch(
            decisions, brief_digest, llm_client, system, user_template
        )
    except MaterializerError as batch_err:
        warnings.warn(
            f"Batch materialization failed ({batch_err}); falling back to per-decision",
            stacklevel=2,
        )
        return _materialize_per_decision(
            decisions, brief_digest, llm_client, system, user_template
        )
