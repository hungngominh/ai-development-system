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


def test_run_plan_worker_no_url_leaves_plan_clean(tmp_path):
    root = tmp_path / "storage"
    _seed_spec(root, "spec1234ab", "/repos/app")
    with patch("ai_dev_system.task_graph.single_task_worker.publish_doc", return_value=None):
        plan = w.run_plan_worker("spec1234ab", storage_root=str(root))
    assert "doc_url" not in plan


def test_main_mode_plan_dispatches(tmp_path):
    root = tmp_path / "storage"
    _seed_spec(root, "spec1234ab", "/repos/app")
    with patch("ai_dev_system.task_graph.single_task_worker.run_plan_worker") as rp:
        w.main(["--id", "spec1234ab", "--mode", "plan", "--storage-root", str(root)])
    rp.assert_called_once()
