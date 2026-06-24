"""Agentic, repo-grounded facet generation (Level B).

Runs the `claude` CLI in read-only, non-interactive mode with the target repo as
cwd, letting it Read/Grep/Glob the actual code to ground each facet. Never raises:
any failure yields all-`needs_human`. Tests inject `run` (a subprocess.run-like
callable); the real `claude` CLI is never invoked under test.
"""
from __future__ import annotations

import json
import os
import subprocess

from ai_dev_system.task_graph.facets import (
    FACET_KEYS,
    FACET_DEFINITIONS,
    _all_needs_human,
    _coerce_facet,
)
from ai_dev_system.llm_factory import ClaudeCodeLLMClient

# Read-only, non-interactive flags (verified against Claude Code CLI).
_READONLY_FLAGS = [
    "--output-format", "json",
    "--permission-mode", "bypassPermissions",
    "--disallowedTools", "Edit", "Write", "Bash", "PowerShell", "WebFetch", "WebSearch",
    "--max-turns", "15",
]


def _build_prompt(task: dict) -> str:
    facet_lines = "\n".join(f"- {k}: {FACET_DEFINITIONS[k]}" for k in FACET_KEYS)
    return (
        "You are detailing ONE implementation task against THIS repository. Use "
        "Read/Grep/Glob to inspect the actual code relevant to the task (data "
        "models, schema/migrations, the modules this task touches). For each of the "
        "8 engineering facets below, write a concrete, code-grounded detail and cite "
        "the file path(s) you used. Mark a facet \"na\" (with a reason) when "
        "irrelevant, or \"needs_human\" when you find NO evidence in the code — do "
        "NOT invent. Ignore .env, secrets, node_modules, and build output.\n\n"
        f"TASK:\n- objective: {task.get('objective', '')}\n"
        f"- description: {task.get('description', '')}\n\n"
        "Return ONLY a JSON object keyed by the 8 facet names; each value is "
        '{"status": "filled"|"na"|"needs_human", "content": "...", "reason": "..."}.\n'
        "Facets:\n" + facet_lines
    )


def _build_command(claude: str, prompt: str, *, model: str | None = None) -> list[str]:
    cmd = [claude, "-p", prompt, *_READONLY_FLAGS]
    if model:
        cmd += ["--model", model]
    return cmd


def _extract_text(stdout: str) -> str:
    """Pull the assistant text out of the --output-format json wrapper, defensively."""
    wrapper = json.loads(stdout)  # may raise → caller catches
    if isinstance(wrapper, dict):
        result = wrapper.get("result")
        if isinstance(result, str):
            return result
        messages = wrapper.get("messages")
        if isinstance(messages, list):
            parts = []
            for m in messages:
                c = m.get("content") if isinstance(m, dict) else None
                if isinstance(c, str):
                    parts.append(c)
            if parts:
                return "\n".join(parts)
    # Unknown shape — fall back to the raw stdout (maybe it was already the JSON).
    return stdout


def generate_task_facets_agentic(
    task: dict,
    repo_path: str,
    *,
    model: str | None = None,
    timeout: int = 300,
    run=subprocess.run,
) -> dict[str, dict]:
    """8 facets grounded in the repo at `repo_path`, via read-only `claude -p`.
    Never raises — any failure returns all-`needs_human`."""
    if not repo_path or not os.path.isdir(repo_path):
        return _all_needs_human()
    try:
        claude = ClaudeCodeLLMClient._resolve_claude_cmd()
        cmd = _build_command(claude, _build_prompt(task), model=model)
        proc = run(
            cmd, cwd=repo_path, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        if proc.returncode != 0:
            return _all_needs_human()
        text = ClaudeCodeLLMClient._strip_outer_code_fence(_extract_text(proc.stdout))
        data = json.loads(text)
    except Exception:
        return _all_needs_human()
    if not isinstance(data, dict):
        return _all_needs_human()
    return {k: _coerce_facet(data.get(k)) for k in FACET_KEYS}
