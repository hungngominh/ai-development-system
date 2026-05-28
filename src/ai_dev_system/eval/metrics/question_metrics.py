"""Question metrics — Layer 2 of eval harness.

5 rule-based metrics (M1 scope):
  Q1. required_decision_coverage   — fraction of required decisions matched ≥1 question
  Q2. forbidden_decision_rate      — fraction of forbidden decisions matched ≥1 question (must be 0)
  Q3. duplicate_pair_count         — count of question pairs sharing decision_id (rough proxy w/o embedding)
  Q4. domain_balance_entropy       — Shannon entropy over question.domain distribution
  Q5. avg_question_length          — mean char count per question text

2 LLM-based metrics (M3 scope):
  Q6. binary_yes_no_ratio          — fraction LLM-rated "binary yes/no without context" (stub → 0.5)
  Q7. scope_drift_count            — count LLM-rated "unrelated to brief.scope_in" (stub → 0)

Q8. classification_distribution   — rule-based shape; already computed as part of Q metrics.

Per spec 2026-05-23-evaluation-harness-design.md.
"""
from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Protocol

from ai_dev_system.eval.golden_loader import GoldenIdea


class EvalLLMClient(Protocol):
    """Minimal LLM interface used by eval judge calls."""

    def complete(self, system: str, user: str) -> str:
        """Call LLM with system + user prompt. Returns raw string."""
        ...


THRESHOLDS = {
    "required_decision_coverage": 0.85,    # ≥ 85% of required decisions covered
    "forbidden_decision_rate_max": 0.0,    # zero tolerance
    "duplicate_pair_count_max": 0,         # zero (decision-id collisions)
    "domain_balance_entropy_min": 1.5,     # Shannon entropy ≥ 1.5 nats
    "avg_question_length_min": 60,
    "avg_question_length_max": 300,
    "binary_yes_no_ratio_max": 0.15,       # Q6: ≤ 15% binary questions
    "scope_drift_count_max": 0,            # Q7: zero tolerance for drift
}


@dataclass
class QuestionMetricsReport:
    required_decision_coverage: float = 0.0
    forbidden_decision_rate: float = 0.0
    duplicate_pair_count: int = 0
    domain_balance_entropy: float = 0.0
    avg_question_length: float = 0.0
    classification_distribution: dict[str, float] = field(default_factory=dict)
    binary_yes_no_ratio: float = 0.5          # Q6; 0.5 = stub/unknown
    scope_drift_count: int = 0                 # Q7; 0 = stub/unknown
    llm_metrics_mode: str = "stub"             # "stub" | "real"

    pass_required_coverage: bool = False
    pass_forbidden_rate: bool = False
    pass_duplicate: bool = False
    pass_domain_entropy: bool = False
    pass_avg_length: bool = False
    pass_binary_ratio: bool = True            # stub always passes (neutral 0.5 ≤ threshold)
    pass_scope_drift: bool = True             # stub always passes (0 = target)

    missed_required: list[str] = field(default_factory=list)
    forbidden_hits: list[dict] = field(default_factory=list)  # [{decision_id, question_text}]
    duplicate_pairs: list[tuple[str, str]] = field(default_factory=list)
    binary_yes_no_questions: list[str] = field(default_factory=list)   # Q6 offenders
    scope_drift_questions: list[str] = field(default_factory=list)     # Q7 offenders
    question_count: int = 0

    def overall_pass(self) -> bool:
        return all([
            self.pass_required_coverage,
            self.pass_forbidden_rate,
            self.pass_duplicate,
            self.pass_domain_entropy,
            self.pass_avg_length,
            self.pass_binary_ratio,
            self.pass_scope_drift,
        ])

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _question_text(q: Any) -> str:
    """Extract text from a question (handles dict or Question dataclass)."""
    if isinstance(q, dict):
        return q.get("text", "")
    return getattr(q, "text", "")


def _question_domain(q: Any) -> str:
    if isinstance(q, dict):
        return q.get("domain", "unknown")
    return getattr(q, "domain", "unknown")


def _question_classification(q: Any) -> str:
    if isinstance(q, dict):
        return q.get("classification", "UNKNOWN")
    return getattr(q, "classification", "UNKNOWN")


def _question_source_decision(q: Any) -> str | None:
    """Extract source_decision_id if present (new field from Spec A)."""
    if isinstance(q, dict):
        return q.get("source_decision_id")
    return getattr(q, "source_decision_id", None)


def compute_required_decision_coverage(
    questions: Iterable[Any],
    idea: GoldenIdea,
) -> tuple[float, list[str]]:
    """Fraction of required_decisions where ≥1 question matches its pattern.

    Returns (coverage_rate, missed_decision_ids).
    """
    questions = list(questions)
    if not idea.required_decisions:
        return 1.0, []

    covered = 0
    missed = []
    for decision in idea.required_decisions:
        matched = any(
            decision.matches_any(_question_text(q))
            for q in questions
        )
        if matched:
            covered += 1
        else:
            missed.append(decision.decision_id)

    return covered / len(idea.required_decisions), missed


