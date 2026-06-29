# src/ai_dev_system/rules/learning.py
"""Failure-learning loop.

When a task ends *wrong* — a verification HAS_FAIL or a human gate/Accept
rejection — synthesise a corrective rule scoped to the failed task's
``task_type``/``tags`` and append it to ``rules/definitions/*.yaml`` so that
``RuleRegistry`` injects the lesson into every future task of that kind
(see ``registry.py`` / ``worker.py``). The loop is:

* **selective** — only genuine "built wrong" signals mint a durable rule.
  A transient infra failure (``EXECUTION_ERROR``, already handled by retry in
  ``engine/failure.py``) is ignored, mirroring the retry-vs-final split there.
* **additive** — lessons append to a per-scope YAML file; nothing is mutated
  in agent code, and a learned file can simply be deleted to revert.
* **idempotent** — an identical lesson is never appended twice, so the loop is
  safe to invoke once per failed attempt / retry (``create_retry`` runs per
  attempt).
* **atomic** — files are written temp-then-rename so a half-written YAML can
  never corrupt ``RuleRegistry._load_rules`` for *all* tasks.

A newly written rule only takes effect on the NEXT worker process / pipeline
run, because ``RuleRegistry`` is constructed once at worker module import
(``worker.py``). That is acceptable for a learning loop.

Provenance is recorded in the append-only ``events`` table via
``EventRepo.insert(..., event_type='RULE_LEARNED', ...)`` so every learned rule
traces back to the task_run that produced it and can be audited / reverted.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

# Learned rules live alongside the hand-authored definitions but are prefixed so
# they are easy to spot, audit, and revert (just delete the file).
LEARNED_PREFIX = "learned"

# file_rules are injected verbatim into the agent backstory; keep them short.
MAX_LESSON_LEN = 280

# Markers of a transient/environment-dependent failure. These must NOT harden
# into a permanent rule (they "harden into self-citing refusals"). Borrowed from
# Hermes' background-review DO-NOT-SAVE guardrail — but Hermes applies it as an
# LLM reviewer that judges INTENT, so it never mis-fires. This keyword filter
# cannot judge intent, so it is deliberately scoped to UNAMBIGUOUS infra/transient
# signatures only. Vague negatives ("is broken", "doesn't work", "permission
# denied", "out of memory") were intentionally removed: they also match
# legitimate engineering lessons (e.g. "the retry logic is broken when upstream
# returns 500"). The intent-aware version is the deferred LLM "librarian" follow-up.
# Do NOT use bare "rate limit"/"timeout" — they collide with "rate limiting" and
# legitimate timeout lessons (existing test_learning.py rate-limiting test must stay green).
_DO_NOT_SAVE_MARKERS = (
    "timed out", "connection refused", "connection reset", "econnreset",
    "rate limit exceeded", "429 ", "flaky", "transient failure",
    " 502 ", " 503 ", " 504 ", "command not found", "no such file",
    "module not found", "modulenotfounderror", "disk full",
    "tool is broken",
)


def _is_transient_lesson(text: str) -> bool:
    low = (text or "").lower()
    return any(marker in low for marker in _DO_NOT_SAVE_MARKERS)


@dataclass
class LearnedRule:
    """Result of a learning-loop invocation (mirrors the YAML schema)."""

    rule_name: str
    applies_to: dict = field(default_factory=dict)
    file_rules: list[str] = field(default_factory=list)
    skill_rules: list[str] = field(default_factory=list)
    created: bool = False     # a brand-new rule file was written
    deduped: bool = False     # the lesson already existed → nothing written
    source_task_run_id: Optional[str] = None


# ── lesson extraction ─────────────────────────────────────────────────────────

def _clean(text: str) -> str:
    """Collapse whitespace and clamp to a short imperative string."""
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if len(text) > MAX_LESSON_LEN:
        text = text[: MAX_LESSON_LEN - 1].rstrip() + "…"
    return text


def lessons_from_verification(report) -> list[str]:
    """Derive lesson strings from a VerificationReport's FAIL criteria.

    Lesson text is derived from ``CriterionResult.reasoning`` (falling back to
    ``criterion_text``) — never invented. Returns [] for ALL_PASS reports.
    """
    if report is None or getattr(report, "overall", None) != "HAS_FAIL":
        return []
    lessons: list[str] = []
    for crit in getattr(report, "criteria", []):
        if getattr(crit, "verdict", None) != "FAIL":
            continue
        basis = (getattr(crit, "reasoning", "") or "").strip() \
            or (getattr(crit, "criterion_text", "") or "").strip()
        if not basis:
            continue
        if _is_transient_lesson(basis):
            logger.info("Learning loop: dropping transient verification lesson: %s", basis[:80])
            continue
        lesson = _clean(f"Avoid repeating this failure: {basis}")
        if lesson not in lessons:
            lessons.append(lesson)
    return lessons


def lesson_from_rejection(reason: str) -> list[str]:
    """Derive a lesson from a human gate / webui-Accept rejection reason."""
    cleaned = _clean(reason or "")
    if not cleaned:
        return []
    if _is_transient_lesson(cleaned):
        logger.info("Learning loop: dropping transient rejection lesson: %s", cleaned[:80])
        return []
    return [_clean(f"Reviewer rejected prior output: {cleaned}")]


# ── scope + validation ─────────────────────────────────────────────────────────

def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-") or "task"


def scope_task_from_context(task_run_row: dict, ctx: dict) -> dict:
    """Build the minimal task dict ``learn_from_failure`` needs from a task_runs
    row plus its parsed ``context_snapshot``.

    The engine's task_runs table has no ``task_type``/``tags`` columns — the
    task-graph node's type/tags live inside ``context_snapshot`` (see
    ``materializer._build_context``). This adapter bridges that gap so callers
    pass the shape ``_compute_scope`` expects.
    """
    ctx = ctx or {}
    return {
        "task_run_id": task_run_row.get("task_run_id"),
        "task_type": (ctx.get("type") or "").strip(),
        "tags": list(ctx.get("tags") or []),
    }


def _compute_scope(task: dict) -> Optional[tuple[dict, str]]:
    """Build ``applies_to`` and a deterministic scope key for the failed task.

    Scoped by task_type (so siblings of the same type inherit the lesson) and
    by the originating tags (so tag-only future tasks also match — mirroring how
    ``security.yaml`` lists both). Returns None if neither is present, because
    such a rule could never match (registry.py:44-47).
    """
    task_type = (task.get("task_type") or "").strip()
    tags = sorted({t for t in (task.get("tags") or []) if t})

    if not task_type and not tags:
        return None

    applies_to = {
        "task_types": [task_type] if task_type else [],
        "tags": tags,
    }
    key = _slugify(task_type) if task_type else "tags-" + "-".join(_slugify(t) for t in tags)
    return applies_to, key


def _validate_rule(rule: dict) -> bool:
    """Mirror the implicit schema enforced by RuleRegistry.match_rules().

    A rule is only useful if it has a name, a non-empty applies_to, and at
    least one of file_rules / skill_rules.
    """
    if not isinstance(rule, dict):
        return False
    if not rule.get("name"):
        return False
    applies = rule.get("applies_to") or {}
    if not (applies.get("task_types") or applies.get("tags")):
        return False
    if not (rule.get("file_rules") or rule.get("skill_rules")):
        return False
    return True


# ── atomic file write ──────────────────────────────────────────────────────────

def _atomic_write_yaml(path: Path, data: dict) -> None:
    """Write YAML temp-then-rename so a half-written file never corrupts loads."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    # Validate the round-trip before swapping it into place.
    if not _validate_rule(yaml.safe_load(text)):
        raise ValueError(f"Refusing to write invalid learned rule: {data!r}")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _merge_unique(existing: list, incoming: list) -> tuple[list, bool]:
    """Append items from ``incoming`` not already in ``existing``. Returns
    (merged_list, changed)."""
    merged = list(existing or [])
    changed = False
    for item in incoming:
        if item not in merged:
            merged.append(item)
            changed = True
    return merged, changed


