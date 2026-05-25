"""M4.2 Question Materializer tests.

Covers: empty input, batch happy path, classification override from
Decision, domain alias + unknown warn, agent fallback,
source_decision_id validation, missing-decision warn, batch JSON fail
→ per-decision fallback, per-decision partial success, per-decision
total failure → MaterializerError, mode parameter, prompt rendering.
"""

import json

import pytest

from ai_dev_system.debate.questions import materializer
from ai_dev_system.debate.questions.materializer import (
    DEFAULT_AGENT_A,
    DEFAULT_AGENT_B,
    MaterializerError,
)
from ai_dev_system.debate.questions.models import Decision
from ai_dev_system.debate.report import Question


# ---- helpers ----


class FakeLLM:
    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if not self._responses:
            raise AssertionError("FakeLLM exhausted its canned responses")
        return self._responses.pop(0)


def _decision(
    id_: str = "d1",
    *,
    blocks: list[str] | None = None,
    safe_default: bool = False,
    domain_hints: list[str] | None = None,
) -> Decision:
    return Decision(
        id=id_,
        summary=f"Decide {id_}",
        classification="REQUIRED",  # value is irrelevant; overridden in pipeline
        blocks_what=blocks if blocks is not None else ["voting"],
        has_safe_default=safe_default,
        domain_hints=domain_hints if domain_hints is not None else ["backend"],
    )


def _question_payload(decision_id: str, **overrides) -> dict:
    base = {
        "id": f"Q-{decision_id}",
        "text": f"What should we do about {decision_id}?",
        "classification": "REQUIRED",
        "domain": "backend",
        "agent_a": "BackendArchitect",
        "agent_b": "ProductManager",
        "source_decision_id": decision_id,
    }
    base.update(overrides)
    return base


# ---- empty input ----


def test_run_returns_empty_for_no_decisions():
    llm = FakeLLM([])
    assert materializer.run([], brief_digest="", llm_client=llm) == []
    assert llm.calls == []


# ---- batch happy path ----


def test_run_batch_happy_path():
    decisions = [_decision("d1"), _decision("d2"), _decision("d3")]
    response = json.dumps([_question_payload(d.id) for d in decisions])
    llm = FakeLLM([response])

    questions = materializer.run(decisions, brief_digest="digest", llm_client=llm)

    assert len(questions) == 3
    assert all(isinstance(q, Question) for q in questions)
    assert [q.source_decision_id for q in questions] == ["d1", "d2", "d3"]
    assert len(llm.calls) == 1
    _, user_sent = llm.calls[0]
    assert "digest" in user_sent
    assert '"id": "d1"' in user_sent


# ---- classification override ----


def test_classification_required_when_blocks_no_default():
    decisions = [_decision("d1", blocks=["voting"], safe_default=False)]
    response = json.dumps([_question_payload("d1", classification="OPTIONAL")])
    llm = FakeLLM([response])
    questions = materializer.run(decisions, brief_digest="", llm_client=llm)
    assert questions[0].classification == "REQUIRED"


def test_classification_strategic_when_blocks_with_default():
    decisions = [_decision("d1", blocks=["voting"], safe_default=True)]
    response = json.dumps([_question_payload("d1", classification="REQUIRED")])
    llm = FakeLLM([response])
    questions = materializer.run(decisions, brief_digest="", llm_client=llm)
    assert questions[0].classification == "STRATEGIC"


def test_classification_optional_when_no_blocks():
    decisions = [_decision("d1", blocks=[], safe_default=False)]
    response = json.dumps([_question_payload("d1", classification="REQUIRED")])
    llm = FakeLLM([response])
    questions = materializer.run(decisions, brief_digest="", llm_client=llm)
    assert questions[0].classification == "OPTIONAL"


# ---- domain handling ----


def test_domain_alias_resolves_to_canonical():
    decisions = [_decision("d1")]
    response = json.dumps([_question_payload("d1", domain="react")])
    llm = FakeLLM([response])
    questions = materializer.run(decisions, brief_digest="", llm_client=llm)
    assert questions[0].domain == "frontend"


def test_unknown_domain_warns_and_falls_back():
    decisions = [_decision("d1")]
    response = json.dumps([_question_payload("d1", domain="blockchain")])
    llm = FakeLLM([response])
    with pytest.warns(UserWarning, match="DOMAIN_UNRECOGNIZED"):
        questions = materializer.run(decisions, brief_digest="", llm_client=llm)
    assert questions[0].domain == "backend"


def test_missing_domain_uses_decision_first_hint():
    decisions = [_decision("d1", domain_hints=["security", "infra"])]
    payload = _question_payload("d1")
    payload.pop("domain")
    response = json.dumps([payload])
    llm = FakeLLM([response])
    questions = materializer.run(decisions, brief_digest="", llm_client=llm)
    assert questions[0].domain == "security"


