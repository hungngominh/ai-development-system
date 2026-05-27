# src/ai_dev_system/debate/engine.py
"""Debate engine — orchestrates per-question round loops.

v1 behaviour (no M5.E kwargs supplied):
    Each REQUIRED/STRATEGIC question runs up to MAX_ROUNDS, exits
    early when confidence >= CONFIDENCE_THRESHOLD. OPTIONAL is
    auto-resolved without LLM calls.

M5.E (spec D6 + D7) — activated by passing any of `config`,
`registry`, `embedding_client`, `brief_digest`, `decisions`:

- `ensure_diverse_pair` is applied per-question before debate when
  `registry` is supplied (spec D4 same-domain rejection).
- Each round gets `brief_digest` + the matching `Decision` injected
  into Agent A/B user prompts (spec D6).
- REQUIRED questions must run at least `config.required_min_rounds`
  rounds regardless of confidence (spec D7 Mitigation 1).
- After round 1 with `embedding_client` present, agent positions are
  embedded and compared. If similarity > `config.echo_similarity_threshold`:
    * the round's confidence is multiplied by
      `config.diversity_confidence_penalty` (spec D7 Mitigation 3);
    * round 2's Agent B prompt is wrapped with the skeptic prefix
      (spec D4 echo recovery, gated by `config.inject_skeptic_on_echo`).
- The moderator system prompt swaps to MODERATOR_PROMPT_CALIBRATED
  when `config.use_calibrated_moderator` is True (spec D7 Mitigation 2).
"""

import dataclasses
import warnings
from datetime import datetime, timezone

from ai_dev_system.debate.agents import (
    MODERATOR_PROMPT,
    MODERATOR_PROMPT_CALIBRATED,
    AgentRegistry,
)
from ai_dev_system.debate.config import DebateConfig
from ai_dev_system.debate.diversity import (
    EmbeddingCache,
    EmbeddingClient,
    cosine_similarity,
    ensure_diverse_pair,
)
from ai_dev_system.debate.questions.models import Decision
from ai_dev_system.debate.report import (
    DebateReport,
    Question,
    QuestionDebateResult,
    RoundResult,
    auto_resolve,
)
from ai_dev_system.debate.rounds import run_debate_round

MAX_ROUNDS = 5
CONFIDENCE_THRESHOLD = 0.8


def _resolve_decision(
    question: Question,
    decision_map: dict[str, Decision] | None,
) -> Decision | None:
    if not decision_map or not question.source_decision_id:
        return None
    return decision_map.get(question.source_decision_id)


def _apply_diversity_penalty(
    round_result: RoundResult,
    penalty: float,
) -> RoundResult:
    """Return a new RoundResult with confidence multiplied by penalty
    and a caveat note appended."""
    new_confidence = max(0.0, min(1.0, round_result.confidence * penalty))
    note = (
        f"Echo detected (similarity > threshold); "
        f"confidence reduced from {round_result.confidence:.2f} "
        f"to {new_confidence:.2f}."
    )
    caveat = (
        f"{round_result.caveat}\n{note}" if round_result.caveat else note
    )
    return dataclasses.replace(
        round_result,
        confidence=new_confidence,
        caveat=caveat,
    )


def _lookup_dense_prompt(
    registry: AgentRegistry | None,
    agent_key: str,
) -> str | None:
    """Return the dense `.md`-loaded system prompt for `agent_key`,
    or None if no registry was wired / agent missing.

    Returning None tells run_debate_round to fall back to the legacy
    AGENT_PROMPTS dict — preserving v1 behaviour when no registry is
    available. A missing key with a wired registry warns once so the
    operator notices the degraded mode.
    """
    if registry is None:
        return None
    try:
        spec = registry.get(agent_key)
    except KeyError:
        warnings.warn(
            f"AgentRegistry: no spec for {agent_key!r}; "
            f"falling back to legacy 3-line prompt",
            DeprecationWarning,
            stacklevel=2,
        )
        return None
    return spec.system_prompt


