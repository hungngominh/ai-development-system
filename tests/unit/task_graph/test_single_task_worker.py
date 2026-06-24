import json

from ai_dev_system.task_graph import single_task_worker as w


def test_run_worker_writes_done_file_no_repo(tmp_path, monkeypatch):
    # Force the text path with a stub LLM so no real claude/LLM is called.
    from ai_dev_system.debate.llm import StubDebateLLMClient
    monkeypatch.setattr(w, "make_real_llm_client", lambda: StubDebateLLMClient())
    path = w.run_worker("abc123", "add CSV import", None, storage_root=str(tmp_path))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["status"] == "done"
    assert "facets" in data and "task" in data


def test_run_worker_writes_error_on_failure(tmp_path, monkeypatch):
    def _boom(*a, **k): raise RuntimeError("kaboom")
    monkeypatch.setattr(w, "spec_single_task", _boom)
    path = w.run_worker("abc123", "x", "/some/repo", storage_root=str(tmp_path))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["status"] == "error"
    assert "kaboom" in data["error"]


def test_run_worker_writes_error_when_make_client_fails(tmp_path, monkeypatch):
    def _boom():
        raise RuntimeError("no LLM config")
    monkeypatch.setattr(w, "make_real_llm_client", _boom)
    # no repo → worker calls make_real_llm_client(), which raises
    path = w.run_worker("idX", "some idea", None, storage_root=str(tmp_path))
    import json
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["status"] == "error"
    assert "no LLM config" in data["error"]
