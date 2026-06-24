"""ai-dev eval — brief-layer evaluation harness (M2.16 scope).

Verbs:
- run      — replay every golden idea through brief metrics, persist `.eval_runs/<tag>/`
- compare  — diff two persisted runs, flag regressions
- list     — list persisted run tags
- show     — print aggregate.json for one tag

Question/debate layers (`--layer questions|debate`) are intentionally NOT
wired in this slice — they live in their own milestones (M4, M5 in the v2
implementation plan).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from ai_dev_system.cli.core.output import OutputRenderer
from ai_dev_system.cli.core.registry import command


@command(
    noun="eval",
    verb="run",
    help="Run brief-layer eval on golden ideas; persist `.eval_runs/<tag>/`",
    noun_help="Phase 1 evaluation harness",
)
def eval_run(
    tag: str = typer.Option(..., "--tag", help="Run tag (subdirectory name under .eval_runs/)"),
    idea: Optional[list[str]] = typer.Option(
        None, "--idea", help="Restrict to these idea IDs (repeatable). Default = all goldens.",
    ),
    output_dir: Optional[str] = typer.Option(
        None, "--output-dir", help="Override .eval_runs/ base path",
    ),
    mode: str = typer.Option("stub", "--mode", help="Recorded in meta.json (stub|real)"),
    json_output: bool = typer.Option(False, "--json"),
    quiet: bool = typer.Option(False, "--quiet"),
) -> None:
    from ai_dev_system.eval.runner import run_brief_eval

    out = OutputRenderer(mode="json" if json_output else "human", quiet=quiet)
    root = Path(output_dir) if output_dir else None
    try:
        report = run_brief_eval(
            tag=tag,
            idea_ids=idea if idea else None,
            output_root=root,
            mode=mode,
        )
        payload = {
            "status": "ok",
            "tag": report.tag,
            "idea_count": report.idea_count,
            "overall_pass": report.aggregate.get("overall_pass_count", 0),
            "output_dir": str((root or Path(".eval_runs")) / report.tag),
        }
        out.write(payload)
        raise typer.Exit(0)
    except typer.Exit:
        raise
    except Exception as exc:
        out.write_error(code=1, message=f"eval run failed: {exc}")
        raise typer.Exit(1)


@command(noun="eval", verb="compare", help="Diff two runs, flag regressions")
def eval_compare(
    tag_a: str = typer.Argument(..., help="Baseline tag"),
    tag_b: str = typer.Argument(..., help="Comparison tag"),
    output_dir: Optional[str] = typer.Option(None, "--output-dir"),
    json_output: bool = typer.Option(False, "--json"),
    write_markdown: bool = typer.Option(
        True, "--write-md/--no-write-md",
        help="Write a markdown report to .eval_runs/<tag_b>/compare_against_<tag_a>.md",
    ),
) -> None:
    from ai_dev_system.eval.compare import compare_runs

    out = OutputRenderer(mode="json" if json_output else "human")
    root = Path(output_dir) if output_dir else None

    try:
        report = compare_runs(tag_a, tag_b, output_root=root)

        if write_markdown:
            target = (root or Path(".eval_runs")) / tag_b / f"compare_against_{tag_a}.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(report.to_markdown(), encoding="utf-8")

        if json_output:
            out.write({
                "status": "ok",
                "tag_a": report.tag_a,
                "tag_b": report.tag_b,
                "regression_count": report.regression_count,
                "improvement_count": report.improvement_count,
                "metrics": [
                    {
                        "name": m.name, "a": m.a, "b": m.b, "delta": m.delta,
                        "direction": m.direction,
                        "regression": m.regression, "improvement": m.improvement,
                    }
                    for m in report.metrics
                ],
            })
        else:
            # Human mode: print the markdown report to stdout so it's pipeable.
            print(report.to_markdown())

        # Non-zero exit on regression so this command is safe in CI gates.
        raise typer.Exit(1 if report.has_regression() else 0)
    except typer.Exit:
        raise
    except FileNotFoundError as exc:
        out.write_error(code=2, message=str(exc))
        raise typer.Exit(2)
    except Exception as exc:
        out.write_error(code=1, message=f"eval compare failed: {exc}")
        raise typer.Exit(1)


@command(noun="eval", verb="list", help="List persisted eval run tags")
def eval_list(
    output_dir: Optional[str] = typer.Option(None, "--output-dir"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    from ai_dev_system.eval.runner import list_tags

    out = OutputRenderer(mode="json" if json_output else "human")
    root = Path(output_dir) if output_dir else None
    tags = list_tags(root)
    out.write({"status": "ok", "count": len(tags), "tags": tags})
    raise typer.Exit(0)


@command(noun="eval", verb="show", help="Print aggregate.json for one tag")
def eval_show(
    tag: str = typer.Argument(..., help="Tag to show"),
    output_dir: Optional[str] = typer.Option(None, "--output-dir"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    from ai_dev_system.eval.runner import load_aggregate

    out = OutputRenderer(mode="json" if json_output else "human")
    root = Path(output_dir) if output_dir else None
    try:
        agg = load_aggregate(tag, root)
    except FileNotFoundError as exc:
        out.write_error(code=2, message=str(exc))
        raise typer.Exit(2)

    if json_output:
        print(json.dumps(agg, ensure_ascii=False, indent=2))
    else:
        out.write({"status": "ok", "tag": tag, "aggregate": agg})
    raise typer.Exit(0)
