# tests/unit/cli/test_root_repo_option.py
from typer.testing import CliRunner

import ai_dev_system.cli.main as cli_main

runner = CliRunner()


def test_repo_flag_invokes_apply_project_env(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cli_main, "apply_project_env", lambda r: calls.append(r))
    # any registered subcommand; callback runs before it. Exit code irrelevant.
    runner.invoke(cli_main.app, ["--repo", str(tmp_path / "repo"), "info"])
    assert calls == [str(tmp_path / "repo")]


def test_aidev_repo_env_invokes_apply_project_env(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cli_main, "apply_project_env", lambda r: calls.append(r))
    monkeypatch.setenv("AIDEV_REPO", str(tmp_path / "envrepo"))
    runner.invoke(cli_main.app, ["info"])
    assert calls == [str(tmp_path / "envrepo")]


def test_no_repo_does_not_invoke(monkeypatch):
    calls = []
    monkeypatch.setattr(cli_main, "apply_project_env", lambda r: calls.append(r))
    monkeypatch.delenv("AIDEV_REPO", raising=False)
    runner.invoke(cli_main.app, ["info"])
    assert calls == []
