# src/ai_dev_system/agents/test_author_agent.py
"""Agent for the test-authoring phase of the TDD-first split.

Runs `claude -p` to write FAILING tests from the acceptance source (test_cases
facet / acceptance criteria) — no implementation — then runs an independent
TestReviewAgent gate (red check + tests↔AC) and repairs ≤ N rounds before the
implementation phase begins.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from ai_dev_system.agents.base import AgentResult
from ai_dev_system.llm_factory import ClaudeCodeLLMClient
from ai_dev_system.agents.repo_branch_agent import (
    _invoke_claude, _append_log, _max_turns, _git, _extract_summary,
)

# Facets that describe WHAT to test (the acceptance source for this task).
_TEST_SOURCE_FACETS = ("test_cases", "input", "response", "error_cases", "validation_rules")


def _test_review_max_rounds() -> int:
    raw = os.environ.get("EXEC_TEST_REVIEW_MAX_ROUNDS")
    if not raw:
        return 2
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return 2
    return n if n >= 0 else 2


def build_test_source(context: dict) -> str:
    """Assemble the acceptance source the tests must encode, from filled facets
    (+ acceptance_criteria if present). Never returns an empty string."""
    facets = context.get("facets") or {}
    blocks: list[str] = []
    ac = (context.get("acceptance_criteria") or "").strip()
    if ac:
        blocks.append(f"### acceptance_criteria\n{ac}")
    for key in _TEST_SOURCE_FACETS:
        f = facets.get(key) or {}
        if f.get("status") == "filled" and f.get("content", "").strip():
            blocks.append(f"### {key}\n{f['content']}")
    if not blocks:
        return ("(no explicit test spec — derive observable behaviours from the "
                "objective and done-definition below)")
    return "\n\n".join(blocks)


def _build_test_prompt(context: dict) -> str:
    return (
        "You are writing TESTS for a coding task in THIS repository, BEFORE any "
        "implementation exists (test-driven development). Read existing test files "
        "to match the project's test framework and conventions first.\n\n"
        "## Task\n"
        f"**Objective:** {context.get('objective', '')}\n"
        f"**Description:** {context.get('description', '')}\n"
        f"**Done when:** {context.get('done_definition', '')}\n\n"
        "## Acceptance source — your tests MUST encode these\n"
        f"{build_test_source(context)}\n\n"
        "## Rules\n"
        "- Write ONLY tests. Do NOT implement the feature.\n"
        "- Each acceptance item must have a test asserting the OBSERVABLE behaviour "
        "(not implementation detail).\n"
        "- Run the tests. They MUST FAIL (RED) because the implementation is absent. "
        "Confirm they fail for the right reason (assertion / missing symbol), not a "
        "syntax error.\n"
        "- Commit with: `git add -A && git commit -m 'test: <summary>'`\n"
        "- Do NOT push to remote.\n"
    )


def _build_test_fix_prompt(objective: str, findings: list[dict], tests_red: bool) -> str:
    lines = []
    for f in findings:
        loc = f.get("file") or ""
        if f.get("line"):
            loc = f"{loc}:{f['line']}"
        sev = (f.get("severity") or "").upper()
        lines.append(f"- [{sev}] {loc} — {f.get('issue', '')}".strip())
    findings_block = "\n".join(lines) if lines else "(strengthen coverage of the acceptance source)"
    red_note = (
        "Some tests PASS without any implementation — they are tautological. Rewrite "
        "them to assert real behaviour so they FAIL until the feature is built.\n"
        if not tests_red else ""
    )
    return (
        "A review of the tests you just committed found problems. Fix every issue "
        "below, then commit the fixes. Still write TESTS ONLY — no implementation.\n\n"
        f"## Original objective\n{objective}\n\n"
        f"## Findings to fix\n{findings_block}\n\n"
        f"## Rules\n{red_note}"
        "- The tests must still be RED (failing) after your fixes.\n"
        "- Commit with: `git add -A && git commit -m 'test: address review findings'`\n"
        "- Do NOT push to remote.\n"
    )


class TestAuthorAgent:
    """Implements the Agent protocol. Writes failing tests, then review-repairs them."""
    __test__ = False

    def __init__(
        self,
        repo_path: str,
        branch_name: str,
        base_branch: str,
        live_log_path: Optional[Path] = None,
    ) -> None:
        self.repo_path = repo_path
        self.branch_name = branch_name
        self.base_branch = base_branch
        self.live_log_path = live_log_path

    def run(
        self,
        task_id: str,
        output_path: str,
        promoted_outputs=(),
        context: Optional[dict] = None,
        timeout_s: float = 1800.0,
        file_rules: list = (),
    ) -> AgentResult:
        Path(output_path).mkdir(parents=True, exist_ok=True)
        context = context or {}

        try:
            claude = ClaudeCodeLLMClient._resolve_claude_cmd()
        except Exception as exc:
            return AgentResult(output_path=output_path, error=f"claude CLI not found: {exc}")

        if self.live_log_path:
            _append_log(self.live_log_path, f"Test author bắt đầu task {task_id}…")

        run1 = _invoke_claude(
            claude, self.repo_path, _build_test_prompt(context),
            _max_turns(), timeout_s, self.live_log_path,
        )
        if run1.timed_out:
            self._write_outputs(output_path, run1, review=None)
            return AgentResult(output_path=output_path, error=f"claude timed out after {timeout_s}s")
        if run1.returncode != 0:
            self._write_outputs(output_path, run1, review=None)
            if run1.subtype == "error_max_turns":
                err = (f"test author reached the turn limit without finishing "
                       f"(no commit produced). Raise EXEC_MAX_TURNS or split the task.")
            else:
                err = f"claude CLI exited {run1.returncode}. stderr: {run1.stderr[:300]}"
            return AgentResult(output_path=output_path, error=err)

        review = self._review_and_repair(claude, context, timeout_s)
        self._write_outputs(output_path, run1, review)

        # A still-blocking test review (after the repair budget) FAILS the task so
        # the implementation phase never builds on untrustworthy tests.
        if review and review.get("review_status") == "flagged":
            return AgentResult(
                output_path=output_path,
                error=f"test review still flagged after repair: verdict={review.get('verdict')}",
            )
        return AgentResult(output_path=output_path)

    def _review_and_repair(self, claude: str, context: dict, timeout_s: float) -> dict:
        from ai_dev_system.agents.test_review_agent import TestReviewAgent

        max_rounds = _test_review_max_rounds()
        reviewer = TestReviewAgent(self.repo_path, self.base_branch, live_log_path=self.live_log_path)
        objective = str(context.get("objective", ""))
        test_spec = build_test_source(context)
        verdict = None
        rounds_fixed = 0

        for attempt in range(max_rounds + 1):
            verdict = reviewer.review(test_spec=test_spec, objective=objective, timeout_s=timeout_s)
            if self.live_log_path:
                _append_log(
                    self.live_log_path,
                    f"[test-review] verdict={verdict.verdict} tests_red={verdict.tests_red} "
                    f"findings={len(verdict.findings)}",
                )
            if not verdict.is_blocking():
                break
            if attempt >= max_rounds:
                break
            fix_run = _invoke_claude(
                claude, self.repo_path,
                _build_test_fix_prompt(objective, verdict.findings, verdict.tests_red),
                _max_turns(), timeout_s, self.live_log_path,
            )
            rounds_fixed += 1
            if fix_run.timed_out or fix_run.returncode != 0:
                break

        clean = verdict is not None and not verdict.is_blocking()
        return {
            "review_status": "clean" if clean else "flagged",
            "verdict": verdict.verdict if verdict else "inconclusive",
            "tests_red": verdict.tests_red if verdict else False,
            "findings": verdict.findings if verdict else [],
            "rounds_fixed": rounds_fixed,
        }

    def _write_outputs(self, output_path: str, claude_run, review: Optional[dict]) -> None:
        diff_text = _git(["diff", f"{self.base_branch}..HEAD"], self.repo_path).stdout or "(no diff)"
        summary = _extract_summary(claude_run.result_event, claude_run.returncode, len(claude_run.stdout))
        Path(output_path, "diff.txt").write_text(diff_text, encoding="utf-8")
        Path(output_path, "summary.txt").write_text(summary, encoding="utf-8")
        Path(output_path, "claude_stderr.txt").write_text(claude_run.stderr, encoding="utf-8")
        if review is not None:
            Path(output_path, "test_review.json").write_text(
                json.dumps(review, indent=2, ensure_ascii=False), encoding="utf-8"
            )