def _debate_one(
    question: Question,
    llm_client,
    *,
    config: DebateConfig,
    decision: Decision | None,
    brief_digest: str | None,
    embedding_client: EmbeddingClient | None,
    embedding_cache: EmbeddingCache | None,
    moderator_prompt: str,
    registry: AgentRegistry | None = None,
) -> QuestionDebateResult:
    """Run the round loop for a single non-OPTIONAL question."""
    prev_summary: str | None = None
    rounds: list[RoundResult] = []
    inject_skeptic_next = False

    # Spec D10: prefer registry-loaded dense prompts; fall back to
    # legacy 3-line prompts when no registry is wired.
    agent_a_prompt = _lookup_dense_prompt(registry, question.agent_a)
    agent_b_prompt = _lookup_dense_prompt(registry, question.agent_b)

    for round_num in range(1, config.max_rounds + 1):
        result = run_debate_round(
            question,
            round_num,
            prev_summary,
            llm_client,
            brief_digest=brief_digest,
            decision=decision,
            inject_skeptic=inject_skeptic_next,
            moderator_system_prompt=moderator_prompt,
            max_moderator_retries=config.max_moderator_retries,
            agent_a_system_prompt=agent_a_prompt,
            agent_b_system_prompt=agent_b_prompt,
        )

        # M5.D + D7 echo detection: only meaningful round 1 → round 2.
        # Skip if no embedding client, or for MODERATOR_PARSE_FAILED
        # (positions still exist but the confidence is already 0).
        if (
            round_num == 1
            and embedding_client is not None
            and result.agent_a_position
            and result.agent_b_position
        ):
            cache = embedding_cache or EmbeddingCache()
            try:
                emb_a = cache.get_or_compute(result.agent_a_position, embedding_client)
                emb_b = cache.get_or_compute(result.agent_b_position, embedding_client)
                sim = cosine_similarity(emb_a, emb_b)
            except ValueError as e:
                warnings.warn(
                    f"Echo detection failed for question {question.id!r}: {e}",
                    stacklevel=2,
                )
            else:
                if sim > config.echo_similarity_threshold:
                    result = _apply_diversity_penalty(
                        result, config.diversity_confidence_penalty
                    )
                    inject_skeptic_next = config.inject_skeptic_on_echo

        rounds.append(result)

        # M5.E (D7 Mitigation 1): REQUIRED must hit min rounds
        if (
            question.classification == "REQUIRED"
            and round_num < config.required_min_rounds
        ):
            prev_summary = result.moderator_summary
            continue

        if result.confidence >= config.confidence_threshold:
            break
        prev_summary = result.moderator_summary

    return QuestionDebateResult(question=question, rounds=rounds, final=rounds[-1])


def run_debate(
    questions: list[Question],
    llm_client,
    run_id: str,
    brief: dict,
    *,
    config: DebateConfig | None = None,
    registry: AgentRegistry | None = None,
    embedding_client: EmbeddingClient | None = None,
    brief_digest: str | None = None,
    decisions: list[Decision] | None = None,
) -> DebateReport:
    """Run debate for all questions. OPTIONAL questions are auto-resolved.

    All M5.E enrichment knobs are optional and default to v1 behaviour
    when omitted. See module docstring for the exact effect of each.
    """
    cfg = config or DebateConfig()
    moderator_prompt = (
        MODERATOR_PROMPT_CALIBRATED if cfg.use_calibrated_moderator else MODERATOR_PROMPT
    )
    decision_map = (
        {d.id: d for d in decisions} if decisions else None
    )
    # one cache per debate run keeps memory bounded and lets multiple
    # questions sharing an agent position reuse the embedding.
    embedding_cache = EmbeddingCache() if embedding_client is not None else None

    results: list[QuestionDebateResult] = []
    for q in questions:
        if q.classification == "OPTIONAL":
            # Spec D9: pass the matched Decision (if any) so the
            # auto_resolution_reason references the safe default.
            results.append(auto_resolve(q, _resolve_decision(q, decision_map)))
            continue

        # M5.D pre-debate diversity check
        if registry is not None:
            decision = _resolve_decision(q, decision_map)
            domain_hints = decision.domain_hints if decision else [q.domain]
            q = ensure_diverse_pair(q, registry, domain_hints)

        decision = _resolve_decision(q, decision_map)

        results.append(
            _debate_one(
                q,
                llm_client,
                config=cfg,
                decision=decision,
                brief_digest=brief_digest,
                embedding_client=embedding_client,
                embedding_cache=embedding_cache,
                moderator_prompt=moderator_prompt,
                registry=registry,
            )
        )

    return DebateReport(
        run_id=run_id,
        brief=brief,
        results=results,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
