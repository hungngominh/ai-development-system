import json

from ai_dev_system.debate.questions.legacy import (
    generate_questions, SYSTEM_PROMPT,
)
from ai_dev_system.debate.profile import ProjectProfile


class _CaptureLLM:
    """Captures the system prompt; returns a fixed valid question array."""
    def __init__(self):
        self.system_seen = None
    def complete(self, system: str, user: str) -> str:
        self.system_seen = system
        return json.dumps([{
            "id": "Q1", "text": "How do we drive daily emotional engagement?",
            "classification": "REQUIRED", "domain": "psychology",
            "agent_a": "BehavioralPsychologist", "agent_b": "RetentionGrowthStrategist",
        }])


def test_empty_profile_leaves_system_prompt_unchanged():
    llm = _CaptureLLM()
    generate_questions({"idea": "x"}, llm, profile=ProjectProfile.empty())
    assert llm.system_seen == SYSTEM_PROMPT  # byte-identical to legacy


def test_no_profile_arg_leaves_system_prompt_unchanged():
    llm = _CaptureLLM()
    generate_questions({"idea": "x"}, llm)
    assert llm.system_seen == SYSTEM_PROMPT


def test_profile_injects_dimensions_and_new_agent_keys():
    llm = _CaptureLLM()
    profile = ProjectProfile(
        vertical="couples relationship app",
        primary_personas=["long-distance couples"],
        key_dimensions=["couple psychology", "retention"],
        emotional_stakes=["breakup anxiety"],
    )
    qs = generate_questions({"idea": "x"}, llm, profile=profile)
    assert "couple psychology" in llm.system_seen
    assert "BehavioralPsychologist" in llm.system_seen
    # the new persona keys must survive validation (be accepted, not defaulted)
    assert qs[0].agent_a == "BehavioralPsychologist"
    assert qs[0].domain == "psychology"
