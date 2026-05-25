"""M5.D diversity guardrails tests.

Covers: cosine_similarity (math + edge cases), should_inject_skeptic
(strict > boundary), EmbeddingCache (hit/miss/stats), StubEmbeddingClient
(determinism + dim guard), ensure_diverse_pair (re-pair / no-op /
unknown-agent / no-alternative), build_skeptic_round_user template.
"""

import math

import pytest

from ai_dev_system.debate.agents import AgentRegistry, AgentSpec
from ai_dev_system.debate.diversity import (
    ECHO_SIMILARITY_THRESHOLD,
    EmbeddingCache,
    StubEmbeddingClient,
    build_skeptic_round_user,
    cosine_similarity,
    ensure_diverse_pair,
    should_inject_skeptic,
)
from ai_dev_system.debate.report import Question


# ---- helpers ----


def _question(
    qid: str = "Q1",
    agent_a: str = "A",
    agent_b: str = "B",
    *,
    domain: str = "backend",
) -> Question:
    return Question(
        id=qid,
        text="should we?",
        classification="REQUIRED",
        domain=domain,
        agent_a=agent_a,
        agent_b=agent_b,
        source_decision_id="d1",
    )


def _spec(key: str, domain: str, paired: list[str] | None = None) -> AgentSpec:
    return AgentSpec(
        key=key,
        domain=domain,
        version=1,
        aliases=[],
        debate_role="neutral",
        typical_paired_with=paired or [],
        system_prompt=f"{key} prompt",
        file_path=None,
        is_fallback=False,
    )


# ---- cosine_similarity ----


def test_cosine_identical_vectors_is_one():
    assert cosine_similarity([1.0, 0.0, 0.0], [1.0, 0.0, 0.0]) == pytest.approx(1.0)


def test_cosine_opposite_vectors_is_minus_one():
    assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_cosine_orthogonal_vectors_is_zero():
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_scale_invariant():
    a_small = [1.0, 2.0, 3.0]
    a_big = [10.0, 20.0, 30.0]
    assert cosine_similarity(a_small, a_big) == pytest.approx(1.0)


def test_cosine_dim_mismatch_raises():
    with pytest.raises(ValueError, match="dim mismatch"):
        cosine_similarity([1.0, 2.0], [1.0, 2.0, 3.0])


def test_cosine_empty_raises():
    with pytest.raises(ValueError, match="empty"):
        cosine_similarity([], [])


def test_cosine_zero_norm_raises():
    with pytest.raises(ValueError, match="zero-norm"):
        cosine_similarity([0.0, 0.0], [1.0, 1.0])


# ---- should_inject_skeptic ----


def test_skeptic_triggers_above_threshold():
    a = [1.0, 0.0, 0.0]
    b = [0.95, math.sqrt(1 - 0.95**2), 0.0]  # cos ≈ 0.95 > 0.85
    assert should_inject_skeptic(a, b) is True


def test_skeptic_does_not_trigger_below_threshold():
    a = [1.0, 0.0, 0.0]
    b = [0.0, 1.0, 0.0]  # cos = 0
    assert should_inject_skeptic(a, b) is False


def test_skeptic_strict_greater_than_at_boundary():
    # Exactly == threshold → False (strict >)
    a = [1.0, 0.0]
    b_at = [ECHO_SIMILARITY_THRESHOLD, math.sqrt(1 - ECHO_SIMILARITY_THRESHOLD**2)]
    # cos(a, b_at) = ECHO_SIMILARITY_THRESHOLD exactly
    assert cosine_similarity(a, b_at) == pytest.approx(ECHO_SIMILARITY_THRESHOLD)
    assert should_inject_skeptic(a, b_at) is False


def test_skeptic_custom_threshold():
    a = [1.0, 0.0]
    b = [0.6, 0.8]  # cos = 0.6
    assert should_inject_skeptic(a, b, threshold=0.5) is True
    assert should_inject_skeptic(a, b, threshold=0.7) is False


# ---- StubEmbeddingClient ----


def test_stub_returns_deterministic_vector_for_same_text():
    client = StubEmbeddingClient(dim=16)
    v1 = client.embed("hello")
    v2 = client.embed("hello")
    assert v1 == v2
    assert len(v1) == 16


