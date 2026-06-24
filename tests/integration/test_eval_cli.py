"""Integration tests for `ai-dev eval run/compare/list/show` (M2.16)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import typer


def test_eval_run_creates_aggregate_in_custom_output_dir(tmp_path):
    from ai_dev_system.cli.commands.eval import eval_run

    with pytest.raises(typer.Exit) as exc:
        eval_run(
            tag="cli-run", idea=None, output_dir=str(tmp_path),
            mode="stub", json_output=True, quiet=True,
        )
    assert exc.value.exit_code == 0

    assert (tmp_path / "cli-run" / "aggregate.json").exists()
    assert (tmp_path / "cli-run" / "meta.json").exists()


def test_eval_run_restricts_to_one_idea(tmp_path):
    from ai_dev_system.cli.commands.eval import eval_run

    with pytest.raises(typer.Exit) as exc:
        eval_run(
            tag="solo", idea=["01_internal_forum"], output_dir=str(tmp_path),
            mode="stub", json_output=True, quiet=True,
        )
    assert exc.value.exit_code == 0

    per_idea = list((tmp_path / "solo" / "per_idea").iterdir())
    assert [p.name for p in per_idea] == ["01_internal_forum"]


def test_eval_list_returns_persisted_tags(tmp_path):
    from ai_dev_system.cli.commands.eval import eval_list, eval_run

    for t in ("first", "second"):
        with pytest.raises(typer.Exit):
            eval_run(tag=t, idea=None, output_dir=str(tmp_path),
                     mode="stub", json_output=True, quiet=True)

    with pytest.raises(typer.Exit) as exc:
        eval_list(output_dir=str(tmp_path), json_output=True)
    assert exc.value.exit_code == 0


def test_eval_show_missing_tag_exits_2(tmp_path):
    from ai_dev_system.cli.commands.eval import eval_show

    with pytest.raises(typer.Exit) as exc:
        eval_show(tag="ghost", output_dir=str(tmp_path), json_output=True)
    assert exc.value.exit_code == 2


def _write_aggregate(root: Path, tag: str, metrics: dict, overall=0):
    d = root / tag
    d.mkdir(parents=True, exist_ok=True)
    (d / "aggregate.json").write_text(
        json.dumps({
            "idea_count": 1, "metrics": metrics, "passes": {},
            "overall_pass_count": overall,
        }),
        encoding="utf-8",
    )


def test_eval_compare_exits_0_when_no_regression(tmp_path):
    from ai_dev_system.cli.commands.eval import eval_compare

    base = {
        "critical_fill_rate": 0.9, "ai_suggest_acceptance": 1.0,
        "assumption_count": 1, "consistency_violations": 0,
        "field_coverage_per_section": 0.8, "followup_question_count": 2,
    }
    _write_aggregate(tmp_path, "A", base)
    _write_aggregate(tmp_path, "B", {**base, "critical_fill_rate": 0.92})

    with pytest.raises(typer.Exit) as exc:
        eval_compare(tag_a="A", tag_b="B", output_dir=str(tmp_path),
                     json_output=True, write_markdown=False)
    assert exc.value.exit_code == 0


def test_eval_compare_exits_1_on_regression_and_writes_markdown(tmp_path):
    from ai_dev_system.cli.commands.eval import eval_compare

    base = {
        "critical_fill_rate": 0.9, "ai_suggest_acceptance": 1.0,
        "assumption_count": 1, "consistency_violations": 0,
        "field_coverage_per_section": 0.8, "followup_question_count": 2,
    }
    _write_aggregate(tmp_path, "A", base)
    _write_aggregate(tmp_path, "B", {**base, "critical_fill_rate": 0.5})

    with pytest.raises(typer.Exit) as exc:
        eval_compare(tag_a="A", tag_b="B", output_dir=str(tmp_path),
                     json_output=False, write_markdown=True)
    assert exc.value.exit_code == 1

    md_path = tmp_path / "B" / "compare_against_A.md"
    assert md_path.exists()
    body = md_path.read_text(encoding="utf-8")
    assert "regression" in body.lower()


def test_eval_compare_exits_2_for_missing_baseline(tmp_path):
    from ai_dev_system.cli.commands.eval import eval_compare

    base = {
        "critical_fill_rate": 0.9, "ai_suggest_acceptance": 1.0,
        "assumption_count": 1, "consistency_violations": 0,
        "field_coverage_per_section": 0.8, "followup_question_count": 2,
    }
    _write_aggregate(tmp_path, "only_b", base)

    with pytest.raises(typer.Exit) as exc:
        eval_compare(tag_a="missing", tag_b="only_b", output_dir=str(tmp_path),
                     json_output=True, write_markdown=False)
    assert exc.value.exit_code == 2
