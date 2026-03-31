"""
llm_factory.py — Unified real LLM client.

Satisfies both protocols:
- DebateLLMClient  (debate/llm.py)       → complete(system, user) -> str
- VerificationLLMClient (verification/judge.py) → judge_criterion(...) -> tuple
"""

import json
import os
import re
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
    provider: str  # "anthropic", "openai", or "azure"
    model: str     # e.g. "claude-opus-4-5" / "gpt-4o" / "my-deployment-name"
    api_key: str
    azure_endpoint: str | None = None   # required when provider="azure"
    api_version: str | None = None      # required when provider="azure"

    @classmethod
    def from_env(cls) -> "LLMConfig":
        # --- provider ---
        provider_raw = os.environ.get("LLM_PROVIDER")
        if provider_raw is None:
            raise ValueError("LLM_PROVIDER is required (set to 'anthropic', 'openai', or 'azure')")
        provider = provider_raw.strip()
        if provider not in ("anthropic", "openai", "azure"):
            raise ValueError(
                f"LLM_PROVIDER must be 'anthropic', 'openai', or 'azure', got: {provider}"
            )

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
# Factory
# ---------------------------------------------------------------------------

def make_real_llm_client() -> RealLLMClient:
    try:
        config = LLMConfig.from_env()
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    return RealLLMClient(config)
