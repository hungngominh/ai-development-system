"""ProjectProfile — the vertical/persona "lens" for question personalization.

Inferred once per run from the brief/idea, then injected into question
generation so the output spans product/behavioral dimensions, not just
technical ones. Inference is *resilient*: any failure (bad JSON, non-dict,
stub LLM, kill-switch) yields an empty profile, which injects nothing —
preserving today's behavior and the stub-based test suite.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

PROMPT_PATH = Path(__file__).parent / "questions" / "prompts" / "profile.txt"

# Domains that count as "product / behavioral" for personalization checks.
PRODUCT_BEHAVIORAL_DOMAINS: frozenset[str] = frozenset(
    {"psychology", "growth", "research", "product", "design"}
)

_KILL_SWITCH_ENV = "AI_DEV_DISABLE_VERTICAL_PROFILE"


@dataclass
class ProjectProfile:
    vertical: str = ""
    primary_personas: list[str] = field(default_factory=list)
    key_dimensions: list[str] = field(default_factory=list)
    emotional_stakes: list[str] = field(default_factory=list)

    @classmethod
    def empty(cls) -> "ProjectProfile":
        return cls()

    def is_empty(self) -> bool:
        return not self.key_dimensions

    def to_dict(self) -> dict:
        return {
            "vertical": self.vertical,
            "primary_personas": list(self.primary_personas),
            "key_dimensions": list(self.key_dimensions),
            "emotional_stakes": list(self.emotional_stakes),
        }


def infer_project_profile(brief: dict, llm_client) -> ProjectProfile:
    """Infer a ProjectProfile from the brief. Never raises."""
    if os.environ.get(_KILL_SWITCH_ENV) == "1":
        return ProjectProfile.empty()
    try:
        from ai_dev_system.debate.questions._prompt_utils import split_prompt  # deferred to break circular import
        system, user_template = split_prompt(PROMPT_PATH.read_text(encoding="utf-8"))
        user = user_template.replace(
            "{brief_json}", json.dumps(brief, ensure_ascii=False, default=str)
        )
        raw = llm_client.complete(system=system, user=user)
        data = json.loads(raw)
    except Exception:
        return ProjectProfile.empty()
    if not isinstance(data, dict):
        return ProjectProfile.empty()
    return ProjectProfile(
        vertical=str(data.get("vertical") or ""),
        primary_personas=[str(x) for x in (data.get("primary_personas") or [])],
        key_dimensions=[str(x) for x in (data.get("key_dimensions") or [])],
        emotional_stakes=[str(x) for x in (data.get("emotional_stakes") or [])],
    )


def profile_prompt_block(profile: "ProjectProfile") -> str:
    """Render an injectable PROJECT PROFILE block, or '' when empty."""
    if profile is None or profile.is_empty():
        return ""
    dims = "; ".join(profile.key_dimensions)
    personas = ", ".join(profile.primary_personas) or "the stated users"
    return (
        "PROJECT PROFILE (personalization lens):\n"
        f"- vertical: {profile.vertical}\n"
        f"- primary users: {personas}\n"
        f"- key product/behavioral dimensions: {dims}\n"
        "ALSO surface product/behavioral items across these dimensions; tag them "
        "with domain one of psychology, growth, research, product, design.\n"
    )


def vertical_relevance(questions, profile: ProjectProfile) -> float:
    """Fraction of questions whose domain is product/behavioral. 0.0 when
    there are no questions or the profile is empty."""
    if profile.is_empty() or not questions:
        return 0.0
    hits = sum(1 for q in questions if q.domain in PRODUCT_BEHAVIORAL_DOMAINS)
    return hits / len(questions)
