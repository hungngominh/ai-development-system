"""Feature flags for Phase 1 v2 rollout (decision #18 in locked-decisions).

Linear order enforcement: flag N may only be true if flag N-1 is true.
This reduces the test combo matrix from 2^6=64 to 7 valid states.

Order:
    1. eval_harness_enabled
    2. use_intake_wizard            (requires #1)
    3. use_question_pipeline_v2     (requires #2)
    4. use_debate_v2                (requires #3)
    5. use_gate1_v2                 (requires #4)
    6. use_spec_gen_v2              (requires #5)

Per Migration spec decision #35-Mig-2: flags are read once per pipeline
invocation, not per LLM call. The dispatcher snapshots flag state at entry.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Iterator


# Ordered list of flags — index = position in linear chain (0-indexed).
FLAG_ORDER: tuple[str, ...] = (
    "eval_harness_enabled",
    "use_intake_wizard",
    "use_question_pipeline_v2",
    "use_debate_v2",
    "use_gate1_v2",
    "use_spec_gen_v2",
)


class FeatureFlagOrderError(ValueError):
    """Raised when flag state violates linear order (decision #18)."""


@dataclass
class FeatureFlags:
    eval_harness_enabled: bool = False
    use_intake_wizard: bool = False
    use_question_pipeline_v2: bool = False
    use_debate_v2: bool = False
    use_gate1_v2: bool = False
    use_spec_gen_v2: bool = False

    # Per-run override map (decision #35-Mig-2 + per-run feature_overrides JSON)
    overrides: dict[str, bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._validate_linear_order()

    def _validate_linear_order(self) -> None:
        """Enforce: flag N true requires flag N-1 true. Raise if violated."""
        prev_enabled = True  # virtual "flag 0" always enabled
        for name in FLAG_ORDER:
            current = getattr(self, name)
            if current and not prev_enabled:
                # Find the offending predecessor
                idx = FLAG_ORDER.index(name)
                predecessor = FLAG_ORDER[idx - 1] if idx > 0 else "<none>"
                raise FeatureFlagOrderError(
                    f"Cannot enable '{name}' while '{predecessor}' is disabled. "
                    f"Flags must enable in order: {' → '.join(FLAG_ORDER)}"
                )
            prev_enabled = current

    @classmethod
    def from_env(cls, overrides: dict[str, bool] | None = None) -> "FeatureFlags":
        """Construct from environment variables FF_<NAME>=true/false.

        overrides take precedence (used for per-run --feature flag).
        """
        overrides = overrides or {}
        kwargs = {}
        for name in FLAG_ORDER:
            if name in overrides:
                kwargs[name] = bool(overrides[name])
            else:
                env_key = f"FF_{name.upper()}"
                raw = os.environ.get(env_key, "").strip().lower()
                kwargs[name] = raw in {"1", "true", "yes", "on"}
        kwargs["overrides"] = overrides
        return cls(**kwargs)

    def is_enabled(self, name: str) -> bool:
        """Query a flag by name. Raises if name unknown."""
        if name not in FLAG_ORDER:
            raise KeyError(f"Unknown feature flag: {name}. Known: {FLAG_ORDER}")
        return bool(getattr(self, name))

    def active_flags(self) -> list[str]:
        """Return list of currently enabled flag names, in order."""
        return [name for name in FLAG_ORDER if getattr(self, name)]

    def snapshot(self) -> dict[str, bool]:
        """Immutable dict of current state — for eval metadata (decision #19)."""
        return {name: getattr(self, name) for name in FLAG_ORDER}

    def __iter__(self) -> Iterator[tuple[str, bool]]:
        for name in FLAG_ORDER:
            yield name, getattr(self, name)


def parse_feature_overrides(raw_pairs: list[str]) -> dict[str, bool]:
    """Parse `--feature KEY=VAL` CLI args into override dict.

    Accepts: 'key=true', 'key=false', 'key=1', 'key=0', 'key=yes', 'key=no'.
    Raises ValueError on malformed input or unknown flag name.
    """
    truthy = {"1", "true", "yes", "on", "y"}
    falsy = {"0", "false", "no", "off", "n"}
    out: dict[str, bool] = {}
    for pair in raw_pairs:
        if "=" not in pair:
            raise ValueError(f"Bad --feature value '{pair}': expected KEY=VALUE")
        key, _, val = pair.partition("=")
        key = key.strip()
        val = val.strip().lower()
        if key not in FLAG_ORDER:
            raise ValueError(f"Unknown feature flag '{key}'. Known: {', '.join(FLAG_ORDER)}")
        if val in truthy:
            out[key] = True
        elif val in falsy:
            out[key] = False
        else:
            raise ValueError(f"Bad --feature value '{val}' for '{key}': expected true/false")
    return out