def test_stub_different_texts_produce_different_vectors():
    client = StubEmbeddingClient(dim=16)
    assert client.embed("a") != client.embed("b")


def test_stub_records_calls():
    client = StubEmbeddingClient()
    client.embed("foo")
    client.embed("bar")
    assert client.calls == ["foo", "bar"]


def test_stub_rejects_invalid_dim():
    with pytest.raises(ValueError):
        StubEmbeddingClient(dim=0)
    with pytest.raises(ValueError):
        StubEmbeddingClient(dim=65)


# ---- EmbeddingCache ----


def test_cache_first_call_misses_calls_client():
    cache = EmbeddingCache()
    client = StubEmbeddingClient(dim=8)
    vec = cache.get_or_compute("hello", client)
    assert cache.misses == 1
    assert cache.hits == 0
    assert len(client.calls) == 1
    assert "hello" in cache


def test_cache_second_call_hits_skips_client():
    cache = EmbeddingCache()
    client = StubEmbeddingClient(dim=8)
    v1 = cache.get_or_compute("hello", client)
    v2 = cache.get_or_compute("hello", client)
    assert v1 == v2
    assert cache.hits == 1
    assert cache.misses == 1
    assert len(client.calls) == 1


def test_cache_different_texts_are_independent():
    cache = EmbeddingCache()
    client = StubEmbeddingClient()
    cache.get_or_compute("a", client)
    cache.get_or_compute("b", client)
    assert len(cache) == 2
    assert cache.misses == 2


# ---- ensure_diverse_pair ----


def test_pair_unchanged_when_domains_already_differ():
    reg = AgentRegistry.from_specs([
        _spec("A", "backend", paired=["B"]),
        _spec("B", "security"),
    ])
    q = _question(agent_a="A", agent_b="B")
    result = ensure_diverse_pair(q, reg, ["backend", "security"])
    assert result is q  # input returned unchanged


def test_pair_replaced_when_same_domain():
    reg = AgentRegistry.from_specs([
        _spec("A", "backend", paired=["C"]),
        _spec("B", "backend"),  # collides with A
        _spec("C", "security"),
    ])
    q = _question(agent_a="A", agent_b="B")
    result = ensure_diverse_pair(q, reg, ["backend", "security"])
    assert result.agent_a == "A"
    assert result.agent_b == "C"
    # other fields preserved
    assert result.id == q.id
    assert result.text == q.text
    assert result.source_decision_id == q.source_decision_id


def test_pair_unknown_agent_warns_and_returns_input():
    reg = AgentRegistry.from_specs([_spec("A", "backend")])
    q = _question(agent_a="A", agent_b="Ghost")
    with pytest.warns(UserWarning, match="not in registry"):
        result = ensure_diverse_pair(q, reg, ["backend"])
    assert result is q


def test_pair_no_alternative_warns_and_returns_input():
    # Both agents same domain, no other agent exists at all
    reg = AgentRegistry.from_specs([
        _spec("A", "backend"),
        _spec("B", "backend"),
    ])
    q = _question(agent_a="A", agent_b="B")
    with pytest.warns(UserWarning, match="no alternative"):
        result = ensure_diverse_pair(q, reg, ["backend"])
    assert result is q  # fell back to original


def test_pair_replacement_does_not_mutate_input():
    reg = AgentRegistry.from_specs([
        _spec("A", "backend", paired=["C"]),
        _spec("B", "backend"),
        _spec("C", "security"),
    ])
    q = _question(agent_a="A", agent_b="B")
    result = ensure_diverse_pair(q, reg, ["backend", "security"])
    assert q.agent_b == "B"  # original untouched
    assert result.agent_b == "C"
    assert result is not q


# ---- build_skeptic_round_user ----


def test_skeptic_prompt_contains_peer_name_and_original():
    out = build_skeptic_round_user("original instruction body", "BackendArchitect")
    assert "BackendArchitect" in out
    assert "original instruction body" in out
    assert "steel-man" in out


def test_skeptic_prompt_mentions_threshold():
    out = build_skeptic_round_user("orig", "Peer", threshold=0.9)
    assert "0.90" in out


def test_skeptic_prompt_default_threshold_in_output():
    out = build_skeptic_round_user("orig", "Peer")
    assert f"{ECHO_SIMILARITY_THRESHOLD:.2f}" in out