def test_missing_domain_and_empty_hints_defaults_to_backend():
    decisions = [_decision("d1", domain_hints=[])]
    payload = _question_payload("d1")
    payload.pop("domain")
    response = json.dumps([payload])
    llm = FakeLLM([response])
    questions = materializer.run(decisions, brief_digest="", llm_client=llm)
    assert questions[0].domain == "backend"


# ---- agent fallback ----


def test_invalid_agent_falls_back_to_defaults():
    decisions = [_decision("d1")]
    response = json.dumps([
        _question_payload("d1", agent_a="NotAnAgent", agent_b="AlsoNotAnAgent")
    ])
    llm = FakeLLM([response])
    questions = materializer.run(decisions, brief_digest="", llm_client=llm)
    assert questions[0].agent_a == DEFAULT_AGENT_A
    assert questions[0].agent_b == DEFAULT_AGENT_B


# ---- source_decision_id validation ----


def test_batch_source_id_unknown_triggers_per_decision_fallback():
    decisions = [_decision("d1"), _decision("d2")]
    bad_batch = json.dumps([
        _question_payload("d1"),
        _question_payload("d2", source_decision_id="d999"),
    ])
    good_per_decision = [
        json.dumps([_question_payload("d1")]),
        json.dumps([_question_payload("d2")]),
    ]
    llm = FakeLLM([bad_batch] + good_per_decision)
    with pytest.warns(UserWarning, match="falling back to per-decision"):
        questions = materializer.run(decisions, brief_digest="", llm_client=llm)
    assert len(questions) == 2
    assert {q.source_decision_id for q in questions} == {"d1", "d2"}


def test_batch_missing_decision_warns():
    decisions = [_decision("d1"), _decision("d2"), _decision("d3")]
    # only return d1 + d2; d3 missing
    response = json.dumps([_question_payload("d1"), _question_payload("d2")])
    llm = FakeLLM([response])
    with pytest.warns(UserWarning, match=r"skipped decisions: \['d3'\]"):
        questions = materializer.run(decisions, brief_digest="", llm_client=llm)
    assert len(questions) == 2


# ---- batch JSON failure → per-decision ----


def test_batch_invalid_json_falls_back_to_per_decision():
    decisions = [_decision("d1"), _decision("d2")]
    per_decision_responses = [
        json.dumps([_question_payload("d1")]),
        json.dumps([_question_payload("d2")]),
    ]
    llm = FakeLLM(["not valid json {"] + per_decision_responses)
    with pytest.warns(UserWarning, match="falling back to per-decision"):
        questions = materializer.run(decisions, brief_digest="", llm_client=llm)
    assert len(questions) == 2
    # 1 batch attempt + 2 per-decision calls
    assert len(llm.calls) == 3


def test_per_decision_accepts_dict_response_shape():
    decisions = [_decision("d1")]
    # batch fails, per-decision returns a dict (not array)
    llm = FakeLLM([
        "not json",
        json.dumps(_question_payload("d1")),
    ])
    with pytest.warns(UserWarning):
        questions = materializer.run(decisions, brief_digest="", llm_client=llm)
    assert len(questions) == 1


def test_per_decision_partial_success_returns_what_worked():
    decisions = [_decision("d1"), _decision("d2"), _decision("d3")]
    # batch fails entirely; per-decision: d1 ok, d2 bad json, d3 ok
    llm = FakeLLM([
        "batch broken",
        json.dumps([_question_payload("d1")]),
        "per-decision broken",
        json.dumps([_question_payload("d3")]),
    ])
    with pytest.warns(UserWarning):
        questions = materializer.run(decisions, brief_digest="", llm_client=llm)
    assert [q.source_decision_id for q in questions] == ["d1", "d3"]


def test_per_decision_total_failure_raises():
    decisions = [_decision("d1"), _decision("d2")]
    llm = FakeLLM(["batch broken", "also broken", "still broken"])
    with pytest.warns(UserWarning):
        with pytest.raises(MaterializerError, match="zero questions"):
            materializer.run(decisions, brief_digest="", llm_client=llm)


# ---- mode parameter ----


def test_mode_retrigger_is_accepted():
    decisions = [_decision("d1")]
    response = json.dumps([_question_payload("d1")])
    llm = FakeLLM([response])
    # Pure smoke: mode informational, doesn't change happy-path behavior
    questions = materializer.run(decisions, brief_digest="", llm_client=llm, mode="retrigger")
    assert len(questions) == 1


# ---- prompt helpers ----


def test_load_prompt_template_present():
    text = materializer.load_prompt()
    assert "SYSTEM" in text
    assert "USER" in text
    assert "{decisions_json}" in text
    assert "{brief_digest}" in text


def test_split_prompt_separates_sections():
    system, user_template = materializer._split_prompt(materializer.load_prompt())
    assert system.startswith("You are a senior")
    assert "{decisions_json}" in user_template
    assert "{brief_digest}" in user_template
