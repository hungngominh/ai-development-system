"""Eval metrics modules.

3 layers (per spec 2026-05-23-evaluation-harness-design.md):
- brief_metrics: 6 metrics on intake brief output
- question_metrics: 8 metrics on generated questions (5 rule-based + 3 LLM-based)
- debate_metrics: 4 metrics on debate output (deferred to M3/M10)
"""
from ai_dev_system.eval.metrics.brief_metrics import (
    BriefMetricsReport,
    compute_brief_metrics,
)

__all__ = ["BriefMetricsReport", "compute_brief_metrics"]
