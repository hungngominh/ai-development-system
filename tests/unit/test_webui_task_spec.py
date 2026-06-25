import json
import types

from ai_dev_system import webui
from ai_dev_system.task_graph.facets import FACET_KEYS, SPEC_FACET_KEYS, EXEC_FACET_KEYS


def _facets(over=None):
    f = {k: {"status": "filled", "content": f"{k} detail", "reason": ""} for k in FACET_KEYS}
    f.update(over or {})
    return f


def test_render_shows_filled_hides_na_flags_needs_human():
    facets = _facets({
        "database": {"status": "na", "content": "", "reason": "stateless"},
        "auth_permission": {"status": "needs_human", "content": "", "reason": ""},
    })
    html_out = webui._render_task_spec({"title": "My Task"}, facets)
    assert "input detail" in html_out
    assert "stateless" in html_out          # na reason shown
    assert "cần làm rõ" in html_out          # needs_human flagged
    assert "My Task" in html_out


def test_render_escapes_html():
    facets = _facets({"input": {"status": "filled", "content": "<script>x</script>", "reason": ""}})
    out = webui._render_task_spec({"title": "T"}, facets)
    assert "<script>x</script>" not in out
    assert "&lt;script&gt;" in out

    # title is user-derived → must be escaped
    out_title = webui._render_task_spec({"title": "<b>X</b>"}, _facets())
    assert "<b>X</b>" not in out_title
    assert "&lt;b&gt;" in out_title

    # na reason is user-derived → must be escaped
    facets_na = _facets({"input": {"status": "na", "content": "", "reason": "<img src=x onerror=1>"}})
    out_reason = webui._render_task_spec({"title": "T"}, facets_na)
    assert "<img src=x" not in out_reason
    assert "&lt;img" in out_reason


def test_save_writes_json_and_returns_path(tmp_path):
    facets = _facets()
    task = {"id": "TASK-ADHOC", "title": "My Task", "objective": "do x", "facets": facets}
    path = webui._save_task_spec(task, facets, storage_root=str(tmp_path))
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["task"]["title"] == "My Task"
    assert set(data["facets"].keys()) == set(FACET_KEYS)
    assert path.parent.name == "task_specs"


def test_spec_task_stub_renders_and_saves(tmp_path, monkeypatch):
    monkeypatch.setattr(webui, "_config",
                        lambda: types.SimpleNamespace(storage_root=str(tmp_path)))
    page = webui._spec_task("build a CSV importer", "stub")
    assert isinstance(page, (bytes, bytearray))
    text = page.decode("utf-8")
    assert "Task spec" in text
    assert "cần làm rõ" in text   # stub → all needs_human
    # a TaskSpec file was written
    saved = list((tmp_path / "task_specs").glob("*.json"))
    assert len(saved) == 1


def test_spec_task_empty_idea_returns_message():
    page = webui._spec_task("", "stub")
    assert b"task" in page.lower()  # renders a page, not a crash


# ---------------------------------------------------------------------------
# Task 4: _task_spec_page polling-page renderer
# ---------------------------------------------------------------------------

def test_task_spec_page_running(tmp_path, monkeypatch):
    monkeypatch.setattr(webui, "_config", lambda: types.SimpleNamespace(storage_root=str(tmp_path)))
    d = tmp_path / "task_specs"; d.mkdir()
    (d / "id1.json").write_text(json.dumps({"status": "running", "idea": "x"}), encoding="utf-8")
    page = webui._task_spec_page("id1").decode("utf-8")
    assert "đang chạy" in page.lower() or "running" in page.lower()
    assert "http-equiv" in page  # auto-refresh while running


def test_task_spec_page_done_renders_facets(tmp_path, monkeypatch):
    monkeypatch.setattr(webui, "_config", lambda: types.SimpleNamespace(storage_root=str(tmp_path)))
    from ai_dev_system.task_graph.facets import FACET_KEYS
    facets = {k: {"status": "filled", "content": f"{k} c", "reason": ""} for k in FACET_KEYS}
    d = tmp_path / "task_specs"; d.mkdir()
    (d / "id2.json").write_text(json.dumps(
        {"status": "done", "task": {"title": "My Task"}, "facets": facets}), encoding="utf-8")
    page = webui._task_spec_page("id2").decode("utf-8")
    assert "Task spec" in page and "input c" in page


def test_task_spec_page_error(tmp_path, monkeypatch):
    monkeypatch.setattr(webui, "_config", lambda: types.SimpleNamespace(storage_root=str(tmp_path)))
    d = tmp_path / "task_specs"; d.mkdir()
    (d / "id3.json").write_text(json.dumps({"status": "error", "error": "kaboom"}), encoding="utf-8")
    page = webui._task_spec_page("id3").decode("utf-8")
    assert "kaboom" in page


