"""Unit tests for eval/runner.py + eval/compare.py (M2.16)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_dev_system.eval.compare import compare_runs
from ai_dev_system.eval.runner import (
    list_tags,
    load_aggregate,
    run_brief_eval,
)


def test_run_brief_eval_persists_expected_layout(tmp_path):
    """Output dir contains meta.json + aggregate.json + per_idea/<id>/{brief,metrics}.json."""
    report = run_brief_eval(tag="t1", output_root=tmp_path)

    run_dir = tmp_path / "t1"
    assert (run_dir / "meta.json").exists()
    assert (run_dir / "aggregate.json").exists()
    assert (run_dir / "per_idea").exists()

    assert report.idea_count == len(list((run_dir / "per_idea").iterdir()))
    for r in report.per_idea:
        idir = run_dir / "per_idea" / r.idea_id
        assert (idir / "brief.json").exists()
        assert (idir / "metrics.json").exists()
        brief = json.loads((idir / "brief.json").read_text(encoding="utf-8"))
        assert brief["brief_version"] == 2


def test_run_brief_eval_aggregate_has_expected_keys(tmp_path):
    report = run_brief_eval(tag="t-agg", output_root=tmp_path)
    agg = report.aggregate
    assert "metrics" in agg
    assert "passes" in agg
    assert "overall_pass_count" in agg
    assert agg["idea_count"] == report.idea_count
    # Every brief metric appears in the aggregate means.
    for k in (
        "critical_fill_rate", "ai_suggest_acceptance", "assumption_count",
        "consistency_violations", "field_coverage_per_section",
        "followup_question_count",
    ):
        assert k in agg["metrics"]


def test_run_brief_eval_write_false_skips_disk(tmp_path):
    report = run_brief_eval(tag="ghost", output_root=tmp_path, write=False)
    assert report.idea_count >= 1
    assert not (tmp_path / "ghost").exists()


def test_run_brief_eval_restricts_to_requested_ideas(tmp_path):
    report = run_brief_eval(tag="restrict", output_root=tmp_path,
                            idea_ids=["01_internal_forum"])
    assert report.idea_count == 1
    assert report.per_idea[0].idea_id == "01_internal_forum"


def test_run_brief_eval_deterministic_for_same_inputs(tmp_path):
    a = run_brief_eval(tag="run-a", output_root=tmp_path, write=False)
    b = run_brief_eval(tag="run-b", output_root=tmp_path, write=False)
    # Tag/timestamp differ; metric aggregates are identical.
    assert a.aggregate["metrics"] == b.aggregate["metrics"]
    assert a.aggregate["passes"] == b.aggregate["passes"]


def test_list_tags_returns_persisted_runs(tmp_path):
    run_brief_eval(tag="alpha", output_root=tmp_path)
    run_brief_eval(tag="beta", output_root=tmp_path)
    assert list_tags(tmp_path) == ["alpha", "beta"]


def test_list_tags_skips_dirs_without_aggregate(tmp_path):
    (tmp_path / "incomplete").mkdir()  # no aggregate.json inside
    run_brief_eval(tag="good", output_root=tmp_path)
    assert list_tags(tmp_path) == ["good"]


def test_load_aggregate_raises_when_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_aggregate("nope", output_root=tmp_path)


def _write_aggregate(root: Path, tag: str, *, metrics: dict, passes=None,
                     overall_pass_count=0, idea_count=1):
    d = root / tag
    d.mkdir(parents=True, exist_ok=True)
    (d / "aggregate.json").write_text(json.dumps({
        "idea_count": idea_count,
        "metrics": metrics,
        "passes": passes or {},
        "overall_pass_count": overall_pass_count,
    }), encoding="utf-8")


def test_compare_detects_regression_in_higher_is_better_metric(tmp_path):
    _write_aggregate(tmp_path, "A", metrics={
        "critical_fill_rate": 0.9, "ai_suggest_acceptance": 1.0,
        "assumption_count": 1, "consistency_violations": 0,
        "field_coverage_per_section": 0.8, "followup_question_count": 2,
    })
    _write_aggregate(tmp_path, "B", metrics={
        # critical_fill_rate dropped by 0.20 → above the 0.05 threshold → regression
        "critical_fill_rate": 0.7, "ai_suggest_acceptance": 1.0,
        "assumption_count": 1, "consistency_violations": 0,
        "field_coverage_per_section": 0.8, "followup_question_count": 2,
    })
    report = compare_runs("A", "B", output_root=tmp_path)
    assert report.has_regression()
    by_name = {m.name: m for m in report.metrics}
    assert by_name["critical_fill_rate"].regression is True
    assert by_name["ai_suggest_acceptance"].regression is False


def test_compare_detects_regression_in_lower_is_better_metric(tmp_path):
    _write_aggregate(tmp_path, "A", metrics={
        "critical_fill_rate": 0.9, "ai_suggest_acceptance": 1.0,
        "assumption_count": 1, "consistency_violations": 0,
        "field_coverage_per_section": 0.8, "followup_question_count": 2,
    })
    _write_aggregate(tmp_path, "B", metrics={
        "critical_fill_rate": 0.9, "ai_suggest_acceptance": 1.0,
        # assumption_count rose by 3 → above the threshold (1.0) → regression
        "assumption_count": 4, "consistency_violations": 0,
        "field_coverage_per_section": 0.8, "followup_question_count": 2,
    })
    report = compare_runs("A", "B", output_root=tmp_path)
    assert report.has_regression()
    by_name = {m.name: m for m in report.metrics}
    assert by_name["assumption_count"].regression is True


def test_compare_detects_improvement(tmp_path):
    _write_aggregate(tmp_path, "before", metrics={
        "critical_fill_rate": 0.5, "ai_suggest_acceptance": 0.5,
        "assumption_count": 5, "consistency_violations": 2,
        "field_coverage_per_section": 0.4, "followup_question_count": 8,
    })
    _write_aggregate(tmp_path, "after", metrics={
        # All metrics moved decisively in the "better" direction.
        "critical_fill_rate": 0.95, "ai_suggest_acceptance": 0.9,
        "assumption_count": 1, "consistency_violations": 0,
        "field_coverage_per_section": 0.8, "followup_question_count": 2,
    })
    report = compare_runs("before", "after", output_root=tmp_path)
    assert not report.has_regression()
    assert report.improvement_count >= 5  # all 6 should improve (followup borderline)


def test_compare_treats_small_changes_as_noise(tmp_path):
    """Changes inside the threshold are neither regression nor improvement."""
    _write_aggregate(tmp_path, "x", metrics={
        "critical_fill_rate": 0.80, "ai_suggest_acceptance": 0.80,
        "assumption_count": 3, "consistency_violations": 0,
        "field_coverage_per_section": 0.60, "followup_question_count": 4,
    })
    _write_aggregate(tmp_path, "y", metrics={
        "critical_fill_rate": 0.82, "ai_suggest_acceptance": 0.78,
        "assumption_count": 3, "consistency_violations": 0,
        "field_coverage_per_section": 0.62, "followup_question_count": 5,
    })
    report = compare_runs("x", "y", output_root=tmp_path)
    assert report.regression_count == 0
    assert report.improvement_count == 0


def test_compare_to_markdown_renders_all_metrics(tmp_path):
    _write_aggregate(tmp_path, "m1", metrics={
        "critical_fill_rate": 0.9, "ai_suggest_acceptance": 1.0,
        "assumption_count": 1, "consistency_violations": 0,
        "field_coverage_per_section": 0.8, "followup_question_count": 2,
    })
    _write_aggregate(tmp_path, "m2", metrics={
        "critical_fill_rate": 0.95, "ai_suggest_acceptance": 1.0,
        "assumption_count": 1, "consistency_violations": 0,
        "field_coverage_per_section": 0.8, "followup_question_count": 2,
    })
    md = compare_runs("m1", "m2", output_root=tmp_path).to_markdown()
    for k in (
        "critical_fill_rate", "ai_suggest_acceptance", "assumption_count",
        "consistency_violations", "field_coverage_per_section", "followup_question_count",
    ):
        assert f"`{k}`" in md
    assert "Eval Compare" in md
    assert "`m1` → `m2`" in md


def test_compare_missing_tag_raises_file_not_found(tmp_path):
    _write_aggregate(tmp_path, "only_a", metrics={
        "critical_fill_rate": 0.9, "ai_suggest_acceptance": 1.0,
        "assumption_count": 1, "consistency_violations": 0,
        "field_coverage_per_section": 0.8, "followup_question_count": 2,
    })
    with pytest.raises(FileNotFoundError):
        compare_runs("only_a", "missing", output_root=tmp_path)
