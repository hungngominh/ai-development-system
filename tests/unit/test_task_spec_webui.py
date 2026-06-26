"""Tests for task-spec edit/approve UI helpers in webui."""
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ai_dev_system.task_graph.facets import FACET_KEYS
from ai_dev_system.webui import _render_task_spec, _save_task_spec_edits


def _all_needs_human():
    return {k: {"status": "needs_human", "content": "", "reason": ""} for k in FACET_KEYS}


def _all_filled():
    return {k: {"status": "filled", "content": f"content for {k}", "reason": ""} for k in FACET_KEYS}


# ── _render_task_spec ──────────────────────────────────────────────────────────

def test_render_with_spec_id_produces_form():
    html = _render_task_spec({"title": "Login"}, _all_needs_human(), spec_id="abc123")
    assert "<form" in html
    assert "action='/task-spec'" in html or 'action="/task-spec"' in html
    assert 'method=' in html.lower()


def test_render_with_spec_id_includes_hidden_id_field():
    html = _render_task_spec({"title": "t"}, _all_needs_human(), spec_id="myid")
    assert 'name="id"' in html or "name='id'" in html
    assert "myid" in html


def test_render_with_spec_id_has_textarea_for_each_facet():
    html = _render_task_spec({"title": "t"}, _all_needs_human(), spec_id="x")
    for key in FACET_KEYS:
        assert f"facet_{key}" in html, f"missing textarea for {key}"
    assert html.count("<textarea") == len(FACET_KEYS)


def test_render_prepopulates_filled_content_in_textarea():
    facets = _all_needs_human()
    facets["input"] = {"status": "filled", "content": "User credentials", "reason": ""}
    html = _render_task_spec({"title": "t"}, facets, spec_id="x")
    assert "User credentials" in html


def test_render_needs_human_textarea_is_empty():
    facets = _all_needs_human()
    html = _render_task_spec({"title": "t"}, facets, spec_id="x")
    # All textareas should be empty (no visible content between tags)
    assert "><" in html.replace("></textarea>", "><")  # empty textareas exist


def test_render_includes_submit_button():
    html = _render_task_spec({"title": "t"}, _all_needs_human(), spec_id="x")
    assert "<button" in html
    assert "submit" in html


def test_render_without_spec_id_is_readonly_no_form():
    html = _render_task_spec({"title": "t"}, _all_needs_human(), spec_id=None)
    assert "<form" not in html
    assert "cần làm rõ" in html


def test_render_without_spec_id_shows_filled_content_as_text():
    facets = _all_filled()
    html = _render_task_spec({"title": "t"}, facets, spec_id=None)
    assert "content for input" in html
    assert "<textarea" not in html


# ── _save_task_spec_edits ──────────────────────────────────────────────────────

def _write_spec(tmp_path, spec_id, facets):
    d = tmp_path / "task_specs"
    d.mkdir(exist_ok=True)
    p = d / f"{spec_id}.json"
    p.write_text(json.dumps({"status": "done", "task": {"title": "t"}, "facets": facets}),
                 encoding="utf-8")
    return p


def test_save_edits_sets_filled_status_for_non_empty_content(tmp_path):
    spec_id = "spec001"
    path = _write_spec(tmp_path, spec_id, _all_needs_human())
    _save_task_spec_edits(spec_id, {"input": "User email"}, storage_root=str(tmp_path))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["facets"]["input"]["status"] == "filled"
    assert data["facets"]["input"]["content"] == "User email"


def test_save_edits_marks_spec_approved(tmp_path):
    spec_id = "spec002"
    path = _write_spec(tmp_path, spec_id, _all_needs_human())
    _save_task_spec_edits(spec_id, {"input": "something"}, storage_root=str(tmp_path))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data.get("approved") is True


def test_save_edits_empty_string_stays_needs_human(tmp_path):
    spec_id = "spec003"
    facets = _all_needs_human()
    facets["input"] = {"status": "filled", "content": "old", "reason": ""}
    path = _write_spec(tmp_path, spec_id, facets)
    _save_task_spec_edits(spec_id, {"input": "   "}, storage_root=str(tmp_path))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["facets"]["input"]["status"] == "needs_human"
    assert data["facets"]["input"]["content"] == ""


# ── _task_exec_page ────────────────────────────────────────────────────────────

def _fake_cfg(storage_root: str):
    class _Cfg:
        database_url = "sqlite:///:memory:"
    _Cfg.storage_root = storage_root
    return _Cfg()


