import json

import pytest
from ai_dev_system.debate.questions import (
    generate_questions,
    SYSTEM_PROMPT,
    SYSTEM_PROMPT_BRIEF_V2,
)
from ai_dev_system.debate.llm import StubDebateLLMClient
from ai_dev_system.debate.report import Question

SAMPLE_BRIEF = {
    "raw_idea": "Build a task manager",
    "problem": "Teams lose track of tasks",
    "target_users": "Small teams",
    "goal": "Track tasks efficiently",
    "constraints": {"hard": ["GDPR"], "soft": []},
    "assumptions": [],
    "scope": {"type": "new_feature", "complexity_hint": "medium"},
    "success_signals": ["tasks tracked"],
}


def test_generate_questions_returns_list_of_questions():
    client = StubDebateLLMClient()
    questions = generate_questions(SAMPLE_BRIEF, client)
    assert isinstance(questions, list)
    assert len(questions) >= 1
    assert all(isinstance(q, Question) for q in questions)


def test_generate_questions_valid_classifications():
    client = StubDebateLLMClient()
    questions = generate_questions(SAMPLE_BRIEF, client)
    for q in questions:
        assert q.classification in ("REQUIRED", "STRATEGIC", "OPTIONAL")


def test_generate_questions_valid_agent_keys():
    from ai_dev_system.debate.agents import VALID_AGENT_KEYS
    client = StubDebateLLMClient()
    questions = generate_questions(SAMPLE_BRIEF, client)
    for q in questions:
        assert q.agent_a in VALID_AGENT_KEYS
        assert q.agent_b in VALID_AGENT_KEYS


def test_generate_questions_unique_ids():
    client = StubDebateLLMClient()
    questions = generate_questions(SAMPLE_BRIEF, client)
    ids = [q.id for q in questions]
    assert len(ids) == len(set(ids))


class _CapturingLLM:
    def __init__(self, response: str):
        self._response = response
        self.last_system: str | None = None
        self.last_user: str | None = None

    def complete(self, system: str, user: str) -> str:
        self.last_system = system
        self.last_user = user
        return self._response


_QUESTION_LIST = json.dumps([{
    "id": "Q1", "text": "Auth model?", "classification": "REQUIRED",
    "domain": "security", "agent_a": "SecuritySpecialist", "agent_b": "BackendArchitect",
}])


def test_generate_questions_v1_brief_uses_legacy_prompt():
    """Brief without brief_version → original SYSTEM_PROMPT."""
    llm = _CapturingLLM(_QUESTION_LIST)
    generate_questions(SAMPLE_BRIEF, llm)
    assert llm.last_system == SYSTEM_PROMPT


def test_generate_questions_brief_v2_uses_v2_prompt():
    """Brief with brief_version=2 → SYSTEM_PROMPT_BRIEF_V2."""
    brief_v2 = {
        "brief_version": 2,
        "fields": {"problem_statement": {"value": "x", "source": "user"}},
        "assumptions": [],
    }
    llm = _CapturingLLM(_QUESTION_LIST)
    questions = generate_questions(brief_v2, llm)
    assert llm.last_system == SYSTEM_PROMPT_BRIEF_V2
    # Brief is passed through verbatim in the user payload.
    sent = json.loads(llm.last_user)
    assert sent["brief_version"] == 2
    assert len(questions) == 1
    assert questions[0].id == "Q1"
