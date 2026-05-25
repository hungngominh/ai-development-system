"""M5.E full-wire engine tests.

Covers: DebateConfig overrides, required_min_rounds floor, calibrated
moderator selection, brief_digest/decision injection, ensure_diverse_pair
integration, echo detection + diversity penalty + skeptic injection.
"""

import json
import warnings

import pytest

from ai_dev_system.debate.agents import AgentRegistry, AgentSpec
from ai_dev_system.debate.config import DebateConfig
from ai_dev_system.debate.diversity import StubEmbeddingClient
from ai_dev_system.debate.engine import run_debate
from ai_dev_system.debate.llm import StubDebateLLMClient
from ai_dev_system.debate.questions.models import Decision
from ai_dev_system.debate.report import Question


REQUIRED_Q = Question(
    id="Q1", text="Auth?", classification="REQUIRED",
    domain="security",
    agent_a="SecuritySpecialist", agent_b="BackendArchitect",
    source_decision_id="D1",
)
STRATEGIC_Q = Question(
    id="Q2", text="DB engine?", classification="STRATEGIC",
    domain="database",
    agent_a="DatabaseSpecialist", agent_b="BackendArchitect",
)


# ---- DebateConfig wiring ----


def test_required_min_rounds_override_to_1():
    """Caller can opt out of the 2-round floor."""
    client = StubDebateLLMClient()
    cfg = DebateConfig(required_min_rounds=1)
    report = run_debate([REQUIRED_Q], client, run_id="r", brief={}, config=cfg)
    assert len(report.results[0].rounds) == 1


def test_required_min_rounds_override_to_3():
    """3-round floor forces 3 rounds even though stub returns 0.9."""
    client = StubDebateLLMClient()
    cfg = DebateConfig(required_min_rounds=3)
    report = run_debate([REQUIRED_Q], client, run_id="r", brief={}, config=cfg)
    assert len(report.results[0].rounds) == 3


def test_strategic_ignores_required_min_rounds():
    """STRATEGIC questions are not subject to required_min_rounds."""
    client = StubDebateLLMClient()
    cfg = DebateConfig(required_min_rounds=4)
    report = run_debate([STRATEGIC_Q], client, run_id="r", brief={}, config=cfg)
    assert len(report.results[0].rounds) == 1


# ---- Calibrated moderator prompt selection ----


class _SystemRecordingClient:
    """Wraps StubDebateLLMClient and stores every system prompt seen."""

    def __init__(self):
        self.systems: list[str] = []
        self._stub = StubDebateLLMClient()

    def complete(self, system: str, user: str) -> str:
        self.systems.append(system)
        return self._stub.complete(system, user)


def test_calibrated_moderator_prompt_used_by_default():
    client = _SystemRecordingClient()
    run_debate([STRATEGIC_Q], client, run_id="r", brief={})
    mod_systems = [s for s in client.systems if "moderator" in s.lower()]
    assert mod_systems  # at least one moderator call
    assert all("CALIBRATION" in s for s in mod_systems)


def test_legacy_moderator_prompt_when_opt_out():
    client = _SystemRecordingClient()
    cfg = DebateConfig(use_calibrated_moderator=False)
    run_debate([STRATEGIC_Q], client, run_id="r", brief={}, config=cfg)
    mod_systems = [s for s in client.systems if "moderator" in s.lower()]
    assert mod_systems
    assert all("CALIBRATION" not in s for s in mod_systems)


# ---- brief_digest + decision injection ----


def test_brief_digest_and_decision_reach_agent_prompts():
    class _UserRecorder:
        def __init__(self):
            self.users: list[str] = []
            self._stub = StubDebateLLMClient()

        def complete(self, system, user):
            self.users.append(user)
            return self._stub.complete(system, user)

    client = _UserRecorder()
    decision = Decision(
        id="D1",
        summary="Pick an auth scheme",
        classification="REQUIRED",
        domain_hints=["security"],
        blocks_what=["login"],
    )
    run_debate(
        [REQUIRED_Q], client, run_id="r", brief={},
        brief_digest="My brief.",
        decisions=[decision],
    )
    # Agent A/B user prompts (skip moderator user which doesn't get block)
    agent_users = [u for u in client.users if "Đưa ra" in u or "Phản biện" in u]
    assert agent_users
    assert all("My brief." in u for u in agent_users)
    assert all("Decision đang debate (D1)" in u for u in agent_users)


def test_decision_lookup_skips_when_source_decision_id_missing():
    """Question with no source_decision_id → no decision injected, no crash."""
    q = Question(
        id="Q9", text="any?", classification="STRATEGIC",
        domain="database",
        agent_a="DatabaseSpecialist", agent_b="BackendArchitect",
    )
    client = StubDebateLLMClient()
    decision = Decision(
        id="D1", summary="x", classification="REQUIRED",
        domain_hints=["security"],
    )
    # should not raise
    report = run_debate(
        [q], client, run_id="r", brief={},
        brief_digest="brief",
        decisions=[decision],
    )
    assert len(report.results) == 1


# ---- ensure_diverse_pair integration ----