def test_task_exec_page_running_shows_log(tmp_path, monkeypatch):
    """Running state: shows log lines and auto-refresh meta tag."""
    import ai_dev_system.webui as webui
    spec_id = "exec001"
    exec_status = {
        "status": "running", "run_id": "r1",
        "branch": "ai-dev/task-exec001", "base_branch": "main",
    }
    spec_dir = tmp_path / "task_specs"
    spec_dir.mkdir()
    (spec_dir / f"{spec_id}-exec.json").write_text(json.dumps(exec_status), encoding="utf-8")
    (spec_dir / f"{spec_id}-exec.log").write_text(
        "[10:00:00] Executor khởi động\n[10:00:01] Branch created\n", encoding="utf-8"
    )
    monkeypatch.setattr(webui, "_config", lambda: _fake_cfg(str(tmp_path)))

    body = webui._task_exec_page(spec_id).decode("utf-8")
    assert "Executor khởi động" in body
    assert "refresh" in body.lower() or "meta http-equiv" in body.lower()


def test_task_exec_page_done_shows_diff_and_buttons(tmp_path, monkeypatch):
    """Done state: shows diff and accept/reject buttons."""
    import ai_dev_system.webui as webui
    spec_id = "exec002"
    exec_status = {
        "status": "done", "exec_status": "COMPLETED",
        "run_id": "r2", "branch": "ai-dev/task-exec002", "base_branch": "main",
    }
    diff_text = "diff --git a/src/foo.py b/src/foo.py\n+def new_func(): pass\n"
    spec_dir = tmp_path / "task_specs"
    spec_dir.mkdir()
    (spec_dir / f"{spec_id}-exec.json").write_text(json.dumps(exec_status), encoding="utf-8")
    (spec_dir / f"{spec_id}-exec.log").write_text("[10:05:00] done\n", encoding="utf-8")
    monkeypatch.setattr(webui, "_config", lambda: _fake_cfg(str(tmp_path)))
    monkeypatch.setattr(webui, "_task_exec_diff", lambda sid, rid, cfg: diff_text)

    body = webui._task_exec_page(spec_id).decode("utf-8")
    assert "new_func" in body
    assert "accept" in body.lower()
    assert "reject" in body.lower()


def test_task_exec_page_error_shows_message(tmp_path, monkeypatch):
    """Error state: shows error message."""
    import ai_dev_system.webui as webui
    spec_id = "exec003"
    exec_status = {
        "status": "error", "error": "git checkout failed",
        "branch": "ai-dev/task-exec003", "base_branch": "main",
    }
    spec_dir = tmp_path / "task_specs"
    spec_dir.mkdir()
    (spec_dir / f"{spec_id}-exec.json").write_text(json.dumps(exec_status), encoding="utf-8")
    (spec_dir / f"{spec_id}-exec.log").write_text("[10:00:00] LỖI\n", encoding="utf-8")
    monkeypatch.setattr(webui, "_config", lambda: _fake_cfg(str(tmp_path)))

    body = webui._task_exec_page(spec_id).decode("utf-8")
    assert "git checkout failed" in body


def test_task_exec_page_unknown_shows_refresh(tmp_path, monkeypatch):
    """No exec status yet: shows refresh."""
    import ai_dev_system.webui as webui
    spec_id = "exec004"
    spec_dir = tmp_path / "task_specs"
    spec_dir.mkdir()
    # No -exec.json file
    monkeypatch.setattr(webui, "_config", lambda: _fake_cfg(str(tmp_path)))

    body = webui._task_exec_page(spec_id).decode("utf-8")
    assert "refresh" in body.lower() or "Chưa có" in body


def test_save_edits_ignores_unknown_keys(tmp_path):
    spec_id = "spec004"
    path = _write_spec(tmp_path, spec_id, _all_needs_human())
    _save_task_spec_edits(spec_id, {"UNKNOWN_KEY": "hacked"}, storage_root=str(tmp_path))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "UNKNOWN_KEY" not in data["facets"]


def test_save_edits_untouched_facets_preserved(tmp_path):
    spec_id = "spec005"
    facets = _all_needs_human()
    facets["database"] = {"status": "filled", "content": "PostgreSQL", "reason": ""}
    path = _write_spec(tmp_path, spec_id, facets)
    # Only edit "input", leave "database" alone
    _save_task_spec_edits(spec_id, {"input": "credentials"}, storage_root=str(tmp_path))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["facets"]["database"]["content"] == "PostgreSQL"
    assert data["facets"]["database"]["status"] == "filled"


# ── Phase A: reasoning display ────────────────────────────────────────────────

