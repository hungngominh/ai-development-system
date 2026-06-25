import json

from ai_dev_system.task_graph import single_task_worker as w
from ai_dev_system.db.connection import get_connection


def test_run_worker_writes_done_file_no_repo(tmp_path, monkeypatch, file_db_url):
    # Force the text path with a stub LLM so no real claude/LLM is called.
    from ai_dev_system.debate.llm import StubDebateLLMClient
    monkeypatch.setattr(w, "make_real_llm_client", lambda: StubDebateLLMClient())
    path = w.run_worker("abc123", "add CSV import", None,
                        storage_root=str(tmp_path), database_url=file_db_url)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["status"] == "done"
    assert "facets" in data and "task" in data


def test_run_worker_writes_error_on_failure(tmp_path, monkeypatch, file_db_url):
    def _boom(*a, **k): raise RuntimeError("kaboom")
    monkeypatch.setattr(w, "spec_single_task", _boom)
    path = w.run_worker("abc123", "x", "/some/repo",
                        storage_root=str(tmp_path), database_url=file_db_url)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["status"] == "error"
    assert "kaboom" in data["error"]


def test_run_worker_writes_error_when_make_client_fails(tmp_path, monkeypatch, file_db_url):
    def _boom():
        raise RuntimeError("no LLM config")
    monkeypatch.setattr(w, "make_real_llm_client", _boom)
    # no repo → worker calls make_real_llm_client(), which raises
    path = w.run_worker("idX", "some idea", None,
                        storage_root=str(tmp_path), database_url=file_db_url)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["status"] == "error"
    assert "no LLM config" in data["error"]


def test_run_worker_upserts_completed_run_row(tmp_path, monkeypatch, file_db_url):
    """A successful spec writes a terminal COMPLETED run row marked as a task_spec."""
    from ai_dev_system.debate.llm import StubDebateLLMClient
    monkeypatch.setattr(w, "make_real_llm_client", lambda: StubDebateLLMClient())
    w.run_worker("spec0001", "add CSV import", None,
                 storage_root=str(tmp_path), database_url=file_db_url)
    conn = get_connection(file_db_url)
    row = conn.execute(
        "SELECT run_id, status, title, metadata FROM runs WHERE run_id = ?",
        ("spec0001",),
    ).fetchone()
    conn.close()
    assert row is not None
    assert row["status"] == "COMPLETED"
    assert row["title"]  # non-empty human label
    assert json.loads(row["metadata"])["kind"] == "task_spec"


def test_run_worker_upserts_failed_run_row(tmp_path, monkeypatch, file_db_url):
    """A failed spec still records a row, with terminal FAILED status."""
    def _boom(*a, **k): raise RuntimeError("kaboom")
    monkeypatch.setattr(w, "spec_single_task", _boom)
    w.run_worker("spec0002", "x", "/some/repo",
                 storage_root=str(tmp_path), database_url=file_db_url)
    conn = get_connection(file_db_url)
    row = conn.execute(
        "SELECT status, metadata FROM runs WHERE run_id = ?", ("spec0002",),
    ).fetchone()
    conn.close()
    assert row is not None
    assert row["status"] == "FAILED"
    assert json.loads(row["metadata"])["kind"] == "task_spec"


def test_run_worker_db_failure_still_writes_file(tmp_path, monkeypatch):
    """DB recording is best-effort: a bad database_url must not lose the JSON artifact."""
    from ai_dev_system.debate.llm import StubDebateLLMClient
    monkeypatch.setattr(w, "make_real_llm_client", lambda: StubDebateLLMClient())
    path = w.run_worker("spec0003", "idea", None,
                        storage_root=str(tmp_path), database_url="not-a-sqlite-url")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["status"] == "done"
