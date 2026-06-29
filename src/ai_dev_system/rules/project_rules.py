# src/ai_dev_system/rules/project_rules.py
"""Project-tier learned rules: lessons stored INSIDE the target repo.

The failure-learning loop is two-tier, separated by location:

* GLOBAL  — rules shipped with the tool (``rules/definitions/``), matched by the
  worker and handed to the agent as ``file_rules``.
* PROJECT — lessons learned from THIS repo's own runs, committed in the target
  repo at ``<repo>/.ai-dev/rules/``, loaded here.

Both tiers share the identical YAML shape and are matched the same way
(``task_type``/``tags``) via ``RuleRegistry`` — the only difference is location.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from ai_dev_system.rules.registry import RuleRegistry

logger = logging.getLogger(__name__)

# <repo>/.ai-dev/rules
_PROJECT_RULES_SUBDIR = (".ai-dev", "rules")


def project_rules_dir(repo_path: str | Path) -> Path:
    """Return ``<repo>/.ai-dev/rules`` for a target repo (not created here)."""
    return Path(repo_path, *_PROJECT_RULES_SUBDIR)


def _enabled() -> bool:
    """Project tier is ON by default; ``AI_DEV_PROJECT_RULES`` in
    {0,false,off,no} (case-insensitive) disables it."""
    raw = os.environ.get("AI_DEV_PROJECT_RULES")
    if raw is None:
        return True
    return raw.strip().lower() not in ("0", "false", "off", "no")


def load_project_file_rules(repo_path: str | Path, context: dict) -> list[str]:
    """Match the target repo's project-tier rules against this task's context.

    Returns the ``file_rules`` whose ``applies_to`` matches the task's
    ``type``/``tags``, or ``[]`` when the tier is disabled, the repo has no
    ``.ai-dev/rules`` dir, or nothing matches. Never raises — a broken project
    rule file must never fail the task.
    """
    if not _enabled() or not repo_path:
        return []
    rules_dir = project_rules_dir(repo_path)
    if not rules_dir.is_dir():
        return []
    try:
        registry = RuleRegistry(rules_dir=rules_dir)
        match_task = {
            "task_type": (context.get("type") or "").strip(),
            "tags": list(context.get("tags") or []),
        }
        return registry.match_rules(match_task).file_rules
    except Exception:  # noqa: BLE001 - project rules must never break execution
        logger.exception("Failed to load project rules from %s", rules_dir)
        return []
