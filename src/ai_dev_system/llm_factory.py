"""
llm_factory.py — Unified real LLM client.

Satisfies both protocols:
- DebateLLMClient  (debate/llm.py)       → complete(system, user) -> str
- VerificationLLMClient (verification/judge.py) → judge_criterion(...) -> tuple
"""

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Literal

import anthropic
import openai


# ---------------------------------------------------------------------------
# Judge system prompt
# ---------------------------------------------------------------------------

_JUDGE_SYSTEM_PROMPT = """\
You are a rigorous software verification judge.
Given an acceptance criterion and supporting evidence from completed development tasks,
determine whether the criterion has been met.

Respond ONLY with a JSON object in this exact format:
{"verdict": "PASS" or "FAIL", "confidence": <float 0.0-1.0>, "reasoning": "<one paragraph>"}

- PASS if evidence clearly demonstrates the criterion is satisfied.
- FAIL if evidence is absent, insufficient, or contradicts the criterion.
- confidence: 1.0 = fully certain, 0.5 = borderline.
- Do not include any text outside the JSON object.\
"""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class LLMConfig:
    provider: str  # "anthropic", "openai", "azure", or "claude_code"
    model: str     # e.g. "claude-opus-4-5" / "gpt-4o" / "sonnet"
    api_key: str = ""
    azure_endpoint: str | None = None   # required when provider="azure"
    api_version: str | None = None      # required when provider="azure"

    @classmethod
    def from_env(cls) -> "LLMConfig":
        # --- provider ---
        provider_raw = os.environ.get("LLM_PROVIDER")
        if provider_raw is None:
            raise ValueError("LLM_PROVIDER is required (set to 'anthropic', 'openai', 'azure', or 'claude_code')")
        provider = provider_raw.strip()
        if provider not in ("anthropic", "openai", "azure", "claude_code"):
            raise ValueError(
                f"LLM_PROVIDER must be 'anthropic', 'openai', 'azure', or 'claude_code', got: {provider}"
            )

        # claude_code: routes through the `claude` CLI — no API key needed
        if provider == "claude_code":
            model = os.environ.get("LLM_MODEL", "sonnet")
            return cls(provider=provider, model=model)

        # --- model ---
        model = os.environ.get("LLM_MODEL")
        if not model:
            raise ValueError("LLM_MODEL is required")

        # --- api key + provider-specific config ---
        if provider == "anthropic":
            key = os.environ.get("ANTHROPIC_API_KEY")
            if not key:
                raise ValueError(
                    "ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic"
                )
            return cls(provider=provider, model=model, api_key=key)

        elif provider == "openai":
            key = os.environ.get("OPENAI_API_KEY")
            if not key:
                raise ValueError(
                    "OPENAI_API_KEY is required when LLM_PROVIDER=openai"
                )
            return cls(provider=provider, model=model, api_key=key)

        else:  # azure
            key = os.environ.get("AZURE_OPENAI_API_KEY")
            if not key:
                raise ValueError(
                    "AZURE_OPENAI_API_KEY is required when LLM_PROVIDER=azure"
                )
            endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
            if not endpoint:
                raise ValueError(
                    "AZURE_OPENAI_ENDPOINT is required when LLM_PROVIDER=azure"
                )
            api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-01")
            return cls(
                provider=provider,
                model=model,
                api_key=key,
                azure_endpoint=endpoint,
                api_version=api_version,
            )


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class ClaudeCodeLLMClient:
    """LLM client that routes calls through the `claude` CLI subprocess.

    Uses the authenticated Claude Code session (claude.ai Max subscription)
    instead of a direct API key. No ANTHROPIC_API_KEY required.
    """

    def __init__(self, model: str = "sonnet", timeout: int = 120, effort: str | None = None) -> None:
        self._model = model
        self._timeout = timeout
        self._effort = effort

    @staticmethod
    def _resolve_claude_cmd() -> str:
        """Resolve the claude CLI executable.

        On Windows, prefer the native ``claude.exe`` over the ``claude.cmd``
        batch shim. The shim re-expands every argument through cmd.exe
        (``"...claude.exe" %*``), which interprets shell metacharacters
        (``<``, ``>``, ``|``, ``&`` …) embedded in a prompt — exactly what
        debate/spec/judge system prompts contain (e.g. ``"<AgentKey>"`` and
        ``"REQUIRED"|"STRATEGIC"``). cmd.exe then tries to redirect/pipe and
        dies with "The system cannot find the file specified", before claude
        even starts. Invoking the ``.exe`` directly (shell=False) passes the
        argv verbatim and avoids the re-parse entirely.
        """
        if sys.platform == "win32":
            # 1. Native exe already on PATH (cheapest).
            exe = shutil.which("claude.exe")
            if exe:
                return exe
            # 2. Derive the bundled exe from the npm shim location.
            shim = shutil.which("claude.cmd") or shutil.which("claude")
            candidates = []
            if shim:
                shim_dir = os.path.dirname(shim)
                candidates.append(
                    os.path.join(
                        shim_dir, "node_modules", "@anthropic-ai",
                        "claude-code", "bin", "claude.exe",
                    )
                )
            candidates.append(
                os.path.expandvars(
                    r"%APPDATA%\npm\node_modules\@anthropic-ai"
                    r"\claude-code\bin\claude.exe"
                )
            )
            for cand in candidates:
                if os.path.exists(cand):
                    return cand
            # 3. Last resort: the batch shim. Works for simple prompts, but
            #    metacharacter-bearing prompts may fail (see docstring).
            if shim:
                return shim
            npm_cmd = os.path.expandvars(r"%APPDATA%\npm\claude.cmd")
            if os.path.exists(npm_cmd):
                return npm_cmd
            raise RuntimeError(
                "claude CLI not found. Install with: npm install -g @anthropic-ai/claude-code"
            )
        found = shutil.which("claude")
        if not found:
            raise RuntimeError(
                "claude CLI not found. Install with: npm install -g @anthropic-ai/claude-code"
            )
        return found

    @staticmethod
    def _strip_outer_code_fence(text: str) -> str:
        """Strip a Markdown code fence wrapping the WHOLE response.

        `claude -p` (Claude Code print mode) wraps a "return ONLY JSON"
        answer in ```json … ``` fences, unlike the raw Messages API.
        Downstream parsers (generate_questions, task-graph generator) call
        json.loads on the raw string, so normalize here. Only an outer
        fence spanning the entire (stripped) response is removed — inline
        or partial code blocks embedded in prose are left untouched.
        """
        s = text.strip()
        if not s.startswith("```"):
            return s
        nl = s.find("\n")
        if nl == -1:
            return s
        body = s[nl + 1:].rstrip()
        if body.endswith("```"):
            return body[: body.rfind("```")].strip()
        return s

    def _build_cmd(self, system: str, user: str) -> list[str]:
        """Assemble the `claude` CLI argv for one call.

        Effort (``--effort low|medium|high|xhigh|max``) is injected only when
        configured. Thinking is adaptive on Opus 4.7+/4.8 — it is driven by the
        effort level, so there is no separate thinking flag here. Haiku does not
        support ``--effort``; profiles leave effort unset for Haiku steps.
        """
        claude = self._resolve_claude_cmd()
        cmd = [claude, "-p", "--model", self._model]
        if self._effort:
            cmd += ["--effort", self._effort]
        cmd += ["--system-prompt", system, user]
        return cmd

    def _call(self, system: str, user: str) -> str:
        cmd = self._build_cmd(system, user)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            # Force UTF-8: claude -p emits UTF-8 (em-dashes, smart quotes,
            # non-Latin text, emoji), but text=True otherwise decodes with
            # the locale codec (cp1252 on Windows), which dies on bytes it
            # can't map (e.g. 0x81) and leaves stdout=None. errors="replace"
            # keeps a stray undecodable byte from sinking the whole call.
            encoding="utf-8",
            errors="replace",
            timeout=self._timeout,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"claude CLI exited {result.returncode}: {result.stderr.strip()}"
            )
        return self._strip_outer_code_fence(result.stdout)

    def complete(self, system: str, user: str) -> str:
        return self._call(system, user)

    def judge_criterion(
        self,
        criterion_id: str,
        criterion_text: str,
        evidence: list[str],
    ) -> tuple[Literal["PASS", "FAIL"], float, str]:
        evidence_text = "\n".join(f"{i+1}. {e}" for i, e in enumerate(evidence))
        user_prompt = (
            f"Criterion ID: {criterion_id}\n"
            f"Criterion: {criterion_text}\n\n"
            f"Evidence ({len(evidence)} items):\n{evidence_text}\n\n"
            f"Judge this criterion."
        )
        raw = self._call(_JUDGE_SYSTEM_PROMPT, user_prompt)
        cleaned = re.sub(r"```(?:json)?\s*(.*?)\s*```", r"\1", raw, flags=re.DOTALL)
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            raise ValueError(
                f"LLM returned non-JSON for criterion {criterion_id}: {raw[:200]}"
            )
        verdict = parsed.get("verdict")
        if verdict not in ("PASS", "FAIL"):
            raise ValueError(f"Invalid verdict '{verdict}' for criterion {criterion_id}")
        if "confidence" not in parsed or "reasoning" not in parsed:
            raise ValueError(
                f"LLM response missing required fields for {criterion_id}. "
                f"Got keys: {list(parsed.keys())}"
            )
        confidence = max(0.0, min(1.0, float(parsed["confidence"])))
        return (verdict, confidence, str(parsed["reasoning"]))


