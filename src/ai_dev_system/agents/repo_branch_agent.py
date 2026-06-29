"""Agent that runs claude -p with full tools on a git branch of the target repo.

Writes diff.txt, summary.txt, and claude_stderr.txt to output_path after execution.
The agent's output_path is the directory the engine will promote as EXECUTION_LOG.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ai_dev_system.agents.base import AgentResult
from ai_dev_system.llm_factory import ClaudeCodeLLMClient
from ai_dev_system.task_graph.facets import SPEC_FACET_KEYS

# Static claude CLI flags. --max-turns is appended at run() time so it can be
# tuned per-environment (see _max_turns).
_EXEC_FLAGS = [
    "--output-format", "json",
    "--permission-mode", "bypassPermissions",
]

# Default turn budget. A multi-file task (new module + tests + schema + commit)
# routinely needs well over 30 tool turns; 30 was the original value and caused
# attempts to hit error_max_turns before committing. Override with EXEC_MAX_TURNS.
_DEFAULT_MAX_TURNS = 100


def _max_turns() -> int:
    """Resolve the claude --max-turns budget from EXEC_MAX_TURNS (fallback 100)."""
    raw = os.environ.get("EXEC_MAX_TURNS")
    if not raw:
        return _DEFAULT_MAX_TURNS
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_MAX_TURNS
    return val if val > 0 else _DEFAULT_MAX_TURNS


def _parse_ndjson_event(line: str) -> Optional[str]:
    """Return a human-readable log message for a NDJSON event, or None to skip."""
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None

    event_type = obj.get("type", "")

    if event_type == "tool_use":
        name = obj.get("name", "?")
        inp = obj.get("input") or {}
        detail = _summarize_tool_input(name, inp)
        return f"[tool] {name}{detail}"

    if event_type == "result":
        subtype = obj.get("subtype", "")
        result_text = (obj.get("result") or "")[:100]
        cost = obj.get("total_cost_usd")
        cost_str = f" (${cost:.4f})" if cost else ""
        return f"[done] {subtype}: {result_text}{cost_str}"

    return None


def _summarize_tool_input(name: str, inp: dict) -> str:
    if name in ("Read", "Write", "Edit"):
        fp = inp.get("file_path", "")
        return f": {fp}" if fp else ""
    if name == "Bash":
        cmd = (inp.get("command") or "")[:80]
        return f": {cmd}" if cmd else ""
    if name in ("Glob", "Grep"):
        pat = inp.get("pattern", "")
        return f": {pat}" if pat else ""
    return ""


def _append_log(log_path: Path, msg: str) -> None:
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        f.flush()


def _format_lessons(file_rules) -> str:
    """Render learned lessons (file_rules) as a prompt block.

    Returns "" when there are no lessons, so callers can unconditionally append
    it. Lessons are corrective rules mined from earlier failed attempts — the
    agent MUST honour them (this is the seam that closes the learning loop).
    """
    rules = [str(r).strip() for r in (file_rules or []) if str(r).strip()]
    if not rules:
        return ""
    bullets = "\n".join(f"- {r}" for r in rules)
    return (
        "\n## LESSONS FROM PAST FAILURES (apply these)\n"
        "Corrective rules learned from earlier failed attempts on this work. "
        "Honour every one:\n"
        f"{bullets}\n"
    )


def _build_execution_prompt(context: dict, file_rules=()) -> str:
    facets = context.get("facets") or {}
    filled_lines: list[str] = []
    for key in SPEC_FACET_KEYS:
        f = facets.get(key) or {}
        if f.get("status") == "filled" and f.get("content", "").strip():
            filled_lines.append(f"### {key}\n{f['content']}")

    spec_section = "\n\n".join(filled_lines) if filled_lines else "(no spec facets filled)"

    base = (
        "You are implementing a coding task in THIS repository. "
        "Read existing code to understand patterns and conventions before writing anything. "
        "Tests already exist on this branch and are currently FAILING — implement the "
        "feature until they pass, then commit.\n\n"
        f"## Task\n"
        f"**Objective:** {context.get('objective', '')}\n"
        f"**Description:** {context.get('description', '')}\n"
        f"**Done when:** {context.get('done_definition', '')}\n\n"
        f"## Technical Specification\n{spec_section}\n\n"
        "## Rules\n"
        "- Follow existing code style and patterns in this repo\n"
        "- Tests already exist and are RED — make them pass; do NOT delete or weaken "
        "tests to make them pass\n"
        "- You MAY edit a test ONLY if it is genuinely wrong; if so, explain why in the "
        "commit message\n"
        "- Run the full test suite before committing\n"
        "- Commit with: `git add -A && git commit -m '<type>: <summary>'`\n"
        "- Do NOT push to remote\n"
    )
    return base + _format_lessons(file_rules)


def _git(args: list[str], cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def _extract_result_event(stdout: str) -> Optional[dict]:
    """Return the last NDJSON ``result`` event object, or None if absent."""
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("type") == "result":
            return obj
    return None


def _extract_summary(result_event: Optional[dict], returncode: int, stdout_len: int) -> str:
    """Human-readable summary from the result event.

    Falls back to the result subtype (e.g. ``error_max_turns``) when there is no
    result text, so a failed attempt's summary is diagnosable instead of a bare
    ``claude exit=1``.
    """
    if result_event is None:
        return f"claude exit={returncode}, stdout={stdout_len}B"
    result_text = result_event.get("result") or ""
    if result_text:
        return result_text[:500]
    subtype = result_event.get("subtype") or ""
    return f"claude ended without output: {subtype or f'exit={returncode}'}"


@dataclass
class _ClaudeRun:
    """Outcome of one `claude -p` invocation."""
    returncode: int
    stdout: str
    stderr: str
    result_event: Optional[dict]
    subtype: str
    timed_out: bool = False


def _step_model_effort(step: str) -> tuple[Optional[str], Optional[str]]:
    """Resolve (model, effort) for an agentic step via the shared profile table.

    Shared by the CLI-agentic agents (implement/fix → "executor",
    review/test-review → "judge"). Returns the configured alias + effort so the
    agent runs on the right tier instead of the CLI session default.
    """
    from ai_dev_system.llm_factory import resolve_step_model_effort
    return resolve_step_model_effort(step)


def _invoke_claude(
    claude: str,
    cwd: str,
    prompt: str,
    max_turns: int,
    timeout_s: float,
    live_log_path: Optional[Path] = None,
    model: Optional[str] = None,
    effort: Optional[str] = None,
) -> _ClaudeRun:
    """Run `claude -p` once, streaming NDJSON events to the live log. Shared by
    RepoBranchAgent (implement/fix) and ReviewAgent (review).

    `model`/`effort` (when set) pin the tier per step via `--model`/`--effort`;
    left unset, the call inherits the CLI session default (legacy behaviour)."""
    cmd = [claude, "-p", prompt, *_EXEC_FLAGS, "--max-turns", str(max_turns)]
    if model:
        cmd += ["--model", model]
    if effort:
        cmd += ["--effort", effort]
    proc = subprocess.Popen(
        cmd, cwd=cwd,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
    )

    all_stdout: list[str] = []
    stderr_lines: list[str] = []

    def _drain_stdout():
        for line in proc.stdout:
            all_stdout.append(line)
            if live_log_path:
                msg = _parse_ndjson_event(line.strip())
                if msg:
                    _append_log(live_log_path, msg)

    def _drain_stderr():
        for line in proc.stderr:
            stderr_lines.append(line)

    stdout_thread = threading.Thread(target=_drain_stdout, daemon=True)
    stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    timed_out = False
    try:
        proc.wait(timeout=int(timeout_s))
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
    finally:
        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)

    stdout = "".join(all_stdout)
    stderr = "".join(stderr_lines)
    ev = _extract_result_event(stdout)
    rc = proc.returncode if proc.returncode is not None else -1
    return _ClaudeRun(
        returncode=rc, stdout=stdout, stderr=stderr,
        result_event=ev, subtype=(ev or {}).get("subtype") or "", timed_out=timed_out,
    )


def _review_gate_enabled() -> bool:
    """Review gate is ON unless EXEC_REVIEW_GATE is explicitly falsy."""
    v = os.environ.get("EXEC_REVIEW_GATE")
    if v is None:
        return True
    return v.strip().lower() not in ("0", "false", "no", "off", "")


def _review_max_rounds() -> int:
    """Max auto-fix rounds after the first review (EXEC_REVIEW_MAX_ROUNDS, default 2)."""
    raw = os.environ.get("EXEC_REVIEW_MAX_ROUNDS")
    if not raw:
        return 2
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return 2
    return n if n >= 0 else 2


def _build_fix_prompt(objective: str, findings: list[dict], tests_passed: bool) -> str:
    """Prompt the implementer to fix review findings, then re-commit."""
    lines = []
    for f in findings:
        loc = f.get("file") or ""
        if f.get("line"):
            loc = f"{loc}:{f['line']}"
        sev = (f.get("severity") or "").upper()
        lines.append(f"- [{sev}] {loc} — {f.get('issue', '')}".strip())
    findings_block = "\n".join(lines) if lines else "(see test failures)"
    test_note = (
        "The test suite is currently FAILING — make it pass.\n"
        if not tests_passed else ""
    )
    return (
        "A code review of your previous commit on THIS branch found problems that "
        "must be fixed before the work can be accepted. Fix every issue below, then "
        "commit the fixes.\n\n"
        f"## Original objective\n{objective}\n\n"
        f"## Review findings to fix\n{findings_block}\n\n"
        f"## Rules\n{test_note}"
        "- Read the surrounding code before editing; keep existing style.\n"
        "- Re-run the tests and make sure they pass.\n"
        "- Commit with: `git add -A && git commit -m 'fix: address review findings'`\n"
        "- Do NOT push to remote.\n"
    )


class RepoBranchAgent:
    """Implements the Agent protocol. Runs claude -p with full tools on a git branch."""

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

        max_turns = _max_turns()
        model, effort = _step_model_effort("executor")
        if self.live_log_path:
            _append_log(self.live_log_path, f"Claude bắt đầu task {task_id}…")

        run1 = _invoke_claude(
            claude, self.repo_path, _build_execution_prompt(context, file_rules),
            max_turns, timeout_s, self.live_log_path, model=model, effort=effort,
        )

        if run1.timed_out:
            self._write_outputs(output_path, run1, review=None)
            return AgentResult(
                output_path=output_path, error=f"claude timed out after {timeout_s}s",
            )

        if run1.returncode != 0:
            self._write_outputs(output_path, run1, review=None)
            if run1.subtype == "error_max_turns":
                error = (
                    f"claude reached the {max_turns}-turn limit without finishing "
                    f"the task (no commit produced). Raise EXEC_MAX_TURNS or split "
                    f"the task into smaller pieces."
                )
            else:
                error = f"claude CLI exited {run1.returncode}. stderr: {run1.stderr[:300]}"
            return AgentResult(output_path=output_path, error=error)

        # Review gate: run tests + review the diff, auto-fix what's broken before
        # reporting done. Implementation already exists, so a flagged review does
        # NOT fail the task — it's surfaced to the human at the webui Accept gate.
        review = None
        if _review_gate_enabled():
            review = self._review_and_repair(claude, context, timeout_s)

        self._write_outputs(output_path, run1, review)
        return AgentResult(output_path=output_path)

    # ── review gate ────────────────────────────────────────────────────────────

    def _review_and_repair(self, claude: str, context: dict, timeout_s: float) -> dict:
        """Loop: review the committed diff → if blocking, fix → re-review (≤ N rounds).

        Returns a report dict written to review.json. Never raises — a reviewer
        failure degrades to an inconclusive (non-blocking) verdict.
        """
        from ai_dev_system.agents.review_agent import ReviewAgent
        from ai_dev_system.agents.test_author_agent import build_test_source

        max_rounds = _review_max_rounds()
        model, effort = _step_model_effort("executor")  # fixes are implementation work
        reviewer = ReviewAgent(self.repo_path, self.base_branch, live_log_path=self.live_log_path)
        objective = str(context.get("objective", ""))
        test_spec = build_test_source(context) if context.get("tdd_tests_authored") else ""
        verdict = None
        rounds_fixed = 0

        for attempt in range(max_rounds + 1):
            verdict = reviewer.review(objective=objective, test_spec=test_spec, timeout_s=timeout_s)
            if self.live_log_path:
                _append_log(
                    self.live_log_path,
                    f"[review] verdict={verdict.verdict} tests_ran={verdict.tests_ran} "
                    f"tests_passed={verdict.tests_passed} findings={len(verdict.findings)}",
                )
            if not verdict.is_blocking():
                break
            if attempt >= max_rounds:
                break  # exhausted repair budget
            fix_run = _invoke_claude(
                claude, self.repo_path,
                _build_fix_prompt(objective, verdict.findings, verdict.tests_passed),
                _max_turns(), timeout_s, self.live_log_path, model=model, effort=effort,
            )
            rounds_fixed += 1
            if fix_run.timed_out or fix_run.returncode != 0:
                break  # fix run itself failed — stop, leave flagged

        clean = verdict is not None and not verdict.is_blocking()
        return {
            "review_status": "clean" if clean else "flagged",
            "verdict": verdict.verdict if verdict else "inconclusive",
            "tests_ran": verdict.tests_ran if verdict else False,
            "tests_passed": verdict.tests_passed if verdict else False,
            "findings": verdict.findings if verdict else [],
            "rounds_fixed": rounds_fixed,
        }

    def _write_outputs(self, output_path: str, claude_run: _ClaudeRun, review: Optional[dict]) -> None:
        """Write diff.txt / summary.txt / claude_stderr.txt (+ review.json) reflecting
        the FINAL branch state."""
        diff_text = _git(["diff", f"{self.base_branch}..HEAD"], self.repo_path).stdout or "(no diff)"
        summary = _extract_summary(claude_run.result_event, claude_run.returncode, len(claude_run.stdout))
        Path(output_path, "diff.txt").write_text(diff_text, encoding="utf-8")
        Path(output_path, "summary.txt").write_text(summary, encoding="utf-8")
        Path(output_path, "claude_stderr.txt").write_text(claude_run.stderr, encoding="utf-8")
        if review is not None:
            Path(output_path, "review.json").write_text(
                json.dumps(review, indent=2, ensure_ascii=False), encoding="utf-8"
            )
