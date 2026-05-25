"""Debate engine configuration (M5.E, spec D7 'Configuration').

Single dataclass so callers can override individual knobs without
touching defaults. Engine + rounds modules accept `config: DebateConfig
| None = None` and fall back to `DebateConfig()` when None — keeps
the existing v1 call sites source-compatible.
"""

from dataclasses import dataclass

from ai_dev_system.debate.diversity import ECHO_SIMILARITY_THRESHOLD
from ai_dev_system.debate.moderator import MAX_MODERATOR_RETRIES


@dataclass(frozen=True)
class DebateConfig:
    max_rounds: int = 5
    confidence_threshold: float = 0.8

    # M5.E (spec D7) — confidence calibration
    required_min_rounds: int = 2
    use_calibrated_moderator: bool = True

    # M5.C — moderator retry budget
    max_moderator_retries: int = MAX_MODERATOR_RETRIES

    # M5.D — diversity guardrails
    echo_similarity_threshold: float = ECHO_SIMILARITY_THRESHOLD
    diversity_confidence_penalty: float = 0.7
    inject_skeptic_on_echo: bool = True
