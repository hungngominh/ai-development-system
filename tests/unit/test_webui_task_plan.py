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


def test_approve_idempotent_no_double_spawn(_cfg, tmp_path):
    """Second call to _approve_task_plan_and_exec must not spawn a second executor."""
    from ai_dev_system.task_graph.single_task_plan import plan_single_task
    _write_spec(tmp_path, "idem111aaa")
    plan_single_task(
        json.loads((Path(tmp_path) / "task_specs" / "idem111aaa.json").read_text("utf-8")),
        "idem111aaa", storage_root=str(tmp_path),
    )
    exec_dir = Path(tmp_path) / "task_specs"
    exec_dir.mkdir(parents=True, exist_ok=True)

    with patch.object(webui, "_spawn_task_executor") as spawn:
        # First call: no exec.json → spawns
        webui._approve_task_plan_and_exec("idem111aaa")
        # Simulate executor is now running
        (exec_dir / "idem111aaa-exec.json").write_text(
            json.dumps({"status": "running"}), encoding="utf-8"
        )
        # Second call: status=running → must NOT spawn again
        webui._approve_task_plan_and_exec("idem111aaa")

    spawn.assert_called_once_with("idem111aaa")


def test_spec_approve_redirect_with_repo(_cfg, tmp_path):
    """_spec_approve_redirect builds plan, returns /task-plan URL, never spawns executor."""
    _write_spec(tmp_path, "rewire111bbb", repo="/some/repo")
    with patch.object(webui, "_spawn_task_executor") as spawn:
        redirect = webui._spec_approve_redirect("rewire111bbb")
    assert redirect.startswith("/task-plan?id=")
    plan_file = Path(tmp_path) / "task_specs" / "rewire111bbb-plan.json"
    assert plan_file.exists(), "plan file must be created"
    plan = json.loads(plan_file.read_text("utf-8"))
    assert plan.get("approved") is False or plan.get("approved") is None, (
        "plan must NOT be pre-approved; approved should be False/absent"
    )
    spawn.assert_not_called()


def test_spec_approve_redirect_no_repo(_cfg, tmp_path):
    """_spec_approve_redirect without a repo stays on /task-spec, writes no plan."""
    _write_spec(tmp_path, "norep222ccc", repo="")
    with patch.object(webui, "_spawn_task_executor") as spawn:
        redirect = webui._spec_approve_redirect("norep222ccc")
    assert redirect.startswith("/task-spec?id=")
    plan_file = Path(tmp_path) / "task_specs" / "norep222ccc-plan.json"
    assert not plan_file.exists(), "plan file must NOT be created when no repo"
    spawn.assert_not_called()


def test_task_plan_page_approved_hides_approve_button(_cfg, tmp_path):
    """After approve_plan, the plan page must not show the Duyệt & Chạy submit button."""
    from ai_dev_system.task_graph.single_task_plan import plan_single_task, approve_plan
    _write_spec(tmp_path, "hidebtn333")
    plan_single_task(
        json.loads((Path(tmp_path) / "task_specs" / "hidebtn333.json").read_text("utf-8")),
        "hidebtn333", storage_root=str(tmp_path),
    )
    approve_plan(str(tmp_path), "hidebtn333")
    body = webui._task_plan_page("hidebtn333").decode("utf-8")
    # The approve-action hidden input must be absent (or button text absent)
    assert 'value="approve"' not in body, "approve submit must be hidden after approval"
    assert "/task-exec" in body, "/task-exec link must appear after approval"