def compute_forbidden_decision_rate(
    questions: Iterable[Any],
    idea: GoldenIdea,
) -> tuple[float, list[dict]]:
    """Fraction of forbidden_decisions where ≥1 question matches its pattern.

    Returns (forbidden_hit_rate, hits) where hits is list of {decision_id, question_text}.
    """
    questions = list(questions)
    if not idea.forbidden_decisions:
        return 0.0, []

    hits = []
    triggered = set()
    for decision in idea.forbidden_decisions:
        for q in questions:
            text = _question_text(q)
            if decision.matches_any(text):
                triggered.add(decision.decision_id)
                hits.append({"decision_id": decision.decision_id, "question_text": text})
                break  # one hit is enough to mark this forbidden as triggered

    return len(triggered) / len(idea.forbidden_decisions), hits


def compute_duplicate_pair_count(questions: Iterable[Any]) -> tuple[int, list[tuple[str, str]]]:
    """Count pairs of questions sharing the same source_decision_id.

    Without embeddings, this is the cheap proxy for "two questions about same decision".
    Real semantic duplicate detection is M5 (echo detection) territory.

    Returns (pair_count, list_of_(q_id_a, q_id_b)).
    """
    questions = list(questions)
    pairs = []
    seen: dict[str, list] = {}
    for q in questions:
        decision_id = _question_source_decision(q)
        if not decision_id:
            continue
        seen.setdefault(decision_id, []).append(q)
    for decision_id, qs in seen.items():
        if len(qs) < 2:
            continue
        for i in range(len(qs)):
            for j in range(i + 1, len(qs)):
                a = qs[i].get("id") if isinstance(qs[i], dict) else getattr(qs[i], "id", "?")
                b = qs[j].get("id") if isinstance(qs[j], dict) else getattr(qs[j], "id", "?")
                pairs.append((a, b))
    return len(pairs), pairs


def compute_domain_balance_entropy(questions: Iterable[Any]) -> float:
    """Shannon entropy (nats) over the distribution of question.domain values.

    Higher = more balanced. 0 = all questions in one domain.
    """
    counts = Counter(_question_domain(q) for q in questions)
    total = sum(counts.values())
    if total == 0:
        return 0.0
    entropy = 0.0
    for c in counts.values():
        p = c / total
        if p > 0:
            entropy -= p * math.log(p)
    return entropy


def compute_avg_question_length(questions: Iterable[Any]) -> float:
    """Mean character count of question.text across all questions."""
    questions = list(questions)
    if not questions:
        return 0.0
    return sum(len(_question_text(q)) for q in questions) / len(questions)


_BINARY_RATIO_SYSTEM = """\
You are a question quality judge for software project discovery questions.
For each question, classify whether it is "BINARY" (can be answered with a
simple yes/no without elaboration or trade-off discussion) or "OPEN" (requires
explanation, comparison of options, or context-specific reasoning).

Return ONLY valid JSON: {"ratings": [{"id": "...", "binary": true|false}, ...]}
Do not include any other text."""


_SCOPE_DRIFT_SYSTEM = """\
You are a scope drift detector for project question review.
Given a list of project scope items and a set of questions, classify each
question as "IN_SCOPE" (relevant to at least one scope_in item) or "DRIFT"
(not obviously related to any scope_in item).

Return ONLY valid JSON: {"ratings": [{"id": "...", "in_scope": true|false}, ...]}
Do not include any other text."""


def compute_binary_yes_no_ratio(
    questions: list[Any],
    llm_client: EvalLLMClient | None = None,
) -> tuple[float, list[str]]:
    """Fraction of questions LLM-rates as binary yes/no.

    Stub mode (llm_client=None): returns (0.5, []) — neutral signal.
    Real mode: single batch LLM call over all questions.

    Returns (ratio, [question_texts_rated_binary]).
    """
    if not questions or llm_client is None:
        return 0.5, []

    batch = [
        {"id": (q.get("id") if isinstance(q, dict) else getattr(q, "id", str(i))),
         "text": _question_text(q)}
        for i, q in enumerate(questions)
    ]
    user_msg = "Questions:\n" + "\n".join(
        f'{i+1}. (id={b["id"]}) {b["text"]}' for i, b in enumerate(batch)
    )
    try:
        raw = llm_client.complete(_BINARY_RATIO_SYSTEM, user_msg)
        data = json.loads(raw)
        ratings = {r["id"]: r.get("binary", False) for r in data.get("ratings", [])}
    except Exception:
        return 0.5, []

    binary_texts = [
        b["text"] for b in batch if ratings.get(b["id"], False)
    ]
    ratio = len(binary_texts) / len(batch) if batch else 0.0
    return ratio, binary_texts