def test_task_spec_page_error_escapes_html(tmp_path, monkeypatch):
    monkeypatch.setattr(webui, "_config", lambda: types.SimpleNamespace(storage_root=str(tmp_path)))
    d = tmp_path / "task_specs"; d.mkdir()
    (d / "idE.json").write_text(json.dumps({"status": "error", "error": "<script>x</script>"}), encoding="utf-8")
    page = webui._task_spec_page("idE").decode("utf-8")
    assert "<script>x</script>" not in page
    assert "&lt;script&gt;" in page


def test_task_spec_page_unknown_id(tmp_path, monkeypatch):
    monkeypatch.setattr(webui, "_config", lambda: types.SimpleNamespace(storage_root=str(tmp_path)))
    page = webui._task_spec_page("nope")
    assert isinstance(page, (bytes, bytearray))  # no crash on missing file
    assert "Không tìm thấy" in page.decode("utf-8")


# ---------------------------------------------------------------------------
# Fix 1: _spawn_task_spec_worker caps idea/repo before Popen
# ---------------------------------------------------------------------------

def test_spawn_caps_idea_length(tmp_path, monkeypatch):
    monkeypatch.setattr(webui, "_config", lambda: types.SimpleNamespace(
        storage_root=str(tmp_path), database_url="sqlite:///:memory:"))
    recorded = {}

    def _fake_popen(cmd, **kw):
        recorded["cmd"] = cmd

        class _P:
            pass

        return _P()

    monkeypatch.setattr(webui.subprocess, "Popen", _fake_popen)
    spec_id = webui._spawn_task_spec_worker("x" * 50000, "C:/repo")
    assert spec_id and (tmp_path / "task_specs" / f"{spec_id}.json").exists()
    idea_arg = recorded["cmd"][recorded["cmd"].index("--idea") + 1]
    assert len(idea_arg) <= 8000


# ---------------------------------------------------------------------------
# Task-specs appear in the home "Runs" table (linked to /task-spec, not /run)
# ---------------------------------------------------------------------------

def test_home_lists_task_spec_row_linking_to_task_spec(monkeypatch, file_config):
    from ai_dev_system.db.connection import get_connection
    from ai_dev_system.db.helpers import dump_json

    run_id = "abc123def456"
    conn = get_connection(file_config.database_url)
    conn.execute(
        "INSERT INTO runs (run_id, project_id, status, title, metadata) "
        "VALUES (?, ?, ?, ?, ?)",
        (run_id, "adhoc-task-spec", "COMPLETED", "My spec task",
         dump_json({"kind": "task_spec", "spec_id": run_id})),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(webui, "_config", lambda: file_config)
    page = webui._home().decode("utf-8")
    # task-spec rows route to the facet page, never the debate run-detail page
    assert f"/task-spec?id={run_id}" in page
    assert f"/run?id={run_id}" not in page


def test_home_lists_debate_run_linking_to_run_detail(monkeypatch, file_config):
    from ai_dev_system.db.connection import get_connection

    run_id = "debate999aaa"
    conn = get_connection(file_config.database_url)
    conn.execute(
        "INSERT INTO runs (run_id, project_id, status, title) VALUES (?, ?, ?, ?)",
        (run_id, "proj1", "COMPLETED", "Pipeline: debate"),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(webui, "_config", lambda: file_config)
    page = webui._home().decode("utf-8")
    # ordinary runs keep their /run?id= link
    assert f"/run?id={run_id}" in page
    assert f"/task-spec?id={run_id}" not in page


# ---------------------------------------------------------------------------
# Task 3: grouped rendering — spec facets section + exec facets section
# ---------------------------------------------------------------------------

def test_render_shows_spec_and_exec_sections():
    facets = _facets()
    html_out = webui._render_task_spec({"title": "T"}, facets)
    assert "Spec facets (13)" in html_out
    assert "Implementation documents (7)" in html_out


def test_render_exec_na_shows_reason():
    facets = _facets()
    for k in EXEC_FACET_KEYS:
        facets[k] = {"status": "na", "content": "", "reason": "exec-time — fill after implementation"}
    html_out = webui._render_task_spec({"title": "T"}, facets)
    assert "exec-time" in html_out


def test_task_spec_page_done_all_spec_needs_human_shows_warning(tmp_path, monkeypatch):
    monkeypatch.setattr(webui, "_config", lambda: types.SimpleNamespace(storage_root=str(tmp_path)))
    # all spec facets needs_human, exec facets na — simulates a full LLM failure
    facets = {k: {"status": "needs_human", "content": "", "reason": ""} for k in SPEC_FACET_KEYS}
    facets.update({k: {"status": "na", "content": "", "reason": "exec-time"} for k in EXEC_FACET_KEYS})
    d = tmp_path / "task_specs"; d.mkdir()
    (d / "warn1.json").write_text(
        json.dumps({"status": "done", "task": {"title": "T"}, "facets": facets}),
        encoding="utf-8",
    )
    page = webui._task_spec_page("warn1").decode("utf-8")
    assert "⚠" in page or "Tất cả" in page  # warning card shown
