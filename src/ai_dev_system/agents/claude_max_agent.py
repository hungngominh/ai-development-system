"""ClaudeMaxAgent — execute a build task through Claude Max (the `claude` CLI),
no API key required.

Unlike the CrewAI agent (which routes through litellm and needs a provider API
key), this agent talks to the same unified `ClaudeCodeLLMClient` used by the
debate/spec/verification phases. To keep execution deterministic and sandboxed,
it does NOT let `claude -p` write files with tools — it asks Claude to RETURN
file contents as a JSON object and writes them itself, refusing any path that
escapes ``output_path``.

Implements the duck-typed ``Agent`` protocol (agents/base.py): ``run(...)``
returns an ``AgentResult`` whose files the worker then promotes to artifacts.
"""
from __future__ import annotations

import json
import os
from typing import Optional

from ai_dev_system.agents.base import AgentResult

_SYSTEM_PROMPT = """\
You are an autonomous software engineer executing ONE task in a build pipeline.
Produce the required output files for the task.

Respond with ONLY a JSON object, no prose outside it:
{"files": {"<relative/path>": "<full file content>", ...}, "summary": "<one line>"}

Rules:
- File paths MUST be relative (no leading slash, no drive letter, no "..").
- Include a file for EACH required output, named EXACTLY as requested.
- Put complete, runnable file contents as strings (escape newlines per JSON).
"""

_MAX_INPUT_BYTES = 20_000


def _is_within(base: str, dest: str) -> bool:
    try:
        return os.path.commonpath([base, dest]) == base
    except ValueError:
        # Different drives on Windows → definitely outside.
        return False


class ClaudeMaxAgent:
    def __init__(self, llm=None, model: str = "sonnet", timeout_s: float = 3600.0,
                 effort: str | None = None) -> None:
        self._model = model
        self._timeout_s = timeout_s
        self._effort = effort
        self._llm = llm  # injected for tests; built lazily otherwise

    def _client(self):
        if self._llm is None:
            from ai_dev_system.llm_factory import ClaudeCodeLLMClient
            self._llm = ClaudeCodeLLMClient(
                model=self._model, timeout=int(self._timeout_s), effort=self._effort
            )
        return self._llm

    # --- Agent protocol ---

    def run(
        self,
        task_id: str,
        output_path: str,
        promoted_outputs=(),
        context: Optional[dict] = None,
        timeout_s: float = 3600.0,
        file_rules: list = (),
    ) -> AgentResult:
        os.makedirs(output_path, exist_ok=True)
        promoted_list = list(promoted_outputs)
        context = context or {}

        system = self._build_system(file_rules)
        user = self._build_user(task_id, context, promoted_list)

        try:
            raw = self._client().complete(system, user)
        except Exception as exc:  # noqa: BLE001 — surface any backend failure as a task error
            return AgentResult(output_path=output_path, error=f"claude_max agent LLM call failed: {exc}")

        try:
            files = self._parse_files(raw)
            self._write_files(output_path, files)
        except ValueError as exc:
            return AgentResult(output_path=output_path, error=f"claude_max agent: {exc}")

        missing = [
            po.name for po in promoted_list
            if not os.path.exists(os.path.join(output_path, po.name))
        ]
        if missing:
            return AgentResult(
                output_path=output_path,
                error=(
                    f"claude_max agent: missing required output(s) {missing}; "
                    f"model wrote {sorted(files)}"
                ),
            )

        return AgentResult(output_path=output_path, promoted_outputs=promoted_list)

    # --- prompt building ---

    def _build_system(self, file_rules) -> str:
        system = _SYSTEM_PROMPT
        rules = [str(r) for r in (file_rules or [])]
        if rules:
            system += "\nProject rules to honour:\n" + "\n".join(f"- {r}" for r in rules)
        return system

    def _build_user(self, task_id: str, context: dict, promoted_list) -> str:
        lines = [f"# Task {context.get('task_id', task_id)}"]
        for key in ("phase", "type", "agent_type", "objective", "description", "done_definition"):
            val = context.get(key)
            if val:
                lines.append(f"{key}: {val}")

        req_names = [po.name for po in promoted_list]
        if req_names:
            lines.append("")
            lines.append(f"Required output files (exact names): {req_names}")

        expected = context.get("expected_outputs")
        if expected and not req_names:
            lines.append(f"expected_outputs: {expected}")

        inputs = context.get("required_inputs") or []
        if inputs:
            lines.append("")
            lines.append("## Inputs")
            for inp in inputs:
                lines.append(self._render_input(inp))

        facets = context.get("facets") or {}
        facet_lines = []
        for key, facet in facets.items():
            if not isinstance(facet, dict):
                continue
            status = facet.get("status")
            if status == "filled" and facet.get("content"):
                facet_lines.append(f"- {key}: {facet['content']}")
            elif status == "needs_human":
                facet_lines.append(f"- {key}: (needs clarification — confirm before relying on it)")
            # status == "na" → skip
        if facet_lines:
            lines.append("")
            lines.append("## Task Specification")
            lines.extend(facet_lines)

        return "\n".join(lines)

    def _render_input(self, inp) -> str:
        if isinstance(inp, str):
            return f"- {inp}"
        name = inp.get("name", "?")
        path = inp.get("path")
        content = self._read_path(path) if path else ""
        if content:
            return f"- {name} ({path}):\n```\n{content}\n```"
        return f"- {name}"

    @staticmethod
    def _read_path(path: str, limit: int = _MAX_INPUT_BYTES) -> str:
        try:
            if os.path.isfile(path):
                with open(path, encoding="utf-8", errors="replace") as f:
                    return f.read(limit)
            if os.path.isdir(path):
                parts = []
                for fn in sorted(os.listdir(path))[:20]:
                    fp = os.path.join(path, fn)
                    if os.path.isfile(fp):
                        with open(fp, encoding="utf-8", errors="replace") as f:
                            parts.append(f"### {fn}\n{f.read(limit // 4)}")
                return "\n".join(parts)
        except OSError:
            return ""
        return ""

    # --- response handling ---

    @staticmethod
    def _parse_files(raw: str) -> dict:
        text = (raw or "").strip()
        # Defensive: strip an outer ```json fence if the client didn't already.
        if text.startswith("```"):
            nl = text.find("\n")
            if nl != -1:
                body = text[nl + 1:].rstrip()
                if body.endswith("```"):
                    text = body[: body.rfind("```")].strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            raise ValueError(f"expected a JSON object with a 'files' map, got: {raw[:200]!r}")
        files = data.get("files") if isinstance(data, dict) else None
        if not isinstance(files, dict) or not files:
            raise ValueError(f"response has no non-empty 'files' object: {raw[:200]!r}")
        return files

    @staticmethod
    def _write_files(output_path: str, files: dict) -> None:
        base = os.path.abspath(output_path)
        for relpath, content in files.items():
            if not isinstance(relpath, str) or not isinstance(content, str):
                raise ValueError(f"file entry must be str->str, got {relpath!r}")
            dest = os.path.abspath(os.path.join(base, relpath))
            if not _is_within(base, dest):
                raise ValueError(f"refusing to write outside output dir: {relpath!r}")
            os.makedirs(os.path.dirname(dest) or base, exist_ok=True)
            with open(dest, "w", encoding="utf-8") as f:
                f.write(content)


def make_claude_max_agent(model: str = "sonnet", timeout_s: float = 3600.0,
                          effort: str | None = None) -> ClaudeMaxAgent:
    """Factory: a Max-backed execution agent (no API key needed)."""
    return ClaudeMaxAgent(model=model, timeout_s=timeout_s, effort=effort)
