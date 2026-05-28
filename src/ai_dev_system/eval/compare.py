"""Diff two eval runs (M2.16).

Given two tags A and B (typically baseline + branch), load each aggregate.json,
diff every metric, and flag regressions. A "regression" is direction-aware:

- higher_is_better metrics regress when B < A by more than the threshold
- lower_is_better metrics regress when B > A by more than the threshold

Output is structured (CompareReport) + a human-readable markdown summary. The
CLI writes the markdown to `.eval_runs/<B>/compare_against_<A>.md` and prints
the same to stderr.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from ai_dev_system.eval.runner import load_aggregate

Direction = Literal["higher_is_better", "lower_is_better"]

# Metric direction map. Threshold = "absolute change beyond which a delta is
# considered a regression". Tuned to be loose enough to ignore noise but
# tight enough to catch real drift (~10% of the metric's plausible range).
METRIC_DIRECTIONS: dict[str, Direction] = {
    "critical_fill_rate": "higher_is_better",
    "ai_suggest_acceptance": "higher_is_better",
    "assumption_count": "lower_is_better",
    "consistency_violations": "lower_is_better",
    "field_coverage_per_section": "higher_is_better",
    "followup_question_count": "lower_is_better",
}

REGRESSION_THRESHOLDS: dict[str, float] = {
    "critical_fill_rate": 0.05,             # 5 percentage points
    "ai_suggest_acceptance": 0.05,
    "assumption_count": 1.0,                # absolute count
    "consistency_violations": 1.0,
    "field_coverage_per_section": 0.05,
    "followup_question_count": 2.0,
}


@dataclass
class MetricDelta:
    name: str
    a: float
    b: float
    delta: float            # b - a
    direction: Direction
    regression: bool        # True ⇔ b moved away from "better" by > threshold
    improvement: bool       # True ⇔ b moved toward "better" by > threshold


@dataclass
class CompareReport:
    tag_a: str
    tag_b: str
    metrics: list[MetricDelta] = field(default_factory=list)
    pass_counts_a: dict[str, int] = field(default_factory=dict)
    pass_counts_b: dict[str, int] = field(default_factory=dict)
    overall_pass_a: int = 0
    overall_pass_b: int = 0
    regression_count: int = 0
    improvement_count: int = 0

    def has_regression(self) -> bool:
        return self.regression_count > 0

    def to_markdown(self) -> str:
        lines: list[str] = []
        lines.append(f"# Eval Compare: `{self.tag_a}` → `{self.tag_b}`")
        lines.append("")
        lines.append(
            f"Overall pass: {self.overall_pass_a} → {self.overall_pass_b}  "
            f"({'+' if self.overall_pass_b >= self.overall_pass_a else ''}"
            f"{self.overall_pass_b - self.overall_pass_a})"
        )
        lines.append("")
        lines.append("| Metric | A | B | Δ | Direction | Verdict |")
        lines.append("|---|---:|---:|---:|---|---|")
        for m in self.metrics:
            verdict = "—"
            if m.regression:
                verdict = "✗ regression"
            elif m.improvement:
                verdict = "✓ improvement"
            sign = "+" if m.delta >= 0 else ""
            lines.append(
                f"| `{m.name}` | {m.a:.3f} | {m.b:.3f} | {sign}{m.delta:.3f} "
                f"| {m.direction.replace('_', ' ')} | {verdict} |"
            )
        lines.append("")
        if self.regression_count:
            lines.append(f"**{self.regression_count} regression(s) flagged.**")
        elif self.improvement_count:
            lines.append(f"**{self.improvement_count} improvement(s); no regressions.**")
        else:
            lines.append("**No significant deltas.**")
        return "\n".join(lines) + "\n"


def compare_runs(
    tag_a: str, tag_b: str, *, output_root: Path | None = None,
) -> CompareReport:
    """Diff two persisted eval runs. Raises FileNotFoundError if either tag
    has no aggregate.json on disk."""
    agg_a = load_aggregate(tag_a, output_root)
    agg_b = load_aggregate(tag_b, output_root)

    metrics_a = agg_a.get("metrics", {}) or {}
    metrics_b = agg_b.get("metrics", {}) or {}

    deltas: list[MetricDelta] = []
    regressions = 0
    improvements = 0
    for name, direction in METRIC_DIRECTIONS.items():
        a = float(metrics_a.get(name, 0.0))
        b = float(metrics_b.get(name, 0.0))
        delta = b - a
        threshold = REGRESSION_THRESHOLDS.get(name, 0.0)

        if direction == "higher_is_better":
            is_reg = delta < -threshold
            is_imp = delta > threshold
        else:  # lower_is_better
            is_reg = delta > threshold
            is_imp = delta < -threshold

        if is_reg:
            regressions += 1
        if is_imp:
            improvements += 1

        deltas.append(MetricDelta(
            name=name, a=a, b=b, delta=delta, direction=direction,
            regression=is_reg, improvement=is_imp,
        ))

    return CompareReport(
        tag_a=tag_a,
        tag_b=tag_b,
        metrics=deltas,
        pass_counts_a=agg_a.get("passes", {}) or {},
        pass_counts_b=agg_b.get("passes", {}) or {},
        overall_pass_a=int(agg_a.get("overall_pass_count", 0) or 0),
        overall_pass_b=int(agg_b.get("overall_pass_count", 0) or 0),
        regression_count=regressions,
        improvement_count=improvements,
    )
