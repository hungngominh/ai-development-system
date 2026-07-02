# tests/unit/task_graph/test_single_task_worker_publish.py
import json
from pathlib import Path
from unittest.mock import patch

from ai_dev_system.task_graph import single_task_worker as w
from ai_dev_system.task_graph.single_task_plan import plan_path


def _seed_spec(root: Path, spec_id: str, repo: str):
    d = root / "task_specs"; d.mkdir(parents=True, exist_ok=True)
    (d / f"{spec_id}.json").write_text(json.dumps({
        "status": "done", "idea": "add owner id", "repo": repo,
        "task": {"title": "Add OwnerId", "objective": "expose owner id"}, "facets": {},
        "clarify": {"needed": False, "questions": []},
    }), encoding="utf-8")


def test_run_plan_worker_builds_plan_and_records_doc_url(tmp_path):
    root = tmp_path / "storage"
    _seed_spec(root, "spec1234ab", "/repos/app")
    with patch("ai_dev_system.task_graph.single_task_worker.publish_doc",
               return_value="https://github.com/o/r/blob/b/plan.md") as pub:
        plan = w.run_plan_worker("spec1234ab", storage_root=str(root))
    assert pub.called
    assert plan["doc_url"] == "https://github.com/o/r/blob/b/plan.md"
    saved = json.loads(plan_path(str(root), "spec1234ab").read_text(encoding="utf-8"))
    assert saved["doc_url"] == "https://github.com/o/r/blob/b/plan.md"


def test_run_plan_worker_no_url_marks_publish_failure(tmp_path):
    root = tmp_path / "storage"
    _seed_spec(root, "spec1234ab", "/repos/app")
    with patch("ai_dev_system.task_graph.single_task_worker.publish_doc", return_value=None):
        plan = w.run_plan_worker("spec1234ab", storage_root=str(root))
    assert "doc_url" not in plan
    assert plan["doc_publish_failed"] is True
    # persisted so the gateway progress tool can warn the user
    saved = json.loads(plan_path(str(root), "spec1234ab").read_text(encoding="utf-8"))
    assert saved["doc_publish_failed"] is True


def test_run_worker_marks_spec_doc_publish_failure(tmp_path, monkeypatch, file_db_url):
    """Spec done + repo bound but publish fails → payload carries the failure flag."""
    monkeypatch.setattr(w, "spec_single_task",
                        lambda *a, **k: {"task": {"title": "T"}, "facets": {}})
    monkeypatch.setattr(w, "publish_doc", lambda *a, **k: None)
    path = w.run_worker("specpub1", "idea", "/repos/app",
                        storage_root=str(tmp_path), database_url=file_db_url)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["status"] == "done"
    assert "spec_doc_url" not in data
    assert data["doc_publish_failed"] is True


def test_run_worker_publish_success_sets_no_failure_flag(tmp_path, monkeypatch, file_db_url):
    monkeypatch.setattr(w, "spec_single_task",
                        lambda *a, **k: {"task": {"title": "T"}, "facets": {}})
    monkeypatch.setattr(w, "publish_doc",
                        lambda *a, **k: "https://github.com/o/r/blob/b/spec.md")
    path = w.run_worker("specpub2", "idea", "/repos/app",
                        storage_root=str(tmp_path), database_url=file_db_url)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["spec_doc_url"] == "https://github.com/o/r/blob/b/spec.md"
    assert "doc_publish_failed" not in data


def test_main_mode_plan_dispatches(tmp_path):
    root = tmp_path / "storage"
    _seed_spec(root, "spec1234ab", "/repos/app")
    with patch("ai_dev_system.task_graph.single_task_worker.run_plan_worker") as rp:
        w.main(["--id", "spec1234ab", "--mode", "plan", "--storage-root", str(root)])
    rp.assert_called_once()
