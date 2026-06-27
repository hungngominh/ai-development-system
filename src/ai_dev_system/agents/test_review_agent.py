"""Reviewer for the test-authoring phase.

An independent `claude -p` pass that runs the repo test suite, confirms the
newly-authored tests are RED (failing because implementation is absent), and
compares them against the acceptance source (test_cases facet / AC). Returns a
structured verdict with TEST-PHASE blocking semantics: red is the expected,
clean state; green-at-this-stage or missing/weak coverage is blocking.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ai_dev_system.llm_factory import ClaudeCodeLLMClient
from ai_dev_system.agents.repo_branch_agent import _invoke_claude, _append_log

_DEFAULT_TEST_REVIEW_MAX_TURNS = 40


def _test_review_max_turns() -> int:
    raw = os.environ.get("TEST_REVIEW_MAX_TURNS")
    if not raw:
        return _DEFAULT_TEST_REVIEW_MAX_TURNS
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_TEST_REVIEW_MAX_TURNS
    return n if n > 0 else _DEFAULT_TEST_REVIEW_MAX_TURNS


@dataclass
class TestReviewVerdict:
    """Result of one test-phase review pass."""
    verdict: str = "inconclusive"        # "pass" | "fail" | "inconclusive"
    tests_red: bool = False              # do the NEW tests currently fail (expected)?
    findings: list[dict] = field(default_factory=list)  # [{severity,file,line,issue}]
    raw: str = ""

    _BLOCKING_SEVERITIES = ("high", "critical", "blocker")

    def is_blocking(self) -> bool:
        """Test-phase semantics — red is GOOD.

        Inconclusive (reviewer crashed / malformed JSON) never blocks: we do not
        wedge a task on review plumbing. Otherwise block when the tests are NOT
        red (tautological / already-passing => wrong), OR there is a high-severity
        finding (missing AC coverage, weak test), OR the reviewer said 'fail'.
        """
        if self.verdict == "inconclusive":
            return False
        if not self.tests_red:
            return True
        if any((f.get("severity") or "").lower() in self._BLOCKING_SEVERITIES
               for f in self.findings):
            return True
        return self.verdict == "fail"


def _build_test_review_prompt(base_branch: str, objective: str, test_spec: str) -> str:
    return (
        "You are an independent REVIEWER of TESTS just committed to THIS git branch "
        "BEFORE any implementation exists. Do NOT write code — only review.\n\n"
        f"## What the change is supposed to do\n{objective or '(not provided)'}\n\n"
        "## Acceptance source the tests MUST encode\n"
        f"{test_spec or '(none provided)'}\n\n"
        "## Steps\n"
        f"1. Inspect the new tests: `git diff {base_branch}..HEAD`.\n"
        "2. Run the repo test suite (auto-detect pytest / npm test / go test / etc.). "
        "The new tests MUST currently FAIL (RED) — implementation does not exist yet. "
        "If they PASS, they are almost certainly tautological or assert nothing real.\n"
        "3. For EACH acceptance item above, check there is a test that genuinely "
        "exercises the observable behaviour (not implementation detail, not a "
        "trivially-true assertion). Missing coverage or a weak/tautological test is "
        "a HIGH-severity finding.\n\n"
        "## Output\n"
        "Your FINAL message must be ONLY a single JSON object, no prose, no fences:\n"
        '{"verdict": "pass"|"fail", "tests_red": true|false, '
        '"findings": [{"severity": "low|medium|high|critical", "file": "...", '
        '"line": 0, "issue": "..."}]}\n'
        'Use "pass" only when the new tests are RED AND every acceptance item has a '
        "genuine test."
    )


def _parse_test_verdict(raw_text: str) -> TestReviewVerdict:
    text = (raw_text or "").strip()
    if not text:
        return TestReviewVerdict(raw=raw_text or "")
    candidate = text
    if "{" in text and "}" in text:
        candidate = text[text.index("{"): text.rindex("}") + 1]
    try:
        obj = json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return TestReviewVerdict(raw=raw_text or "")
    if not isinstance(obj, dict):
        return TestReviewVerdict(raw=raw_text or "")
    verdict = str(obj.get("verdict") or "inconclusive").lower()
    if verdict not in ("pass", "fail"):
        verdict = "inconclusive"
    findings = obj.get("findings")
    findings = [f for f in findings if isinstance(f, dict)] if isinstance(findings, list) else []
    return TestReviewVerdict(
        verdict=verdict,
        tests_red=bool(obj.get("tests_red")),
        findings=findings,
        raw=raw_text or "",
    )


class TestReviewAgent:
    """Runs an independent `claude -p` review of the test-authoring phase."""

    def __init__(self, repo_path: str, base_branch: str, live_log_path: Optional[Path] = None) -> None:
        self.repo_path = repo_path
        self.base_branch = base_branch
        self.live_log_path = live_log_path

    def review(self, test_spec: str, objective: str = "", timeout_s: float = 1800.0) -> TestReviewVerdict:
        """Run the reviewer; never raises — failures degrade to inconclusive."""
        try:
            claude = ClaudeCodeLLMClient._resolve_claude_cmd()
        except Exception:
            return TestReviewVerdict()  # inconclusive — no reviewer available
        if self.live_log_path:
            _append_log(self.live_log_path, "Test reviewer bắt đầu (red check + tests↔AC)…")
        run = _invoke_claude(
            claude, self.repo_path,
            _build_test_review_prompt(self.base_branch, objective, test_spec),
            _test_review_max_turns(), timeout_s, self.live_log_path,
        )
        if run.timed_out or run.returncode != 0:
            return TestReviewVerdict(raw=(run.result_event or {}).get("result") or "")
        return _parse_test_verdict((run.result_event or {}).get("result") or "")
