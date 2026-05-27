"""Unit tests for CLI commands added in C5-C7+C10.

Tests cover:
- phase_b: import + function existence
- golden: init creates file, validate detects errors, golden_path helper
- gate: import + sub-command routing
- info: _recommend_next mapping, _mask_creds
- migrate: status command import
- __init__: all new commands auto-imported (tree registered)
- CLI registry: all expected nouns present after import
"""
from __future__ import annotations

import json
from pathlib import Path

import click
import pytest

# typer.Exit raises click.exceptions.Exit (not SystemExit) when called directly
_Exit = click.exceptions.Exit


# ---- CLI tree registration ----


def test_all_nouns_registered():
    """All command nouns must be in the typer app after import."""
    # Import commands (side-effects register onto the root app)
    import ai_dev_system.cli.commands  # noqa: F401
    from ai_dev_system.cli.core.registry import get_app

    app = get_app()
    names = (
        [c.name for c in app.registered_commands]
        + [g.typer_instance.info.name for g in app.registered_groups]
    )
    for expected in ["eval", "gate", "golden", "info", "intake", "migrate", "phase-b", "setup"]:
        assert expected in names, f"Missing noun: {expected!r}, registered: {sorted(names)}"


# ---- phase_b ----


def test_phase_b_module_imports():
    from ai_dev_system.cli.commands import phase_b
    assert callable(phase_b.phase_b_run)
    assert callable(phase_b.phase_b_resume)
    assert callable(phase_b.phase_b_abort)


# ---- golden ----


def test_golden_init_creates_file(tmp_path):
    from ai_dev_system.cli.commands.golden import _GOLDEN_DIR, golden_init

    dest = tmp_path / "test-idea.json"
    try:
        golden_init.__wrapped__(
            idea_id="test-idea",
            raw_idea="Build something",
            output_dir=str(tmp_path),
            json_output=False,
        )
    except _Exit:
        pass  # typer.Exit raises click.exceptions.Exit when called directly

    assert dest.exists()
    data = json.loads(dest.read_text(encoding="utf-8"))
    assert data["idea_id"] == "test-idea"
    assert data["raw_idea"] == "Build something"
    assert "brief_expectations" in data


def test_golden_init_refuses_overwrite(tmp_path):
    dest = tmp_path / "existing.json"
    dest.write_text('{"idea_id":"existing"}', encoding="utf-8")

    from ai_dev_system.cli.commands.golden import golden_init

    with pytest.raises(_Exit) as exc_info:
        golden_init.__wrapped__(
            idea_id="existing",
            raw_idea="x",
            output_dir=str(tmp_path),
            json_output=False,
        )
    assert exc_info.value.exit_code == 1


def test_golden_validate_passes_on_valid_file(tmp_path):
    entry = {
        "idea_id": "my-idea",
        "raw_idea": "Build a chat app",
        "brief_expectations": {"scope_in": {"min_items": 1}},
    }
    (tmp_path / "my-idea.json").write_text(json.dumps(entry), encoding="utf-8")

    from ai_dev_system.cli.commands.golden import golden_validate
    with pytest.raises(_Exit) as exc_info:
        golden_validate.__wrapped__(
            idea_id="my-idea",
            output_dir=str(tmp_path),
            json_output=False,
        )
    assert exc_info.value.exit_code == 0


def test_golden_validate_fails_on_missing_keys(tmp_path):
    (tmp_path / "bad.json").write_text('{"idea_id":"bad"}', encoding="utf-8")

    from ai_dev_system.cli.commands.golden import golden_validate
    with pytest.raises(_Exit) as exc_info:
        golden_validate.__wrapped__(
            idea_id="bad",
            output_dir=str(tmp_path),
            json_output=False,
        )
    assert exc_info.value.exit_code == 1


def test_golden_validate_fails_on_missing_file(tmp_path):
    from ai_dev_system.cli.commands.golden import golden_validate
    with pytest.raises(_Exit) as exc_info:
        golden_validate.__wrapped__(
            idea_id="nonexistent",
            output_dir=str(tmp_path),
            json_output=False,
        )
    assert exc_info.value.exit_code == 1


def test_golden_validate_fails_on_idea_id_mismatch(tmp_path):
    entry = {
        "idea_id": "wrong-id",
        "raw_idea": "x",
        "brief_expectations": {},
    }
    (tmp_path / "my-idea.json").write_text(json.dumps(entry), encoding="utf-8")

    from ai_dev_system.cli.commands.golden import golden_validate
    with pytest.raises(_Exit) as exc_info:
        golden_validate.__wrapped__(
            idea_id="my-idea",
            output_dir=str(tmp_path),
            json_output=False,
        )
    assert exc_info.value.exit_code == 1


# ---- info ----


def test_recommend_next_known_statuses():
    from ai_dev_system.cli.commands.info import _recommend_next

    assert "intake resume" in _recommend_next("COLLECTING_INTAKE")
    assert "debate start" in _recommend_next("INTAKE_COMPLETE")
    assert "gate review-debate" in _recommend_next("DEBATE_COMPLETE")
    assert "phase-b run" in _recommend_next("RUNNING_PHASE_1D")
    assert "intake start" in _recommend_next("ABORTED")
    assert "done" in _recommend_next("COMPLETE")


def test_recommend_next_unknown_status():
    from ai_dev_system.cli.commands.info import _recommend_next
    result = _recommend_next("SOME_UNKNOWN_STATUS")
    assert "SOME_UNKNOWN_STATUS" in result


def test_mask_creds_hides_password():
    from ai_dev_system.cli.commands.info import _mask_creds
    url = "postgresql://user:secret@host:5432/db"
    masked = _mask_creds(url)
    assert "secret" not in masked
    assert "user" in masked
    assert "***" in masked


def test_mask_creds_sqlite_unchanged():
    from ai_dev_system.cli.commands.info import _mask_creds
    url = "sqlite:///test.db"
    assert _mask_creds(url) == url


def test_mask_creds_none_safe():
    from ai_dev_system.cli.commands.info import _mask_creds
    assert _mask_creds(None) == ""


# ---- gate ----


def test_gate_module_imports():
    from ai_dev_system.cli.commands import gate
    assert callable(gate.gate_review_debate)
    assert callable(gate.gate_review_graph)
    assert callable(gate.gate_review_verification)


# ---- migrate status ----


def test_migrate_status_function_exists():
    from ai_dev_system.cli.commands.migrate import migrate_status
    assert callable(migrate_status)


def test_migrate_classify_runs_function_exists():
    from ai_dev_system.cli.commands.migrate import migrate_classify_runs
    assert callable(migrate_classify_runs)


# ---- legacy arrows removed ----


def test_legacy_docstring_has_no_unicode_arrows():
    from ai_dev_system.cli.commands import legacy
    assert "→" not in legacy.__doc__, "Unicode arrow in legacy docstring breaks Windows CP1252"
