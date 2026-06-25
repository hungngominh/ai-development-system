"""Agent that runs claude -p with full tools on a git branch of the target repo.

Writes diff.txt, summary.txt, and claude_stderr.txt to output_path after execution.
The agent's output_path is the directory the engine will promote as EXECUTION_LOG.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Optional

from ai_dev_system.agents.base import AgentResult
from ai_dev_system.llm_factory import ClaudeCodeLLMClient
from ai_dev_system.task_graph.facets import SPEC_FACET_KEYS

# Full-tool flags for execution — NOT read-only (agent needs Edit/Write/Bash).
_EXEC_FLAGS = [
    "--output-format", "json",
    "--permission-mode", "bypassPermissions",
    "--max-turns", "30",
]


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


def _extract_summary(stdout: str, returncode: int) -> str:
    """Pull the result text from NDJSON output."""
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict) and obj.get("type") == "result":
                result_text = obj.get("result") or ""
                return result_text[:500] if result_text else f"claude exit={returncode}"
        except json.JSONDecodeError:
            continue
    return f"claude exit={returncode}, stdout={len(stdout)}B"


class RepoBranchAgent:
    """Implements the Agent protocol. Runs claude -p with full tools on a git branch."""

    def __init__(self, repo_path: str, branch_name: str, base_branch: str) -> None:
        self.repo_path = repo_path
        self.branch_name = branch_name
        self.base_branch = base_branch

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
        cmd = [claude, "-p", prompt, *_EXEC_FLAGS]

        proc = subprocess.run(
            cmd, cwd=self.repo_path,
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=int(timeout_s),
        )

        # Capture git diff regardless of claude exit code
        diff_proc = _git(["diff", f"{self.base_branch}..HEAD"], self.repo_path)
        diff_text = diff_proc.stdout or "(no diff)"

        summary = _extract_summary(proc.stdout or "", proc.returncode)

        Path(output_path, "diff.txt").write_text(diff_text, encoding="utf-8")
        Path(output_path, "summary.txt").write_text(summary, encoding="utf-8")
        Path(output_path, "claude_stderr.txt").write_text(proc.stderr or "", encoding="utf-8")

        if proc.returncode != 0:
            return AgentResult(
                output_path=output_path,
                error=f"claude CLI exited {proc.returncode}. stderr: {(proc.stderr or '')[:300]}",
            )

        return AgentResult(output_path=output_path)
