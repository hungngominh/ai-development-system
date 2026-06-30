# tests/unit/task_graph/test_single_task_worker_clarify.py
import json
from pathlib import Path

import ai_dev_system.task_graph.single_task_worker as w


def _patch_common(monkeypatch, result):
    monkeypatch.setattr(w, "spec_single_task", lambda *a, **k: result)
    # agentic path passes repo → worker builds no llm for facets, but DOES build one
    # for synthesis; make that a no-op so we exercise the fallback deterministically.
    monkeypatch.setattr(w, "make_llm_client", lambda step: None)
    monkeypatch.setattr(w, "_record_run_row", lambda *a, **k: None)


def test_worker_writes_clarify_needed_true(tmp_path, monkeypatch):
    result = {
        "task": {"title": "t"},
        "facets": {"security_rules": {"status": "needs_human", "content": "enum risk"}},
        "findings": [{"section": "business_rule", "severity": "error", "message": "GUID vs PK"}],
    }
    _patch_common(monkeypatch, result)
    w.run_worker("abc", "add OwnerId", "/repo", storage_root=str(tmp_path), database_url="sqlite://")
    spec = json.loads((tmp_path / "task_specs" / "abc.json").read_text(encoding="utf-8"))
    assert spec["clarify"]["needed"] is True
    assert spec["clarify"]["questions"]            # non-empty (raw-message fallback used)


def test_worker_writes_clarify_needed_false_when_clean(tmp_path, monkeypatch):
    result = {"task": {"title": "t"},
              "facets": {"input": {"status": "filled", "content": "ok"}},
              "findings": []}
    _patch_common(monkeypatch, result)
    w.run_worker("def", "x", "/repo", storage_root=str(tmp_path), database_url="sqlite://")
    spec = json.loads((tmp_path / "task_specs" / "def.json").read_text(encoding="utf-8"))
    assert spec["clarify"] == {"needed": False, "questions": []}
