"""Tests for DebateConfig (M5.E)."""

from ai_dev_system.debate.config import DebateConfig
from ai_dev_system.debate.diversity import ECHO_SIMILARITY_THRESHOLD
from ai_dev_system.debate.moderator import MAX_MODERATOR_RETRIES


def test_defaults_match_spec_d7():
    cfg = DebateConfig()
    assert cfg.max_rounds == 5
    assert cfg.confidence_threshold == 0.8
    assert cfg.required_min_rounds == 2
    assert cfg.use_calibrated_moderator is True
    assert cfg.max_moderator_retries == MAX_MODERATOR_RETRIES
    assert cfg.echo_similarity_threshold == ECHO_SIMILARITY_THRESHOLD
    assert cfg.diversity_confidence_penalty == 0.7
    assert cfg.inject_skeptic_on_echo is True


def test_frozen():
    cfg = DebateConfig()
    try:
        cfg.max_rounds = 10
    except Exception as e:
        assert "frozen" in str(e).lower() or "cannot assign" in str(e).lower()
    else:
        raise AssertionError("DebateConfig should be frozen")


def test_override_individual_knob():
    cfg = DebateConfig(required_min_rounds=3, use_calibrated_moderator=False)
    assert cfg.required_min_rounds == 3
    assert cfg.use_calibrated_moderator is False
    # other defaults preserved
    assert cfg.max_rounds == 5
    assert cfg.confidence_threshold == 0.8
