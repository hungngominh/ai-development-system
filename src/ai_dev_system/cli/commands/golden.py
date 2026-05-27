"""ai-dev golden — golden dataset management for eval harness.

Verbs:
- init      — scaffold a new golden idea entry
- validate  — check a golden entry for required fields / format
- dryrun    — run eval metrics against a golden without persisting
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from ai_dev_system.cli.core.output import OutputRenderer
from ai_dev_system.cli.core.registry import command

_GOLDEN_DIR = Path(".golden")


def _golden_path(idea_id: str) -> Path:
    return _GOLDEN_DIR / f"{idea_id}.json"


@command(
    noun="golden",
    verb="init",
    help="Scaffold a new golden idea entry",
    noun_help="Golden dataset management for the eval harness",
)
def golden_init(
    idea_id: str = typer.Argument(..., help="Unique idea identifier (e.g. 'task-manager-v1')"),
    raw_idea: str = typer.Option(..., "--idea", help="Raw idea text"),
    output_dir: Optional[str] = typer.Option(None, "--dir", help="Override golden directory (default: .golden/)"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Create a skeleton golden entry at .golden/<idea-id>.json."""
    out = OutputRenderer(mode="json" if json_output else "human")
    base = Path(output_dir) if output_dir else _GOLDEN_DIR
    base.mkdir(parents=True, exist_ok=True)
    dest = base / f"{idea_id}.json"

    if dest.exists():
        out.write_error(code=1, message=f"Golden entry {idea_id!r} already exists at {dest}")
        raise typer.Exit(1)

    skeleton = {
        "idea_id": idea_id,
        "raw_idea": raw_idea,
        "brief_expectations": {
            "problem_statement": {"contains": ["TODO"]},
            "scope_in": {"min_items": 1},
            "success_metric": {"non_empty": True},
        },
        "tags": [],
        "notes": "",
    }
    dest.write_text(json.dumps(skeleton, indent=2, ensure_ascii=False), encoding="utf-8")
    out.success(f"Created golden entry: {dest}")
    out.write({"status": "ok", "idea_id": idea_id, "path": str(dest)})
    raise typer.Exit(0)


@command(noun="golden", verb="validate", help="Validate a golden entry for required fields")
def golden_validate(
    idea_id: str = typer.Argument(..., help="Idea ID to validate"),
    output_dir: Optional[str] = typer.Option(None, "--dir"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Check that a golden entry has all required fields."""
    out = OutputRenderer(mode="json" if json_output else "human")
    base = Path(output_dir) if output_dir else _GOLDEN_DIR
    path = base / f"{idea_id}.json"

    if not path.exists():
        out.write_error(code=1, message=f"Golden entry {idea_id!r} not found at {path}")
        raise typer.Exit(1)

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        out.write_error(code=1, message=f"Invalid JSON: {exc}")
        raise typer.Exit(1)

    required_keys = {"idea_id", "raw_idea", "brief_expectations"}
    missing = required_keys - data.keys()
    errors: list[str] = []
    if missing:
        errors.append(f"Missing required keys: {sorted(missing)}")
    if data.get("idea_id") != idea_id:
        errors.append(f"idea_id mismatch: expected {idea_id!r}, got {data.get('idea_id')!r}")
    if not data.get("raw_idea", "").strip():
        errors.append("raw_idea is empty")

    if errors:
        out.write_error(code=1, message=f"{len(errors)} validation error(s)", errors=errors)
        raise typer.Exit(1)

    out.success(f"Golden entry {idea_id!r} is valid.")
    out.write({"status": "ok", "idea_id": idea_id, "path": str(path)})
    raise typer.Exit(0)


@command(noun="golden", verb="dryrun", help="Run eval on one golden idea without persisting")
def golden_dryrun(
    idea_id: str = typer.Argument(..., help="Idea ID to dry-run"),
    output_dir: Optional[str] = typer.Option(None, "--dir"),
    json_output: bool = typer.Option(False, "--json"),
    quiet: bool = typer.Option(False, "--quiet"),
) -> None:
    """Evaluate one golden entry; print metrics but do not write to .eval_runs/."""
    from ai_dev_system.eval.runner import run_brief_eval

    out = OutputRenderer(mode="json" if json_output else "human", quiet=quiet)
    base = Path(output_dir) if output_dir else _GOLDEN_DIR
    path = base / f"{idea_id}.json"

    if not path.exists():
        out.write_error(code=1, message=f"Golden entry {idea_id!r} not found at {path}")
        raise typer.Exit(1)

    import tempfile

    out.progress(f"Dry-running eval for {idea_id}...")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            report = run_brief_eval(
                tag=f"dryrun_{idea_id}",
                idea_ids=[idea_id],
                output_root=Path(tmp),
                mode="stub",
                golden_dir=base,
            )
        payload = {
            "status": "ok",
            "idea_id": idea_id,
            "overall_pass": report.aggregate.get("overall_pass_count", 0),
            "dry_run": True,
        }
        out.write(payload)
        raise typer.Exit(0)
    except typer.Exit:
        raise
    except Exception as exc:
        out.write_error(code=1, message=f"golden dryrun failed: {exc}")
        raise typer.Exit(1)
