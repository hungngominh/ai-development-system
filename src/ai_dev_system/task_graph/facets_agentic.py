"""Agentic, repo-grounded facet generation (Level B).

Runs the `claude` CLI in read-only, non-interactive mode with the target repo as
cwd, letting it Read/Grep/Glob the actual code to ground each facet.
Raises on failure so callers can surface the error. Use `_all_needs_human()` at
the call site when a silent fallback is wanted. Tests inject `invoke` (an
_invoke_claude-shaped callable); the real `claude` CLI is never invoked under test.
"""
from __future__ import annotations

import json
import os

from ai_dev_system.task_graph.facets import (
    SPEC_FACET_KEYS,
    EXEC_FACET_KEYS,
    FACET_DEFINITIONS,
    _EXEC_NA,
    _all_needs_human,
    _coerce_facet,
)
from ai_dev_system.llm_factory import ClaudeCodeLLMClient

# Read-only, non-interactive flags. stream-json (one NDJSON event per line)
# feeds the idle watchdog in _invoke_claude — a stalled CLI dies after
# SPEC_IDLE_TIMEOUT of silence instead of a fixed total budget; --verbose is
# required by the CLI for stream-json in -p mode. --max-turns is appended by
# _invoke_claude (see SPEC_MAX_TURNS).
_READONLY_FLAGS = [
    "--output-format", "stream-json", "--verbose",
    "--permission-mode", "bypassPermissions",
    "--disallowedTools", "Edit", "Write", "Bash", "PowerShell", "WebFetch", "WebSearch",
]

_DEFAULT_SPEC_MAX_TURNS = 40
_DEFAULT_SPEC_IDLE_TIMEOUT = 180.0
_DEFAULT_SPEC_HARD_TIMEOUT = 3600.0


def _spec_max_turns() -> int:
    """Resolve the claude --max-turns budget from SPEC_MAX_TURNS (fallback 40)."""
    raw = os.environ.get("SPEC_MAX_TURNS")
    if raw is None:
        return _DEFAULT_SPEC_MAX_TURNS
    try:
        n = int(raw)
    except ValueError:
        return _DEFAULT_SPEC_MAX_TURNS
    return n if n > 0 else _DEFAULT_SPEC_MAX_TURNS


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return default
    return v if v > 0 else default


def _spec_idle_timeout() -> float:
    """Kill claude only after this many seconds WITHOUT a new NDJSON event
    (SPEC_IDLE_TIMEOUT, default 180). Liveness, not total work, is the bound —
    a large repo may legitimately take 15+ minutes of active reading."""
    return _float_env("SPEC_IDLE_TIMEOUT", _DEFAULT_SPEC_IDLE_TIMEOUT)


def _spec_hard_timeout() -> float:
    """Safety ceiling against infinite loops (SPEC_HARD_TIMEOUT, default 3600)."""
    return _float_env("SPEC_HARD_TIMEOUT", _DEFAULT_SPEC_HARD_TIMEOUT)


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
    live_log_path=None,
    invoke=None,
    log=None,
) -> dict[str, dict]:
    """20 facets grounded in the repo at `repo_path`, via read-only `claude -p`
    (streamed through the shared _invoke_claude with an idle watchdog).

    Raises on failure so callers can surface the error. Use _all_needs_human()
    at the call site when a silent fallback is wanted.
    live_log_path: NDJSON tool events are appended here (the spec .log file).
    invoke: test seam — an _invoke_claude-shaped callable.
    log: optional callable(str) for progress/diagnostic lines.
    """
    def _log(msg):
        if log:
            log(msg)

    if not repo_path or not os.path.isdir(repo_path):
        raise ValueError(f"repo_path không hợp lệ hoặc không tồn tại: {repo_path!r}")
    claude = ClaudeCodeLLMClient._resolve_claude_cmd()
    if invoke is None:
        from ai_dev_system.agents.repo_branch_agent import _invoke_claude as invoke
    idle_s, hard_s = _spec_idle_timeout(), _spec_hard_timeout()
    run = invoke(
        claude, repo_path, _build_prompt(task), _spec_max_turns(), hard_s,
        live_log_path=live_log_path, model=model,
        flags=_READONLY_FLAGS, idle_timeout_s=idle_s,
    )
    if run.timed_out:
        if run.timeout_kind == "idle":
            raise RuntimeError(
                f"claude CLI treo: không có event mới trong {int(idle_s)}s "
                f"(SPEC_IDLE_TIMEOUT)")
        raise RuntimeError(
            f"claude CLI vượt trần an toàn {int(hard_s)}s (SPEC_HARD_TIMEOUT)")
    _log(f"claude CLI xong: rc={run.returncode} "
         f"stdout={len(run.stdout)}B stderr={len(run.stderr)}B")
    if run.returncode != 0:
        kind = f" ({run.subtype})" if run.subtype else ""
        raise RuntimeError(
            f"claude CLI trả về code {run.returncode}{kind}. "
            f"stderr: {run.stderr[:400]!r}. stdout: {run.stdout[:200]!r}"
        )
    raw = (run.result_event or {}).get("result") or ""
    if not raw:
        stdout = run.stdout or run.stderr
        try:
            raw = _extract_text(stdout)
        except (json.JSONDecodeError, ValueError):
            raw = stdout
    text = ClaudeCodeLLMClient._strip_outer_code_fence(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # claude prepended prose before the JSON fence — extract block directly.
        text = _find_json_block(raw)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"claude CLI trả về JSON không hợp lệ: {text[:200]!r}"
            ) from e
    if not isinstance(data, dict):
        raise RuntimeError(f"claude CLI trả về dữ liệu không phải dict: {text[:200]!r}")
    result = {k: _coerce_facet(data.get(k)) for k in SPEC_FACET_KEYS}
    for k in EXEC_FACET_KEYS:
        result[k] = _EXEC_NA.copy()
    return result
