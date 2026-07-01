import ai_dev_system.webui as webui


def test_repo_argv_wins_over_env(monkeypatch):
    calls = []
    monkeypatch.setattr(webui, "apply_project_env", lambda r: calls.append(r))
    monkeypatch.setenv("AIDEV_REPO", "/from/env")
    webui._maybe_apply_project(["--repo", "/from/argv"])
    assert calls == ["/from/argv"]


def test_env_used_when_no_argv(monkeypatch):
    calls = []
    monkeypatch.setattr(webui, "apply_project_env", lambda r: calls.append(r))
    monkeypatch.setenv("AIDEV_REPO", "/from/env")
    webui._maybe_apply_project([])
    assert calls == ["/from/env"]


def test_noop_when_neither(monkeypatch):
    calls = []
    monkeypatch.setattr(webui, "apply_project_env", lambda r: calls.append(r))
    monkeypatch.delenv("AIDEV_REPO", raising=False)
    webui._maybe_apply_project([])
    assert calls == []