# ── public entry point ──────────────────────────────────────────────────────────

def learn_from_failure(
    conn,
    run_id: str,
    task: dict,
    *,
    rules_dir: Path | str,
    source: str,
    report=None,
    rejection_reason: Optional[str] = None,
    error_type: Optional[str] = None,
    skill_rules: Optional[list[str]] = None,
    actor: str = "learning-loop",
) -> Optional[LearnedRule]:
    """Synthesise/append a corrective rule from a failed task.

    Parameters
    ----------
    source
        ``'verification'`` (use ``report``), ``'gate'`` (use ``rejection_reason``),
        or ``'error'`` (transient — never mints a rule).
    report
        A ``VerificationReport``; only its FAIL criteria are mined.
    rejection_reason
        Free-text reason from a human gate / webui Accept rejection.
    error_type
        The task_run error_type. ``EXECUTION_ERROR`` (transient/infra) never
        mints a rule, regardless of retryability.

    Returns the ``LearnedRule`` describing what was written/deduped, or None if
    the failure was transient, unscopable, or produced no lesson.
    """
    rules_dir = Path(rules_dir)
    task_run_id = task.get("task_run_id")

    # 1) Only genuine "built wrong" signals mint a durable rule.
    if source == "error" or error_type == "EXECUTION_ERROR":
        logger.info("Learning loop: transient failure (%s) — no rule minted", error_type)
        return None

    # 2) Derive lessons from the appropriate signal (never invented).
    if source == "verification":
        file_rules = lessons_from_verification(report)
    elif source == "gate":
        file_rules = lesson_from_rejection(rejection_reason or "")
    else:
        logger.warning("Learning loop: unknown source %r — skipping", source)
        return None

    skill_rules = list(skill_rules or [])
    if not file_rules and not skill_rules:
        logger.warning("Learning loop: empty lesson for task_run %s — skipping", task_run_id)
        return None

    # 3) Scope by task_type / tags; refuse rules that could never match.
    scope = _compute_scope(task)
    if scope is None:
        logger.warning(
            "Learning loop: task_run %s has no task_type/tags to scope a rule — skipping",
            task_run_id,
        )
        return None
    applies_to, scope_key = scope

    rule_name = f"{LEARNED_PREFIX}-{scope_key}"
    rule_path = rules_dir / f"{rule_name}.yaml"

    # 4) Load existing learned rule (if any) and merge idempotently.
    created = not rule_path.exists()
    if created:
        rule = {
            "name": rule_name,
            "applies_to": applies_to,
            "file_rules": [],
            "skill_rules": [],
        }
    else:
        try:
            with open(rule_path, encoding="utf-8") as f:
                rule = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError):
            logger.exception("Learning loop: could not read %s — skipping", rule_path)
            return None
        rule.setdefault("name", rule_name)
        # A hand-edited/corrupt learned file may carry `applies_to: null` (or a
        # non-mapping). setdefault won't replace an existing None, so coerce it
        # before the subscript assignments below — otherwise None["task_types"]
        # raises TypeError.
        if not isinstance(rule.get("applies_to"), dict):
            rule["applies_to"] = {"task_types": [], "tags": []}
        rule.setdefault("file_rules", [])
        rule.setdefault("skill_rules", [])

    # Broaden scope additively (union of task_types/tags across lessons).
    rule["applies_to"]["task_types"], t_changed = _merge_unique(
        rule["applies_to"].get("task_types", []), applies_to["task_types"]
    )
    rule["applies_to"]["tags"], g_changed = _merge_unique(
        rule["applies_to"].get("tags", []), applies_to["tags"]
    )
    rule["file_rules"], f_changed = _merge_unique(rule.get("file_rules", []), file_rules)
    rule["skill_rules"], s_changed = _merge_unique(rule.get("skill_rules", []), skill_rules)

    changed = created or t_changed or g_changed or f_changed or s_changed

    result = LearnedRule(
        rule_name=rule_name,
        applies_to=rule["applies_to"],
        file_rules=rule["file_rules"],
        skill_rules=rule["skill_rules"],
        created=created,
        deduped=not changed,
        source_task_run_id=task_run_id,
    )

    if not changed:
        logger.warning("Learning loop: lesson already captured in %s — deduped", rule_name)
        return result

    # 5) Validate then atomically swap into place.
    if not _validate_rule(rule):
        logger.warning("Learning loop: synthesised rule %s is invalid — skipping", rule_name)
        return None

    rules_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_yaml(rule_path, rule)

    # 6) Audit trail — trace every learned rule back to the failure.
    if conn is not None:
        try:
            from ai_dev_system.db.repos.events import EventRepo

            EventRepo(conn).insert(
                run_id,
                "RULE_LEARNED",
                actor,
                task_run_id=task_run_id,
                payload={
                    "rule_name": rule_name,
                    "task_type": task.get("task_type"),
                    "tags": task.get("tags") or [],
                    "lesson_source": source,
                    "source_error_type": error_type,
                    "created": created,
                },
            )
        except Exception:  # pragma: no cover - audit must never break learning
            logger.exception("Learning loop: failed to emit RULE_LEARNED event")

    logger.info(
        "Learning loop: %s rule %s from %s failure (task_run %s)",
        "created" if created else "appended to",
        rule_name,
        source,
        task_run_id,
    )
    return result