class RealLLMClient:
    def __init__(self, config: LLMConfig) -> None:
        self._config = config
        if config.provider == "anthropic":
            self._client = anthropic.Anthropic(api_key=config.api_key)
        elif config.provider == "azure":
            self._client = openai.AzureOpenAI(
                api_key=config.api_key,
                azure_endpoint=config.azure_endpoint,
                api_version=config.api_version,
            )
        else:  # openai
            self._client = openai.OpenAI(api_key=config.api_key)

    # --- DebateLLMClient protocol ---

    def complete(self, system: str, user: str) -> str:
        if self._config.provider == "anthropic":
            response = self._client.messages.create(
                model=self._config.model,
                max_tokens=4096,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            return response.content[0].text
        else:  # openai or azure (same SDK interface)
            response = self._client.chat.completions.create(
                model=self._config.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            content = response.choices[0].message.content
            if content is None:
                raise ValueError(f"OpenAI returned null content (finish_reason={response.choices[0].finish_reason!r})")
            return content

    # --- VerificationLLMClient protocol ---

    def judge_criterion(
        self,
        criterion_id: str,
        criterion_text: str,
        evidence: list[str],
    ) -> tuple[Literal["PASS", "FAIL"], float, str]:
        # 1. Build user prompt
        evidence_text = "\n".join(f"{i+1}. {e}" for i, e in enumerate(evidence))
        user_prompt = (
            f"Criterion ID: {criterion_id}\n"
            f"Criterion: {criterion_text}\n\n"
            f"Evidence ({len(evidence)} items):\n{evidence_text}\n\n"
            f"Judge this criterion."
        )

        # 2. Call LLM
        raw = self.complete(_JUDGE_SYSTEM_PROMPT, user_prompt)

        # 3. Strip markdown code fences
        cleaned = re.sub(r"```(?:json)?\s*(.*?)\s*```", r"\1", raw, flags=re.DOTALL)

        # 4. Parse JSON
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            raise ValueError(
                f"LLM returned non-JSON for criterion {criterion_id}: {raw[:200]}"
            )

        # 5. Validate and extract
        verdict = parsed.get("verdict")
        if verdict not in ("PASS", "FAIL"):
            raise ValueError(
                f"Invalid verdict '{verdict}' for criterion {criterion_id}"
            )
        if "confidence" not in parsed or "reasoning" not in parsed:
            raise ValueError(
                f"LLM response missing required fields for {criterion_id}. "
                f"Got keys: {list(parsed.keys())}"
            )
        confidence = max(0.0, min(1.0, float(parsed["confidence"])))
        reasoning = str(parsed["reasoning"])
        return (verdict, confidence, reasoning)


# ---------------------------------------------------------------------------
# Per-step model + effort profiles (claude_code / CLI path only)
# ---------------------------------------------------------------------------
#
# Each pipeline step has different intelligence needs. On the claude_code (Max
# CLI) path token cost is flat, so the real constraints are latency and the
# 5-hour rate-limit window — cheap steps run a light model to conserve quota for
# the steps that decide quality (debate, executor, judge).
#
# (model_alias, effort). effort=None means "don't pass --effort" (also required
# for Haiku, which doesn't accept the flag). Override any cell at runtime with
# env vars AI_DEV_MODEL_<STEP> / AI_DEV_EFFORT_<STEP> (STEP upper-cased).
#
# Only the claude_code provider honors these — API providers need full model IDs
# (e.g. claude-opus-4-8), not aliases, so they keep using env LLM_MODEL.

STEP_PROFILES: dict[str, tuple[str, str | None]] = {
    "intake":     ("haiku",  None),       # classification / short suggestions
    "gate":       ("sonnet", "medium"),   # gate review
    "questions":  ("sonnet", "medium"),   # decision inventory / question gen
    "debate":     ("opus",   "high"),     # ★ argument quality — core value
    "spec":       ("sonnet", "medium"),   # spec section generation / facets
    "critic":     ("sonnet", "medium"),   # spec self-review critic
    "task_graph": ("sonnet", "low"),      # structured enrichment
    "executor":   ("opus",   "xhigh"),    # ★ agentic code/test authoring
    "judge":      ("opus",   "high"),     # ★ verification — avoid false PASS
}


def resolve_step_model_effort(step: str) -> tuple[str, str | None]:
    """Resolve (model_alias, effort) for a pipeline step on the CLI path.

    Precedence: env override (AI_DEV_MODEL_<STEP> / AI_DEV_EFFORT_<STEP>) →
    STEP_PROFILES entry → env LLM_MODEL (or "sonnet") with no effort for an
    unknown/"default" step. An env effort of "" / "none" disables effort.
    """
    key = step.upper()
    profile_model, profile_effort = STEP_PROFILES.get(step, (None, None))

    model = os.environ.get(f"AI_DEV_MODEL_{key}") or profile_model \
        or os.environ.get("LLM_MODEL", "sonnet")

    effort_env = os.environ.get(f"AI_DEV_EFFORT_{key}")
    if effort_env is not None:
        effort = effort_env.strip() or None
        if effort and effort.lower() == "none":
            effort = None
    else:
        effort = profile_effort
    return model, effort


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def make_llm_client(step: str = "default") -> "RealLLMClient | ClaudeCodeLLMClient":
    """Build the LLM client for a pipeline step.

    On the claude_code (Max CLI) path the step selects model + effort via
    `resolve_step_model_effort`. On API providers the step is ignored (aliases
    aren't valid model IDs there) — they use env LLM_MODEL as before.

    `step="default"` reproduces the historical `make_real_llm_client()`
    behaviour exactly (env model, no effort), so existing callers are unaffected.
    """
    try:
        config = LLMConfig.from_env()
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    if config.provider == "claude_code":
        timeout = int(os.environ.get("CLAUDE_CODE_LLM_TIMEOUT", "120"))
        if step == "default":
            return ClaudeCodeLLMClient(model=config.model, timeout=timeout)
        model, effort = resolve_step_model_effort(step)
        return ClaudeCodeLLMClient(model=model, timeout=timeout, effort=effort)
    return RealLLMClient(config)


def make_real_llm_client() -> "RealLLMClient | ClaudeCodeLLMClient":
    """Backwards-compatible alias: the default-step client (no per-step tuning)."""
    return make_llm_client("default")
