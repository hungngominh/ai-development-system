"""Tests for feature flag linear-order enforcement (locked decision #18)."""
from __future__ import annotations

import pytest

from ai_dev_system.feature_flags import (
    FLAG_ORDER,
    FeatureFlagOrderError,
    FeatureFlags,
    parse_feature_overrides,
)


class TestLinearOrder:
    def test_all_disabled_is_valid(self):
        flags = FeatureFlags()
        assert flags.active_flags() == []

    def test_only_first_enabled(self):
        flags = FeatureFlags(eval_harness_enabled=True)
        assert flags.is_enabled("eval_harness_enabled")
        assert flags.active_flags() == ["eval_harness_enabled"]

    def test_first_two_enabled_valid(self):
        flags = FeatureFlags(eval_harness_enabled=True, use_intake_wizard=True)
        assert flags.active_flags() == ["eval_harness_enabled", "use_intake_wizard"]

    def test_all_six_enabled_valid(self):
        flags = FeatureFlags(
            eval_harness_enabled=True,
            use_intake_wizard=True,
            use_question_pipeline_v2=True,
            use_debate_v2=True,
            use_gate1_v2=True,
            use_spec_gen_v2=True,
        )
        assert len(flags.active_flags()) == 6

    def test_skip_first_raises(self):
        """Enabling flag 2 without flag 1 must fail."""
        with pytest.raises(FeatureFlagOrderError, match="use_intake_wizard.*eval_harness_enabled"):
            FeatureFlags(use_intake_wizard=True)

    def test_skip_middle_raises(self):
        """Enabling flag 4 without flag 3 must fail."""
        with pytest.raises(FeatureFlagOrderError):
            FeatureFlags(
                eval_harness_enabled=True,
                use_intake_wizard=True,
                # use_question_pipeline_v2 missing!
                use_debate_v2=True,
            )

    def test_gap_at_end_raises(self):
        """Enabling last flag without preceding chain must fail."""
        with pytest.raises(FeatureFlagOrderError):
            FeatureFlags(
                eval_harness_enabled=True,
                use_intake_wizard=True,
                use_question_pipeline_v2=True,
                # use_debate_v2 missing
                # use_gate1_v2 missing
                use_spec_gen_v2=True,
            )


class TestFromEnv:
    def test_default_all_false(self, monkeypatch):
        for name in FLAG_ORDER:
            monkeypatch.delenv(f"FF_{name.upper()}", raising=False)
        flags = FeatureFlags.from_env()
        assert flags.active_flags() == []

    def test_env_truthy_values(self, monkeypatch):
        for name in FLAG_ORDER:
            monkeypatch.delenv(f"FF_{name.upper()}", raising=False)
        monkeypatch.setenv("FF_EVAL_HARNESS_ENABLED", "true")
        monkeypatch.setenv("FF_USE_INTAKE_WIZARD", "yes")
        flags = FeatureFlags.from_env()
        assert flags.is_enabled("eval_harness_enabled")
        assert flags.is_enabled("use_intake_wizard")

    def test_env_falsy_values(self, monkeypatch):
        for name in FLAG_ORDER:
            monkeypatch.delenv(f"FF_{name.upper()}", raising=False)
        monkeypatch.setenv("FF_EVAL_HARNESS_ENABLED", "false")
        monkeypatch.setenv("FF_USE_INTAKE_WIZARD", "0")
        flags = FeatureFlags.from_env()
        assert not flags.is_enabled("eval_harness_enabled")

    def test_overrides_take_precedence(self, monkeypatch):
        for name in FLAG_ORDER:
            monkeypatch.delenv(f"FF_{name.upper()}", raising=False)
        monkeypatch.setenv("FF_EVAL_HARNESS_ENABLED", "true")
        flags = FeatureFlags.from_env(overrides={"eval_harness_enabled": False})
        assert not flags.is_enabled("eval_harness_enabled")

    def test_env_invalid_order_raises(self, monkeypatch):
        for name in FLAG_ORDER:
            monkeypatch.delenv(f"FF_{name.upper()}", raising=False)
        # Enable flag 3 without flags 1 and 2
        monkeypatch.setenv("FF_USE_QUESTION_PIPELINE_V2", "true")
        with pytest.raises(FeatureFlagOrderError):
            FeatureFlags.from_env()


class TestSnapshot:
    def test_snapshot_includes_all_flags(self):
        flags = FeatureFlags(eval_harness_enabled=True, use_intake_wizard=True)
        snap = flags.snapshot()
        assert set(snap.keys()) == set(FLAG_ORDER)
        assert snap["eval_harness_enabled"] is True
        assert snap["use_intake_wizard"] is True
        assert snap["use_question_pipeline_v2"] is False


class TestIsEnabled:
    def test_known_flag(self):
        flags = FeatureFlags(eval_harness_enabled=True)
        assert flags.is_enabled("eval_harness_enabled") is True

    def test_unknown_flag_raises(self):
        flags = FeatureFlags()
        with pytest.raises(KeyError, match="Unknown feature flag"):
            flags.is_enabled("nonexistent")


class TestParseOverrides:
    def test_truthy(self):
        assert parse_feature_overrides(["eval_harness_enabled=true"]) == {
            "eval_harness_enabled": True
        }

    def test_falsy(self):
        assert parse_feature_overrides(["eval_harness_enabled=false"]) == {
            "eval_harness_enabled": False
        }

    def test_various_truthy_words(self):
        for val in ("1", "true", "yes", "on", "y"):
            assert parse_feature_overrides([f"eval_harness_enabled={val}"]) == {
                "eval_harness_enabled": True
            }

    def test_various_falsy_words(self):
        for val in ("0", "false", "no", "off", "n"):
            assert parse_feature_overrides([f"eval_harness_enabled={val}"]) == {
                "eval_harness_enabled": False
            }

    def test_multiple_pairs(self):
        out = parse_feature_overrides([
            "eval_harness_enabled=true",
            "use_intake_wizard=false",
        ])
        assert out == {"eval_harness_enabled": True, "use_intake_wizard": False}

    def test_missing_equals(self):
        with pytest.raises(ValueError, match="expected KEY=VALUE"):
            parse_feature_overrides(["eval_harness_enabled"])

    def test_unknown_flag(self):
        with pytest.raises(ValueError, match="Unknown feature flag"):
            parse_feature_overrides(["nonexistent=true"])

    def test_bad_value(self):
        with pytest.raises(ValueError, match="expected true/false"):
            parse_feature_overrides(["eval_harness_enabled=maybe"])

    def test_empty_list(self):
        assert parse_feature_overrides([]) == {}
