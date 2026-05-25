"""Stage 3 — Critic Loop.

Iteratively reviews materialized questions and applies the critic's
flagged actions. Per locked decision #10, a sha256 of every question
text (originals and accepted rewrites) is tracked; if a rewrite
produces a text whose hash was previously seen, the question is
forced into `drop` rather than rewritten again. This prevents A→B→A
oscillation between iterations.

Flags (4): SHALLOW, OUT_OF_SCOPE, ALREADY_DECIDED, DUPLICATE.
Actions (4): rewrite, drop, merge, keep.

Exit conditions:
- critic returns no flags → done
- iterations reach `max_iter` (default 2) → done
- surviving questions drop below `MIN_SURVIVING_QUESTIONS` (5) →
  emit warning and stop early so the pipeline still has something to
  deliver

Degraded handling: critic JSON parse failure is treated as "no flags"
(loop exits cleanly). Per-rewrite parse failures convert that flag
into a drop with a warning. Catastrophic LLM exceptions propagate to
the caller as `CriticError`.
"""

import dataclasses
import hashlib
import json
import warnings
from pathlib import Path

from ai_dev_system.debate.report import Question

CRITIC_PROMPT_PATH = Path(__file__).parent / "prompts" / "critic.txt"
REWRITE_PROMPT_PATH = Path(__file__).parent / "prompts" / "critic_rewrite.txt"

MAX_CRITIC_ITER = 2
MIN_SURVIVING_QUESTIONS = 5

VALID_FLAGS = {"SHALLOW", "OUT_OF_SCOPE", "ALREADY_DECIDED", "DUPLICATE"}
VALID_ACTIONS = {"rewrite", "drop", "merge", "keep"}


class CriticError(RuntimeError):
    """Critic stage failed in a non-recoverable way."""


def load_critic_prompt() -> str:
    return CRITIC_PROMPT_PATH.read_text(encoding="utf-8")


def load_rewrite_prompt() -> str:
    return REWRITE_PROMPT_PATH.read_text(encoding="utf-8")


def _split_prompt(template: str) -> tuple[str, str]:
    if "\nUSER\n" not in template:
        raise ValueError("Prompt template missing USER section")
    system_block, user_template = template.split("\nUSER\n", 1)
    if system_block.startswith("SYSTEM\n"):
        system_block = system_block[len("SYSTEM\n"):]
    return system_block.strip(), user_template


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _questions_to_payload(questions: list[Question]) -> str:
    return json.dumps(
        [
            {
                "id": q.id,
                "text": q.text,
                "classification": q.classification,
                "domain": q.domain,
                "source_decision_id": q.source_decision_id,
            }
            for q in questions
        ],
        ensure_ascii=False,
        indent=2,
    )


def _call_critic(
    questions: list[Question],
    brief_digest: str,
    llm_client,
    system: str,
    user_template: str,
) -> list[dict]:
    """Run one critic review pass. Returns the flag list, possibly empty.

    Invalid JSON or non-list responses degrade to "no flags" with a
    warning so the loop can exit cleanly rather than crash.
    """
    user = user_template.replace(
        "{questions_json}", _questions_to_payload(questions)
    ).replace("{brief_digest}", brief_digest)
    response = llm_client.complete(system=system, user=user)

    try:
        flags = json.loads(response)
    except json.JSONDecodeError as e:
        warnings.warn(f"Critic response not valid JSON: {e}; treating as no flags", stacklevel=2)
        return []
    if not isinstance(flags, list):
        warnings.warn(
            f"Critic response must be a JSON array, got {type(flags).__name__}; "
            f"treating as no flags",
            stacklevel=2,
        )
        return []
    return [f for f in flags if isinstance(f, dict)]


def _call_rewrite(
    question: Question,
    flag: str,
    reason: str,
    brief_digest: str,
    llm_client,
    system: str,
    user_template: str,
) -> str | None:
    """Run one rewrite call. Returns new text or None on failure.

    A None return signals the caller to convert the rewrite action
    into a drop.
    """
    question_json = json.dumps(
        {"id": question.id, "text": question.text},
        ensure_ascii=False,
    )
    user = (
        user_template.replace("{question_json}", question_json)
        .replace("{flag}", flag)
        .replace("{reason}", reason)
        .replace("{brief_digest}", brief_digest)
    )
    response = llm_client.complete(system=system, user=user)
    try:
        payload = json.loads(response)
    except json.JSONDecodeError as e:
        warnings.warn(
            f"Rewrite response for {question.id!r} not valid JSON: {e}",
            stacklevel=2,
        )
        return None
    if not isinstance(payload, dict):
        warnings.warn(
            f"Rewrite response for {question.id!r} must be an object",
            stacklevel=2,
        )
        return None
    new_text = payload.get("new_text")
    if not isinstance(new_text, str) or not new_text.strip():
        warnings.warn(
            f"Rewrite response for {question.id!r} missing or empty new_text",
            stacklevel=2,
        )
        return None
    return new_text