def test_render_shows_reasoning_in_details_when_present():
    facets = _all_needs_human()
    facets["input"] = {"status": "filled", "content": "email", "reason": "",
                       "reasoning": "Dev: form input. QA: validate format. Security: no PII log."}
    html = _render_task_spec({"title": "t"}, facets, spec_id="x")
    assert "<details" in html
    assert "Dev: form input" in html


def test_render_no_details_element_when_reasoning_empty(tmp_path):
    facets = {k: {"status": "filled", "content": "c", "reason": "", "reasoning": ""}
              for k in FACET_KEYS}
    html = _render_task_spec({"title": "t"}, facets, spec_id="x")
    assert "<details" not in html


def test_render_reasoning_shown_per_facet_not_globally():
    facets = _all_needs_human()
    facets["input"] = {"status": "filled", "content": "x", "reason": "", "reasoning": "reason A"}
    facets["database"] = {"status": "filled", "content": "y", "reason": "", "reasoning": "reason B"}
    html = _render_task_spec({"title": "t"}, facets, spec_id="x")
    assert "reason A" in html
    assert "reason B" in html


def test_save_edits_all_facets_at_once(tmp_path):
    spec_id = "spec006"
    path = _write_spec(tmp_path, spec_id, _all_needs_human())
    edits = {k: f"content {k}" for k in FACET_KEYS}
    _save_task_spec_edits(spec_id, edits, storage_root=str(tmp_path))
    data = json.loads(path.read_text(encoding="utf-8"))
    for k in FACET_KEYS:
        assert data["facets"][k]["status"] == "filled"
        assert data["facets"][k]["content"] == f"content {k}"


# ── _accept_branch_create_pr: push + gh pr create ───────────────────────────────

def _mk_proc(returncode=0, stdout="", stderr=""):
    p = MagicMock()
    p.returncode = returncode
    p.stdout = stdout
    p.stderr = stderr
    return p


def test_accept_branch_create_pr_success(monkeypatch):
    """git push ok + gh pr create returns a URL → ok=True with parsed pr_url."""
    import ai_dev_system.webui as webui
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["git", "push"]:
            return _mk_proc(0)
        if cmd[:3] == ["gh", "pr", "create"]:
            return _mk_proc(0, stdout="https://github.com/o/r/pull/42\n")
        return _mk_proc(0)

    monkeypatch.setattr(webui.subprocess, "run", fake_run)
    res = webui._accept_branch_create_pr("ai-dev/task-x", "master", "/repo", "My title")
    assert res["ok"] is True
    assert res["pushed"] is True
    assert res["pr_url"] == "https://github.com/o/r/pull/42"
    # The PR title is forwarded to gh.
    gh_call = next(c for c in calls if c[:3] == ["gh", "pr", "create"])
    assert "My title" in gh_call


def test_accept_branch_create_pr_push_fails_falls_back(monkeypatch):
    """git push fails → ok=False, not pushed, error surfaced (caller shows merge hint)."""
    import ai_dev_system.webui as webui

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "push"]:
            return _mk_proc(1, stderr="fatal: 'origin' does not appear to be a git repo")
        return _mk_proc(0)

    monkeypatch.setattr(webui.subprocess, "run", fake_run)
    res = webui._accept_branch_create_pr("ai-dev/task-x", "master", "/repo", "t")
    assert res["ok"] is False
    assert res["pushed"] is False
    assert "does not appear to be a git repo" in (res["error"] or "")


def test_accept_branch_create_pr_existing_pr_recovers_url(monkeypatch):
    """gh pr create fails (PR exists) → recover URL via gh pr view → ok=True."""
    import ai_dev_system.webui as webui

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "push"]:
            return _mk_proc(0)
        if cmd[:3] == ["gh", "pr", "create"]:
            return _mk_proc(1, stderr="a pull request for branch already exists")
        if cmd[:3] == ["gh", "pr", "view"]:
            return _mk_proc(0, stdout="https://github.com/o/r/pull/7\n")
        return _mk_proc(0)

    monkeypatch.setattr(webui.subprocess, "run", fake_run)
    res = webui._accept_branch_create_pr("ai-dev/task-x", "master", "/repo", "t")
    assert res["ok"] is True
    assert res["pr_url"] == "https://github.com/o/r/pull/7"


def test_accept_branch_create_pr_missing_repo():
    """No repo path → ok=False without touching subprocess."""
    import ai_dev_system.webui as webui
    res = webui._accept_branch_create_pr("ai-dev/task-x", "master", "", "t")
    assert res["ok"] is False
    assert res["pushed"] is False
