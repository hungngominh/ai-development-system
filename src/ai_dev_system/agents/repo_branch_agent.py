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


def _build_execution_prompt(context: dict) -> str:
    facets = context.get("facets") or {}
    filled_lines: list[str] = []
    for key in SPEC_FACET_KEYS:
        f = facets.get(key) or {}
        if f.get("status") == "filled" and f.get("content", "").strip():
            filled_lines.append(f"### {key}\n{f['content']}")

    spec_section = "\n\n".join(filled_lines) if filled_lines else "(no spec facets filled)"

    return (
        "You are implementing a coding task in THIS repository. "
        "Read existing code to understand patterns and conventions before writing anything. "
        "Implement the task completely, write tests, and commit your changes with a "
        "meaningful commit message.\n\n"
        f"## Task\n"
        f"**Objective:** {context.get('objective', '')}\n"
        f"**Description:** {context.get('description', '')}\n"
        f"**Done when:** {context.get('done_definition', '')}\n\n"
        f"## Technical Specification\n{spec_section}\n\n"
        "## Rules\n"
        "- Follow existing code style and patterns in this repo\n"
        "- Write or update tests for every behaviour you add or change\n"
        "- Run existing tests before committing — fix failures if they relate to your change\n"
        "- Commit with: `git add -A && git commit -m '<type>: <summary>'`\n"
        "- Do NOT push to remote\n"
    )


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

        prompt = _build_execution_prompt(context)
        max_turns = _max_turns()
        cmd = [claude, "-p", prompt, *_EXEC_FLAGS, "--max-turns", str(max_turns)]

        if self.live_log_path:
            _append_log(self.live_log_path, f"Claude bắt đầu task {task_id}…")

        proc = subprocess.Popen(
            cmd, cwd=self.repo_path,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
        )

        all_stdout: list[str] = []
        stderr_lines: list[str] = []

        def _drain_stdout():
            for line in proc.stdout:
                all_stdout.append(line)
                if self.live_log_path:
                    msg = _parse_ndjson_event(line.strip())
                    if msg:
                        _append_log(self.live_log_path, msg)

        def _drain_stderr():
            for line in proc.stderr:
                stderr_lines.append(line)

        stdout_thread = threading.Thread(target=_drain_stdout, daemon=True)
        stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
        stdout_thread.start()
        stderr_thread.start()

        try:
            proc.wait(timeout=int(timeout_s))
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=5)   # bounded wait after kill
            except subprocess.TimeoutExpired:
                pass
            stdout_thread.join(timeout=5)
            stderr_thread.join(timeout=5)
            return AgentResult(
                output_path=output_path,
                error=f"claude timed out after {timeout_s}s",
            )
        finally:
            stdout_thread.join(timeout=5)
            stderr_thread.join(timeout=5)

        full_stdout = "".join(all_stdout)
        full_stderr = "".join(stderr_lines)

        diff_proc = _git(["diff", f"{self.base_branch}..HEAD"], self.repo_path)
        diff_text = diff_proc.stdout or "(no diff)"

        result_event = _extract_result_event(full_stdout)
        subtype = (result_event or {}).get("subtype") or ""
        summary = _extract_summary(result_event, proc.returncode, len(full_stdout))

        Path(output_path, "diff.txt").write_text(diff_text, encoding="utf-8")
        Path(output_path, "summary.txt").write_text(summary, encoding="utf-8")
        Path(output_path, "claude_stderr.txt").write_text(full_stderr, encoding="utf-8")

        if proc.returncode != 0:
            if subtype == "error_max_turns":
                error = (
                    f"claude reached the {max_turns}-turn limit without finishing "
                    f"the task (no commit produced). Raise EXEC_MAX_TURNS or split "
                    f"the task into smaller pieces."
                )
            else:
                error = f"claude CLI exited {proc.returncode}. stderr: {full_stderr[:300]}"
            return AgentResult(output_path=output_path, error=error)

        return AgentResult(output_path=output_path)
