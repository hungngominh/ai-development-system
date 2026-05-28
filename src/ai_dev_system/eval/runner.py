"""Eval runner — brief layer (M2.16 scope).

Goal of this slice: produce a deterministic `.eval_runs/<tag>/` directory that
captures brief metrics for every golden idea, so `ai-dev eval compare` can
prove (or disprove) that intake brief v2 actually improves over a baseline.

Stub-mode only here — we do NOT call the live LLM. The runner replays each
golden idea's `intake_script` straight into a synthetic IntakeState, calls
`to_brief_v2` to materialize the brief, then computes brief metrics. Question/
debate layers are out of scope for M2.16 — they have their own eval slices in
the spec.

Output layout (matches the eval design spec):
    .eval_runs/<tag>/
    ├── meta.json
    ├── per_idea/<idea_id>/
    │   ├── brief.json
    │   └── metrics.json
    └── aggregate.json
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from ai_dev_system.eval.golden_loader import GoldenIdea, load_all_ideas, load_idea
from ai_dev_system.eval.metrics.brief_metrics import (
    BriefMetricsReport,
    compute_brief_metrics,
)
from ai_dev_system.intake.engine import FieldAnswer, IntakeState, to_brief_v2
from ai_dev_system.intake.template import Template, load_template


DEFAULT_EVAL_ROOT = Path(".eval_runs")


@dataclass
class PerIdeaResult:
    idea_id: str
    brief: dict
    metrics: BriefMetricsReport


@dataclass
class EvalRunReport:
    tag: str
    timestamp: str
    mode: str
    template_id: str
    idea_count: int
    aggregate: dict
    per_idea: list[PerIdeaResult] = field(default_factory=list)


def _answer_from_script(raw_value) -> FieldAnswer:
    """Convert a YAML intake_script entry into a `FieldAnswer`.

    Conventions (matches the eval golden-set spec):
      - `null`     → user explicitly skipped
      - `"?"`      → user said "không biết" → AI suggested + confirmed (stub
                     supplies a placeholder value so the field is filled)
      - anything else → typed by user (source=user)
    """
    if raw_value is None:
        return FieldAnswer(value=None, source="skipped")
    if isinstance(raw_value, str) and raw_value.strip() == "?":
        return FieldAnswer(
            value="(stub AI suggestion)",
            source="ai_suggested_confirmed",
            rationale="stub-mode: scripted '?' → simulated AI confirm",
        )
    return FieldAnswer(value=raw_value, source="user")


def _state_from_idea(idea: GoldenIdea, template: Template) -> IntakeState:
    """Build a DONE IntakeState directly from the idea's intake_script.

    We deliberately bypass the engine state machine: the script already encodes
    final answers. Skipped fields stay absent from `answers` (matches
    `to_brief_v2` behavior — those render as `source=skipped` in the brief).
    """
    script = idea.intake_script or {}
    answers: dict[str, FieldAnswer] = {}
    for f in template.fields:
        if f.id not in script:
            continue  # absent → treated as skipped by to_brief_v2
        answers[f.id] = _answer_from_script(script[f.id])

    return IntakeState(
        template_id=template.id,
        schema_hash=template.schema_hash,
        run_id=f"eval-{idea.id}",
        project_id=idea.id,
        stage="DONE",
        field_idx=len(template.fields),
        answers=answers,
        audit=[],
    )


def _aggregate(per_idea: list[PerIdeaResult]) -> dict:
    """Mean each numeric metric across ideas + pass-count breakdown.

    Pass counts come straight from `BriefMetricsReport.pass_*` booleans, so the
    consumer can see "5/5 ideas passed critical_fill_rate" at a glance.
    """
    if not per_idea:
        return {"idea_count": 0, "metrics": {}, "passes": {}}

    numeric_keys = (
        "critical_fill_rate",
        "ai_suggest_acceptance",
        "assumption_count",
        "consistency_violations",
        "field_coverage_per_section",
        "followup_question_count",
    )
    pass_keys = (
        "pass_critical_fill", "pass_ai_suggest", "pass_assumption",
        "pass_consistency", "pass_field_coverage", "pass_followup",
    )

    n = len(per_idea)
    metric_means: dict[str, float] = {}
    for k in numeric_keys:
        metric_means[k] = sum(getattr(r.metrics, k) for r in per_idea) / n

    pass_counts: dict[str, int] = {}
    for k in pass_keys:
        pass_counts[k] = sum(1 for r in per_idea if getattr(r.metrics, k))

    overall_pass = sum(1 for r in per_idea if r.metrics.overall_pass())

    return {
        "idea_count": n,
        "metrics": metric_means,
        "passes": pass_counts,
        "overall_pass_count": overall_pass,
    }


def run_brief_eval(
    tag: str,
    *,
    idea_ids: Iterable[str] | None = None,
    output_root: Path | None = None,
    template_id: str = "generic_v1",
    mode: str = "stub",
    write: bool = True,
    golden_dir: Path | None = None,
) -> EvalRunReport:
    """Run brief-layer eval against the golden idea set.

    Args:
        tag:          subdirectory name under `output_root` (e.g. "pre-v2").
        idea_ids:     restrict to these idea IDs; default = every golden idea.
        output_root:  base dir for `.eval_runs/`; default = `./.eval_runs`.
        template_id:  intake template (only generic_v1 today).
        mode:         "stub" or "real" — recorded in meta.json; runner itself
                      is fully stub-mode in this slice.
        write:        when False, compute everything but don't touch the disk
                      (useful for unit tests).
        golden_dir:   override path to the golden dataset root (default: built-in).
    """
    output_root = Path(output_root) if output_root else DEFAULT_EVAL_ROOT
    template = load_template(template_id)

    ideas: list[GoldenIdea]
    if idea_ids is None:
        ideas = load_all_ideas(root=golden_dir)
    else:
        ideas = [load_idea(i, root=golden_dir) for i in idea_ids]

    per_idea: list[PerIdeaResult] = []
    for idea in ideas:
        state = _state_from_idea(idea, template)
        brief = to_brief_v2(state, template, source_hash=f"eval-{idea.id}")
        metrics = compute_brief_metrics(brief)
        per_idea.append(PerIdeaResult(idea_id=idea.id, brief=brief, metrics=metrics))

    aggregate = _aggregate(per_idea)

    report = EvalRunReport(
        tag=tag,
        timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        mode=mode,
        template_id=template_id,
        idea_count=len(per_idea),
        aggregate=aggregate,
        per_idea=per_idea,
    )

    if write:
        _persist(report, output_root)

    return report


def _persist(report: EvalRunReport, output_root: Path) -> None:
    run_dir = output_root / report.tag
    per_idea_dir = run_dir / "per_idea"
    per_idea_dir.mkdir(parents=True, exist_ok=True)

    meta = {
        "tag": report.tag,
        "timestamp": report.timestamp,
        "mode": report.mode,
        "template_id": report.template_id,
        "idea_count": report.idea_count,
    }
    (run_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    (run_dir / "aggregate.json").write_text(
        json.dumps(report.aggregate, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    for r in report.per_idea:
        idir = per_idea_dir / r.idea_id
        idir.mkdir(parents=True, exist_ok=True)
        (idir / "brief.json").write_text(
            json.dumps(r.brief, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        (idir / "metrics.json").write_text(
            json.dumps(r.metrics.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def list_tags(output_root: Path | None = None) -> list[str]:
    """Return alphabetically sorted list of run tags previously persisted."""
    output_root = Path(output_root) if output_root else DEFAULT_EVAL_ROOT
    if not output_root.exists():
        return []
    return sorted(
        p.name for p in output_root.iterdir()
        if p.is_dir() and (p / "aggregate.json").exists()
    )


def load_aggregate(tag: str, output_root: Path | None = None) -> dict:
    """Read aggregate.json for a tag. Raises FileNotFoundError if missing."""
    output_root = Path(output_root) if output_root else DEFAULT_EVAL_ROOT
    path = output_root / tag / "aggregate.json"
    if not path.exists():
        raise FileNotFoundError(f"No eval run tagged {tag!r} (looked at {path})")
    return json.loads(path.read_text(encoding="utf-8"))
