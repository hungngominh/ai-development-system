import json
import types

from ai_dev_system import webui
from ai_dev_system.task_graph.facets import FACET_KEYS


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
