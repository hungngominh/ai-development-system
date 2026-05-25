"""Diversity guardrails (M5.D, spec D4 + locked decision #6).

Two layers that fight the "two agents trivially agree" failure mode:

1. **Pre-debate**: `ensure_diverse_pair` rejects same-domain
   pairings and re-pairs via `AgentRegistry.pair_suggestion`.
2. **Mid-debate**: after round 1, `should_inject_skeptic` flags
   pairs whose embedding cosine similarity > 0.85; the round-2
   user prompt for the second agent is wrapped via
   `build_skeptic_round_user` to ask for a steel-man / edge case.

Embeddings flow through an `EmbeddingClient` Protocol so tests use
`StubEmbeddingClient` (deterministic hash-derived vectors) and
production wires in an OpenAI `text-embedding-3-small` adapter
(locked decision #6). The `EmbeddingCache` keys by sha256(text) to
cap memory on long position statements and to make cache hits
deterministic across reruns within a session.

This module is independent of the rounds orchestrator — callers
(M5.E or M5.F) compose the primitives.
"""

import hashlib
import math
import warnings
from dataclasses import dataclass, field
from typing import Protocol

from ai_dev_system.debate.agents import (
    AgentNotFoundError,
    AgentRegistry,
    PairSuggestionError,
)
from ai_dev_system.debate.report import Question

ECHO_SIMILARITY_THRESHOLD = 0.85

_SKEPTIC_PROMPT_TEMPLATE = (
    "Round 1 cho thấy bạn và {peer} đồng ý gần như hoàn toàn "
    "(cosine similarity > {threshold:.2f}). Hãy steel-man phía "
    "ngược lại, hoặc raise edge case mà cả 2 đã miss. "
    "Nếu sau khi cân nhắc bạn vẫn giữ quan điểm, nói rõ vì sao "
    "không có alternative reasonable.\n\n{original_user}"
)


# ---- protocols & clients ----


class EmbeddingClient(Protocol):
    """Minimal surface: `embed(text) -> list[float]`.

    Implementations may batch internally but the protocol stays
    one-text-in / one-vector-out to keep the caller (echo detector)
    simple.
    """

    def embed(self, text: str) -> list[float]: ...


class StubEmbeddingClient:
    """Deterministic hash-derived stub for tests.

    Maps the sha256 of `text` to a fixed-dimension vector using the
    digest bytes directly (bytes 0..dim-1 / 255). Same text → same
    vector across runs. Different texts produce vectors that are
    almost-orthogonal in expectation, which is enough for cosine
    comparisons in tests.
    """

    def __init__(self, dim: int = 32):
        if dim < 1 or dim > 64:
            raise ValueError("dim must be in [1, 64] for sha256-derived stub")
        self._dim = dim
        self.calls: list[str] = []

    def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [b / 255.0 for b in digest[: self._dim]]


# ---- similarity ----


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity in [-1, 1]. Raises ValueError on dim
    mismatch or all-zero vector."""
    if len(a) != len(b):
        raise ValueError(
            f"vector dim mismatch: len(a)={len(a)}, len(b)={len(b)}"
        )
    if not a:
        raise ValueError("empty vectors")
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        raise ValueError("zero-norm vector has no defined cosine similarity")
    return dot / (norm_a * norm_b)


def should_inject_skeptic(
    embedding_a: list[float],
    embedding_b: list[float],
    *,
    threshold: float = ECHO_SIMILARITY_THRESHOLD,
) -> bool:
    """True iff cosine similarity strictly exceeds threshold.

    Strict `>` (not `>=`) so the boundary case (exactly equal to
    threshold) does not trigger; matches spec D4 wording
    "cosine_similarity(emb_a, emb_b) > 0.85".
    """
    return cosine_similarity(embedding_a, embedding_b) > threshold


# ---- embedding cache ----


@dataclass
class EmbeddingCache:
    """In-memory text→vector cache, keyed by sha256 of the text.

    Hits are O(1). The cache has no eviction; callers managing very
    long sessions should construct a fresh cache per debate run.
    Stats are exposed for telemetry / eval reporting.
    """

    _store: dict[str, list[float]] = field(default_factory=dict)
    hits: int = 0
    misses: int = 0

    @staticmethod
    def _key(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def get_or_compute(self, text: str, client: EmbeddingClient) -> list[float]:
        key = self._key(text)
        if key in self._store:
            self.hits += 1
            return self._store[key]
        self.misses += 1
        vec = client.embed(text)
        self._store[key] = vec
        return vec

    def __len__(self) -> int:
        return len(self._store)

    def __contains__(self, text: str) -> bool:
        return self._key(text) in self._store


# ---- pre-debate: same-domain rejection ----


def ensure_diverse_pair(
    question: Question,
    registry: AgentRegistry,
    decision_domains: list[str],
) -> Question:
    """Return a Question whose agent_a and agent_b live in different
    domains.

    Behavior:
        - Both agents resolve in `registry` AND share a domain →
          re-pair: keep agent_a, replace agent_b via
          `registry.pair_suggestion(agent_a, decision_domains)`.
          Returns a new Question (input is not mutated).
        - Both resolve AND already differ → return input unchanged.
        - Either agent missing from registry → warn and return input
          unchanged (cannot verify; defer to fallback prompts).
        - pair_suggestion raises PairSuggestionError → warn, return
          input unchanged (best-effort).
    """
    try:
        spec_a = registry.get(question.agent_a)
        spec_b = registry.get(question.agent_b)
    except AgentNotFoundError as e:
        warnings.warn(
            f"DiversityCheck: agent {e.args[0]!r} not in registry; "
            f"skipping same-domain check for question {question.id!r}",
            stacklevel=2,
        )
        return question

    if spec_a.domain != spec_b.domain:
        return question

    try:
        new_b = registry.pair_suggestion(question.agent_a, decision_domains)
    except PairSuggestionError as e:
        warnings.warn(
            f"DiversityCheck: same-domain pair "
            f"({question.agent_a}/{question.agent_b}) but no alternative: {e}",
            stacklevel=2,
        )
        return question

    return Question(
        id=question.id,
        text=question.text,
        classification=question.classification,
        domain=question.domain,
        agent_a=question.agent_a,
        agent_b=new_b,
        source_decision_id=question.source_decision_id,
    )


# ---- mid-debate: skeptic prompt wrapper ----


def build_skeptic_round_user(
    base_user: str,
    peer_agent_key: str,
    *,
    threshold: float = ECHO_SIMILARITY_THRESHOLD,
) -> str:
    """Wrap an agent's round-2 user prompt with the skeptic prefix
    when echo was detected in round 1."""
    return _SKEPTIC_PROMPT_TEMPLATE.format(
        peer=peer_agent_key,
        threshold=threshold,
        original_user=base_user,
    )