def _apply_flags(
    current: list[Question],
    flags: list[dict],
    brief_digest: str,
    llm_client,
    rewrite_system: str,
    rewrite_user_template: str,
    seen_hashes: set[str],
) -> list[Question]:
    """Apply the critic's flag list to the current question set.

    Returns a new list with drops removed, merges absorbed, and
    rewrites applied (subject to the sha256 loop guard).
    """
    by_id = {q.id: q for q in current}
    dropped_ids: set[str] = set()
    merged_ids: set[str] = set()
    rewrites: dict[str, str] = {}

    for flag in flags:
        qid = str(flag.get("question_id") or "")
        action = flag.get("action")
        flag_name = str(flag.get("flag") or "")
        reason = str(flag.get("reason") or "")

        if qid not in by_id:
            warnings.warn(
                f"Critic flagged unknown question_id={qid!r}; ignored",
                stacklevel=2,
            )
            continue
        if action not in VALID_ACTIONS:
            warnings.warn(
                f"Critic emitted invalid action={action!r} for {qid!r}; ignored",
                stacklevel=2,
            )
            continue
        if flag_name and flag_name not in VALID_FLAGS:
            warnings.warn(
                f"Critic emitted invalid flag={flag_name!r} for {qid!r}; ignored",
                stacklevel=2,
            )
            continue

        if action == "keep":
            continue
        if action == "drop":
            dropped_ids.add(qid)
            continue
        if action == "merge":
            target = str(flag.get("merge_into") or "")
            if not target or target not in by_id or target == qid:
                warnings.warn(
                    f"Critic merge for {qid!r} has bad merge_into={target!r}; "
                    f"converting to drop",
                    stacklevel=2,
                )
                dropped_ids.add(qid)
            else:
                merged_ids.add(qid)
            continue
        if action == "rewrite":
            new_text = _call_rewrite(
                by_id[qid],
                flag_name,
                reason,
                brief_digest,
                llm_client,
                rewrite_system,
                rewrite_user_template,
            )
            if new_text is None:
                dropped_ids.add(qid)
                continue
            new_hash = _hash_text(new_text)
            if new_hash in seen_hashes:
                warnings.warn(
                    f"Critic loop guard: rewrite for {qid!r} produced "
                    f"previously-seen text; forcing drop",
                    stacklevel=2,
                )
                dropped_ids.add(qid)
                continue
            seen_hashes.add(new_hash)
            rewrites[qid] = new_text

    result: list[Question] = []
    for q in current:
        if q.id in dropped_ids or q.id in merged_ids:
            continue
        if q.id in rewrites:
            q = dataclasses.replace(q, text=rewrites[q.id])
        result.append(q)
    return result


def run(
    questions: list[Question],
    brief_digest: str,
    llm_client,
    *,
    max_iter: int = MAX_CRITIC_ITER,
) -> tuple[list[Question], int]:
    """Execute Stage 3.

    Args:
        questions: Output of Stage 2.
        brief_digest: 500-token compressed brief.
        llm_client: object with `.complete(system, user) -> str`.
        max_iter: hard cap on critic iterations (default 2).

    Returns:
        Tuple `(refined_questions, iterations_run)`. Caller emits one
        `CRITIC_ITERATION_DONE` event per iteration with the count.
    """
    if not questions:
        return [], 0

    critic_system, critic_user = _split_prompt(load_critic_prompt())
    rewrite_system, rewrite_user = _split_prompt(load_rewrite_prompt())

    current = list(questions)
    seen_hashes: set[str] = {_hash_text(q.text) for q in current}

    iterations = 0
    while iterations < max_iter:
        flags = _call_critic(current, brief_digest, llm_client, critic_system, critic_user)
        if not flags:
            break
        iterations += 1
        current = _apply_flags(
            current,
            flags,
            brief_digest,
            llm_client,
            rewrite_system,
            rewrite_user,
            seen_hashes,
        )
        if len(current) < MIN_SURVIVING_QUESTIONS:
            warnings.warn(
                f"Critic reduced surviving questions to {len(current)} "
                f"(< {MIN_SURVIVING_QUESTIONS}); stopping early",
                stacklevel=2,
            )
            break

    return current, iterations
