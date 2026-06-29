"""Reviewer agent for the executor review gate.

An independent `claude -p` pass (separate context from the implementer) that runs
the repo's test suite and reviews the committed diff for correctness + integration
bugs, then returns a structured verdict. Used by RepoBranchAgent's review-repair
loop to decide whether the work is clean before it is reported done.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ai_dev_system.llm_factory import ClaudeCodeLLMClient
# Reuse the shared claude-CLI plumbing + log helpers from the implementer agent.
from ai_dev_system.agents.repo_branch_agent import _invoke_claude, _append_log

_DEFAULT_REVIEW_MAX_TURNS = 40


def _review_max_turns() -> int:
    """Turn budget for the reviewer (REVIEW_MAX_TURNS, default 40)."""
    raw = os.environ.get("REVIEW_MAX_TURNS")
    if not raw:
        return _DEFAULT_REVIEW_MAX_TURNS
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_REVIEW_MAX_TURNS
    return n if n > 0 else _DEFAULT_REVIEW_MAX_TURNS


@dataclass
class ReviewVerdict:
    """Structured result of one review pass."""
    verdict: str = "inconclusive"          # "pass" | "fail" | "inconclusive"
    tests_ran: bool = False
    tests_passed: bool = False
    findings: list[dict] = field(default_factory=list)  # [{severity,file,line,issue}]
    raw: str = ""

    _BLOCKING_SEVERITIES = ("high", "critical", "blocker")

    def is_blocking(self) -> bool:
        """Should the implementer be asked to fix and re-review?

        An inconclusive verdict (e.g. the reviewer crashed or returned malformed
        JSON) is NOT blocking — we never wedge a task on review plumbing. A task
        blocks when the reviewer said 'fail', OR tests ran and failed, OR there is
        a high-severity finding.
        """
        if self.verdict == "inconclusive":
            return False
        if self.tests_ran and not self.tests_passed:
            return True
        if any((f.get("severity") or "").lower() in self._BLOCKING_SEVERITIES
               for f in self.findings):
            return True
        return self.verdict == "fail"


def _build_review_prompt(base_branch: str, objective: str, test_spec: str = "") -> str:
    weakening_block = ""
    if test_spec:
        weakening_block = (
            "\n## Test integrity (tests were authored BEFORE the implementation)\n"
            "The tests on this branch encode this acceptance source:\n"
            f"{test_spec}\n"
            f"Inspect whether the implementer changed any test: `git log {base_branch}..HEAD` "
            f"and `git diff {base_branch}..HEAD -- '*test*'`. Any test that was deleted, "
            "skipped, or weakened so it no longer enforces the acceptance source above is a "
            "HIGH-severity finding.\n"
        )
    return (
        "You are an independent REVIEWER of a code change just committed to THIS "
        "git branch. Do NOT make changes — only review.\n\n"
        f"## What the change was supposed to do\n{objective or '(not provided)'}\n\n"
        "## Steps\n"
        "1. Find and run this repo's test suite (auto-detect: pytest, npm test, "
        "go test, cargo test, etc.). Note whether tests ran and whether they passed.\n"
        f"2. Review the diff: `git diff {base_branch}..HEAD`. Look hardest for "
        "INTEGRATION bugs, not just style: is new code actually reached from a "
        "non-test path (grep for callers)? Do referenced functions, fields, DB "
        "columns, and config keys actually exist where the code runs? Does it match "
        "the real runtime data shapes, not just what the tests fabricate? Also check "
        "correctness: wrong/inverted conditions, missing error handling, off-by-one.\n"
        "3. Only report findings you have VERIFIED are real (prefer fewer, certain "
        "findings over noise). Severity is one of: low, medium, high, critical.\n"
        f"{weakening_block}"
        "\n## Output\n"
        "Your FINAL message must be ONLY a single JSON object, no prose, no code "
        "fences, exactly this shape:\n"
        '{"verdict": "pass" | "fail", "tests_ran": true|false, '
        '"tests_passed": true|false, '
        '"findings": [{"severity": "...", "file": "...", "line": 0, "issue": "..."}]}\n'
        "Use verdict \"pass\" only when tests pass (or there are genuinely no tests) "
        "AND there are no high/critical findings."
    )


def _parse_verdict(raw_text: str) -> ReviewVerdict:
    """Parse the reviewer's final JSON message into a ReviewVerdict.

    Tolerates surrounding prose / code fences by extracting the outermost {...}.
    Returns an inconclusive verdict if nothing parses.
    """
    text = (raw_text or "").strip()
    if not text:
        return ReviewVerdict(raw=raw_text or "")
    candidate = text
    if "{" in text and "}" in text:
        candidate = text[text.index("{"): text.rindex("}") + 1]
    try:
        obj = json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return ReviewVerdict(raw=raw_text or "")
    if not isinstance(obj, dict):
        return ReviewVerdict(raw=raw_text or "")
    verdict = str(obj.get("verdict") or "inconclusive").lower()
    if verdict not in ("pass", "fail"):
        verdict = "inconclusive"
    findings = obj.get("findings")
    findings = [f for f in findings if isinstance(f, dict)] if isinstance(findings, list) else []
    return ReviewVerdict(
        verdict=verdict,
        tests_ran=bool(obj.get("tests_ran")),
        tests_passed=bool(obj.get("tests_passed")),
        findings=findings,
        raw=raw_text or "",
    )


class ReviewAgent:
    """Runs an independent `claude -p` review pass over a branch."""

    def __init__(self, repo_path: str, base_branch: str, live_log_path: Optional[Path] = None) -> None:
        self.repo_path = repo_path
        self.base_branch = base_branch
        self.live_log_path = live_log_path

    def review(self, objective: str = "", test_spec: str = "", timeout_s: float = 1800.0) -> ReviewVerdict:
        """Run the reviewer; never raises — failures degrade to inconclusive."""
        try:
            claude = ClaudeCodeLLMClient._resolve_claude_cmd()
        except Exception:
            return ReviewVerdict()  # inconclusive — no reviewer available

        if self.live_log_path:
            _append_log(self.live_log_path, "Reviewer bắt đầu kiểm tra (test + diff)…")

        from ai_dev_system.llm_factory import resolve_step_model_effort
        model, effort = resolve_step_model_effort("judge")
        run = _invoke_claude(
            claude, self.repo_path, _build_review_prompt(self.base_branch, objective, test_spec),
            _review_max_turns(), timeout_s, self.live_log_path, model=model, effort=effort,
        )
        if run.timed_out or run.returncode != 0:
            return ReviewVerdict(raw=(run.result_event or {}).get("result") or "")
        result_text = (run.result_event or {}).get("result") or ""
        return _parse_verdict(result_text)
