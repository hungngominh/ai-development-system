import json

from ai_dev_system.debate.profile import (
    ProjectProfile,
    infer_project_profile,
    vertical_relevance,
    PRODUCT_BEHAVIORAL_DOMAINS,
)
from ai_dev_system.debate.llm import StubDebateLLMClient
from ai_dev_system.debate.report import Question


class _FakeLLM:
    def __init__(self, response: str):
        self._response = response
    def complete(self, system: str, user: str) -> str:
        return self._response


def _q(domain: str) -> Question:
    return Question(id="Q1", text="t", classification="REQUIRED",
                    domain=domain, agent_a="ProductManager", agent_b="BackendArchitect")


def test_empty_profile_is_empty():
    p = ProjectProfile.empty()
    assert p.is_empty() is True
    assert p.key_dimensions == []


def test_valid_json_parses_into_profile():
    payload = json.dumps({
        "vertical": "couples relationship app",
        "primary_personas": ["long-distance couples"],
        "key_dimensions": ["couple psychology", "daily-usage habit", "retention"],
        "emotional_stakes": ["breakup anxiety"],
    })
    p = infer_project_profile({"idea": "an app for couples"}, _FakeLLM(payload))
    assert p.is_empty() is False
    assert p.vertical == "couples relationship app"
    assert "retention" in p.key_dimensions


def test_non_json_response_yields_empty_profile():
    p = infer_project_profile({"idea": "x"}, _FakeLLM("not json at all"))
    assert p.is_empty() is True


def test_json_that_is_not_an_object_yields_empty_profile():
    p = infer_project_profile({"idea": "x"}, _FakeLLM("[1, 2, 3]"))
    assert p.is_empty() is True


def test_stub_llm_yields_empty_profile():
    # critical backward-compat guarantee: under the stub, profile is always empty
    p = infer_project_profile({"idea": "x"}, StubDebateLLMClient())
    assert p.is_empty() is True


def test_kill_switch_env_yields_empty_profile(monkeypatch):
    monkeypatch.setenv("AI_DEV_DISABLE_VERTICAL_PROFILE", "1")
    payload = json.dumps({"vertical": "x", "key_dimensions": ["a"],
                          "primary_personas": [], "emotional_stakes": []})
    p = infer_project_profile({"idea": "x"}, _FakeLLM(payload))
    assert p.is_empty() is True


def test_vertical_relevance_fraction():
    profile = ProjectProfile("v", [], ["d"], [])
    qs = [_q("psychology"), _q("backend"), _q("growth"), _q("security")]
    assert vertical_relevance(qs, profile) == 0.5


def test_vertical_relevance_zero_when_profile_empty():
    qs = [_q("psychology")]
    assert vertical_relevance(qs, ProjectProfile.empty()) == 0.0