def compute_scope_drift_count(
    questions: list[Any],
    scope_in: list[str],
    llm_client: EvalLLMClient | None = None,
) -> tuple[int, list[str]]:
    """Count of questions LLM-rates as unrelated to brief.scope_in.

    Stub mode (llm_client=None): returns (0, []) — neutral signal.
    Real mode: single batch LLM call.

    Returns (drift_count, [question_texts_rated_drift]).
    """
    if not questions or llm_client is None:
        return 0, []

    batch = [
        {"id": (q.get("id") if isinstance(q, dict) else getattr(q, "id", str(i))),
         "text": _question_text(q)}
        for i, q in enumerate(questions)
    ]
    scope_str = "\n".join(f"- {s}" for s in scope_in) if scope_in else "(not specified)"
    user_msg = (
        f"scope_in:\n{scope_str}\n\nQuestions:\n"
        + "\n".join(f'{i+1}. (id={b["id"]}) {b["text"]}' for i, b in enumerate(batch))
    )
    try:
        raw = llm_client.complete(_SCOPE_DRIFT_SYSTEM, user_msg)
        data = json.loads(raw)
        ratings = {r["id"]: r.get("in_scope", True) for r in data.get("ratings", [])}
    except Exception:
        return 0, []

    drift_texts = [
        b["text"] for b in batch if not ratings.get(b["id"], True)
    ]
    return len(drift_texts), drift_texts


def compute_classification_distribution(questions: Iterable[Any]) -> dict[str, float]:
    """Fraction of questions per classification (REQUIRED / STRATEGIC / OPTIONAL)."""
    counts = Counter(_question_classification(q) for q in questions)
    total = sum(counts.values())
    if total == 0:
        return {}
    return {k: v / total for k, v in counts.items()}


def compute_question_metrics(
    questions: Iterable[Any],
    idea: GoldenIdea,
    llm_client: EvalLLMClient | None = None,
) -> QuestionMetricsReport:
    """Run all question metrics (Q1-Q7), return report.

    Q1-Q5 are rule-based and always computed.
    Q6 (binary_yes_no_ratio) and Q7 (scope_drift_count) require an LLM client.
    When llm_client=None (stub mode), Q6 returns 0.5 and Q7 returns 0.
    """
    questions = list(questions)

    coverage, missed = compute_required_decision_coverage(questions, idea)
    forbidden_rate, forbidden_hits = compute_forbidden_decision_rate(questions, idea)
    dup_count, dup_pairs = compute_duplicate_pair_count(questions)
    entropy = compute_domain_balance_entropy(questions)
    avg_len = compute_avg_question_length(questions)
    classif = compute_classification_distribution(questions)

    # Q6: binary yes/no ratio (stub returns neutral 0.5)
    binary_ratio, binary_q_texts = compute_binary_yes_no_ratio(questions, llm_client)

    # Q7: scope drift (stub returns 0)
    scope_in = idea.intake_script.get("scope_in", []) if idea.intake_script else []
    if isinstance(scope_in, str):
        scope_in = [scope_in]
    drift_count, drift_q_texts = compute_scope_drift_count(questions, scope_in, llm_client)

    llm_mode = "real" if llm_client is not None else "stub"

    return QuestionMetricsReport(
        required_decision_coverage=coverage,
        forbidden_decision_rate=forbidden_rate,
        duplicate_pair_count=dup_count,
        domain_balance_entropy=entropy,
        avg_question_length=avg_len,
        classification_distribution=classif,
        binary_yes_no_ratio=binary_ratio,
        scope_drift_count=drift_count,
        llm_metrics_mode=llm_mode,

        pass_required_coverage=coverage >= THRESHOLDS["required_decision_coverage"],
        pass_forbidden_rate=forbidden_rate <= THRESHOLDS["forbidden_decision_rate_max"],
        pass_duplicate=dup_count <= THRESHOLDS["duplicate_pair_count_max"],
        pass_domain_entropy=entropy >= THRESHOLDS["domain_balance_entropy_min"],
        pass_avg_length=(
            THRESHOLDS["avg_question_length_min"]
            <= avg_len
            <= THRESHOLDS["avg_question_length_max"]
        ),
        # Stub mode → neutral pass (LLM not called, no signal to penalise)
        pass_binary_ratio=(
            True if llm_client is None
            else binary_ratio <= THRESHOLDS["binary_yes_no_ratio_max"]
        ),
        pass_scope_drift=(
            True if llm_client is None
            else drift_count <= THRESHOLDS["scope_drift_count_max"]
        ),

        missed_required=missed,
        forbidden_hits=forbidden_hits,
        duplicate_pairs=dup_pairs,
        binary_yes_no_questions=binary_q_texts,
        scope_drift_questions=drift_q_texts,
        question_count=len(questions),
    )
