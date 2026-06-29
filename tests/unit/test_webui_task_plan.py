from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import ai_dev_system.webui as webui


@pytest.fixture
def _cfg(tmp_path, monkeypatch):
    class _C:
        storage_root = str(tmp_path)
        database_url = "sqlite:///:memory:"
    monkeypatch.setattr(webui, "_config", lambda: _C())
    return _C()


def _write_spec(tmp_path, spec_id, repo="/repo"):
    out = Path(tmp_path) / "task_specs"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{spec_id}.json").write_text(json.dumps({
        "status": "done", "idea": "add X", "repo": repo,
        "task": {"id": "TASK-ADHOC", "title": "Add X", "objective": "do X"},
        "facets": {"test_cases": {"status": "filled", "content": "t", "reason": ""}},
        "approved": True,
    }), encoding="utf-8")


def test_task_plan_page_renders_two_tasks_and_branch(_cfg, tmp_path):
    from ai_dev_system.task_graph.single_task_plan import plan_single_task
    _write_spec(tmp_path, "abc123def456")
    plan_single_task(json.loads((Path(tmp_path) / "task_specs" / "abc123def456.json").read_text("utf-8")),
                     "abc123def456", storage_root=str(tmp_path))
    html_bytes = webui._task_plan_page("abc123def456")
    body = html_bytes.decode("utf-8")
    assert "ai-dev/task-abc123de" in body
    assert "TASK-ADHOC-TEST" in body and "TASK-ADHOC-IMPL" in body
    assert "Duyệt" in body  # approve button present


def test_task_plan_page_missing_plan(_cfg):
    body = webui._task_plan_page("missing999").decode("utf-8")
    assert "Không tìm thấy" in body or "plan" in body.lower()


def test_approve_plan_then_spawn(_cfg, tmp_path):
    from ai_dev_system.task_graph.single_task_plan import plan_single_task, load_plan
    _write_spec(tmp_path, "abc123def456")
    plan_single_task(json.loads((Path(tmp_path) / "task_specs" / "abc123def456.json").read_text("utf-8")),
                     "abc123def456", storage_root=str(tmp_path))
    with patch.object(webui, "_spawn_task_executor") as spawn:
        webui._approve_task_plan_and_exec("abc123def456")
    assert load_plan(str(tmp_path), "abc123def456")["approved"] is True
    spawn.assert_called_once_with("abc123def456")