def _spec(key: str, domain: str) -> AgentSpec:
    return AgentSpec(key=key, domain=domain, version=1, system_prompt="prompt")


def test_same_domain_pair_is_repaired_when_registry_provided():
    """If both agents share a domain in the registry, agent_b is swapped."""
    specs = [
        _spec("SecuritySpecialist", "security"),
        _spec("SecondSecurity", "security"),
        _spec("BackendArchitect", "backend"),
    ]
    registry = AgentRegistry.from_specs(specs)

    q = Question(
        id="Q1", text="x?", classification="STRATEGIC",
        domain="security",
        agent_a="SecuritySpecialist", agent_b="SecondSecurity",
    )
    client = StubDebateLLMClient()
    report = run_debate([q], client, run_id="r", brief={}, registry=registry)
    # registry-aware engine path re-paired agent_b; report carries the
    # rewritten question
    assert report.results[0].question.agent_a == "SecuritySpecialist"
    assert report.results[0].question.agent_b != "SecondSecurity"


def test_different_domain_pair_left_alone():
    specs = [
        _spec("SecuritySpecialist", "security"),
        _spec("BackendArchitect", "backend"),
    ]
    registry = AgentRegistry.from_specs(specs)
    client = StubDebateLLMClient()
    report = run_debate([REQUIRED_Q], client, run_id="r", brief={}, registry=registry)
    assert report.results[0].question.agent_b == "BackendArchitect"


# ---- echo detection + diversity penalty + skeptic injection ----


class _EchoModeratorClient:
    """First moderator response = high confidence (would normally stop after
    floor); also returns identical agent positions to guarantee echo."""

    AGENT_POSITION = "Identical position from both agents."

    def __init__(self):
        self.user_prompts: list[str] = []

    def complete(self, system: str, user: str) -> str:
        self.user_prompts.append(user)
        s = system.lower()
        if "moderator" in s:
            return json.dumps({
                "status": "RESOLVED",
                "confidence": 0.9,
                "summary": "agree",
                "caveat": None,
            })
        return self.AGENT_POSITION


def test_echo_detection_applies_diversity_penalty():
    client = _EchoModeratorClient()
    embed = StubEmbeddingClient()
    cfg = DebateConfig(required_min_rounds=1)  # let confidence alone gate
    report = run_debate(
        [STRATEGIC_Q], client, run_id="r", brief={},
        config=cfg,
        embedding_client=embed,
    )
    rounds = report.results[0].rounds
    # Identical positions → similarity = 1.0 > 0.85 → round 1 confidence
    # is multiplied by 0.7 → 0.63, below threshold → debate goes to round 2.
    assert len(rounds) >= 2
    assert rounds[0].confidence == pytest.approx(0.9 * 0.7)
    assert rounds[0].caveat is not None
    assert "Echo detected" in rounds[0].caveat


def test_echo_detection_triggers_skeptic_wrap_round_2():
    client = _EchoModeratorClient()
    embed = StubEmbeddingClient()
    cfg = DebateConfig(required_min_rounds=1)
    run_debate(
        [STRATEGIC_Q], client, run_id="r", brief={},
        config=cfg,
        embedding_client=embed,
    )
    # Round 2 Agent B user prompt should carry the skeptic prefix
    agent_b_round2 = client.user_prompts[4]  # r1: a,b,mod  r2: a,b,...
    assert "Round 1 cho thấy" in agent_b_round2


def test_no_embedding_client_no_echo_detection():
    """Without embedding_client, no penalty is applied even on identical positions."""
    client = _EchoModeratorClient()
    cfg = DebateConfig(required_min_rounds=1)
    report = run_debate(
        [STRATEGIC_Q], client, run_id="r", brief={},
        config=cfg,
    )
    # No penalty → round 1 confidence stays at 0.9, debate stops early
    assert len(report.results[0].rounds) == 1
    assert report.results[0].rounds[0].confidence == 0.9


def test_inject_skeptic_disabled_does_not_wrap_round_2():
    """inject_skeptic_on_echo=False: penalty still applied, skeptic prefix NOT."""
    client = _EchoModeratorClient()
    embed = StubEmbeddingClient()
    cfg = DebateConfig(
        required_min_rounds=1,
        inject_skeptic_on_echo=False,
    )
    run_debate(
        [STRATEGIC_Q], client, run_id="r", brief={},
        config=cfg,
        embedding_client=embed,
    )
    if len(client.user_prompts) >= 5:
        agent_b_round2 = client.user_prompts[4]
        assert "Round 1 cho thấy" not in agent_b_round2


def test_diversity_penalty_clamped_to_unit_interval():
    """Confidence multiplied by penalty stays in [0, 1]."""
    client = _EchoModeratorClient()
    embed = StubEmbeddingClient()
    cfg = DebateConfig(
        required_min_rounds=1,
        diversity_confidence_penalty=2.0,  # would exceed 1.0 → must clamp
    )
    report = run_debate(
        [STRATEGIC_Q], client, run_id="r", brief={},
        config=cfg,
        embedding_client=embed,
    )
    assert report.results[0].rounds[0].confidence <= 1.0
    assert report.results[0].rounds[0].confidence >= 0.0
