import pytest
from ai_dev_system.debate.questions import generate_questions
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
