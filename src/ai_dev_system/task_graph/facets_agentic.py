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
    SPEC_FACET_KEYS,
    EXEC_FACET_KEYS,
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
    facet_lines = "\n".join(f"- {k}: {FACET_DEFINITIONS[k]}" for k in SPEC_FACET_KEYS)
    return (
        "You are detailing ONE implementation task against THIS repository. Use "
        "Read/Grep/Glob to inspect the actual code relevant to the task (data "
        "models, schema/migrations, the modules this task touches). For each of the "
        "13 engineering facets below, write a concrete, code-grounded detail and cite "
        "the file path(s) you used. Mark a facet \"na\" (with a reason) when "
        "irrelevant, or \"needs_human\" when you find NO evidence in the code — do "
        "NOT invent. Ignore .env, secrets, node_modules, and build output.\n\n"
        f"TASK:\n- objective: {task.get('objective', '')}\n"
        f"- description: {task.get('description', '')}\n\n"
        "Return ONLY a JSON object keyed by the 13 facet names; each value is "
        '{"status": "filled"|"na"|"needs_human", "content": "...", "reason": "..."}.\n'
        "Facets:\n" + facet_lines
    )


def _build_command(claude: str, prompt: str, *, model: str | None = None) -> list[str]:
    cmd = [claude, "-p", prompt, *_READONLY_FLAGS]
    if model:
        cmd += ["--model", model]
    return cmd


def _find_json_block(text: str) -> str:
    """Extract the first ```json … ``` (or ``` … ```) block from prose text.

    When claude prepends reasoning prose before the JSON fence, the outer
    _strip_outer_code_fence cannot strip it (text doesn't START with ```).
    This function finds the fence wherever it appears.
    """
    idx = text.find("```")
    if idx == -1:
        return text
    nl = text.find("\n", idx)
    if nl == -1:
        return text
    close = text.find("\n```", nl + 1)
    if close == -1:
        return text
    return text[nl + 1:close].strip()


def _extract_text(stdout: str) -> str:
    """Pull the assistant text out of the --output-format json wrapper.

    claude -p with tools outputs NDJSON (one JSON event per line). The final
    line with "type":"result" holds the assistant's text in "result". Single-
    turn responses (no tool calls) may be a single JSON object instead.
    """
    # Try NDJSON first: scan lines in reverse for the result event.
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("type") == "result":
            result = obj.get("result")
            if isinstance(result, str):
                return result
        # messages fallback (older CLI shape)
        messages = obj.get("messages")
        if isinstance(messages, list):
            parts = [m.get("content") for m in messages
                     if isinstance(m, dict) and isinstance(m.get("content"), str)]
            if parts:
                return "\n".join(parts)
    # Last resort: treat the whole stdout as a single JSON object.
    wrapper = json.loads(stdout)
    if isinstance(wrapper, dict):
        result = wrapper.get("result")
        if isinstance(result, str):
            return result
    return stdout


def generate_task_facets_agentic(
    task: dict,
    repo_path: str,
    *,
    model: str | None = None,
    timeout: int = 300,
    run=subprocess.run,
    log=None,
) -> dict[str, dict]:
    """20 facets grounded in the repo at `repo_path`, via read-only `claude -p`.

    Raises on failure so callers can surface the error. Use _all_needs_human()
    at the call site when a silent fallback is wanted.
    log: optional callable(str) for progress/diagnostic lines.
    """
    def _log(msg):
        if log:
            log(msg)

    if not repo_path or not os.path.isdir(repo_path):
        raise ValueError(f"repo_path không hợp lệ hoặc không tồn tại: {repo_path!r}")
    claude = ClaudeCodeLLMClient._resolve_claude_cmd()
    cmd = _build_command(claude, _build_prompt(task), model=model)
    proc = run(
        cmd, cwd=repo_path, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    _log(f"claude CLI xong: rc={proc.returncode} stdout={len(stdout)}B stderr={len(stderr)}B")
    if proc.returncode != 0:
        raise RuntimeError(
            f"claude CLI trả về code {proc.returncode}. "
            f"stderr: {stderr[:400]!r}. stdout: {stdout[:200]!r}"
        )
    if not stdout.strip() and stderr.strip():
        # Node.js on Windows (DETACHED_PROCESS) sometimes writes to stderr instead of stdout.
        _log("stdout trống, thử dùng stderr thay thế")
        stdout = stderr
    raw = _extract_text(stdout)
    text = ClaudeCodeLLMClient._strip_outer_code_fence(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # claude prepended prose before the JSON fence — extract block directly.
        text = _find_json_block(raw)
        data = json.loads(text)
    if not isinstance(data, dict):
        raise RuntimeError(f"claude CLI trả về dữ liệu không phải dict: {text[:200]!r}")
    result = {k: _coerce_facet(data.get(k)) for k in SPEC_FACET_KEYS}
    for k in EXEC_FACET_KEYS:
        result[k] = {"status": "na", "content": "", "reason": "exec-time — fill after implementation"}
    return result
