# Standalone Single-Task Spec Mode — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A webui form where the user pastes one task description and gets back an 8-facet TaskSpec (reusing the slice-1 facet engine), displayed and saved — no execution.

**Architecture:** A pure core (`task_graph/single_task.py`) turns free text → a minimal atomic coding task → 8 facets via `generate_task_facets`. The webui adds a form + `/spec-task` route that calls the core, renders a facet card, and saves the TaskSpec JSON. No task-graph/Gate/execution machinery is touched.

**Tech Stack:** Python 3.12, stdlib (`json`, `html`, `uuid`, `pathlib`), `pytest`.

## Global Constraints

- **Python 3.12**; run tests with `PYTHONUTF8=1 python -m pytest ...`.
- **Reuse the slice-1 facet engine** — import `generate_task_facets`, `FACET_KEYS`, `is_implementation_task` from `ai_dev_system.task_graph.facets`. Do NOT reimplement facet logic.
- **LLM interface is `complete(system, user)`** — `spec_single_task` passes the chosen client straight to `generate_task_facets` (which uses that shape). Stub (`StubDebateLLMClient`) → all facets `needs_human` (facets.py is resilient + avoids stub substrings); real client (`make_real_llm_client`) → real facets.
- **No execution / no Gate / no task-graph / no `validate_graph`.** A single ad-hoc task is not the 4-phase graph.
- **HTML-escape all user-derived content** with `html.escape` in any rendered HTML.
- **TaskSpec persistence:** write `{task, facets}` JSON under `<storage_root>/task_specs/`, filename = `<slug>-<uuid8>.json`; `mkdir(parents=True, exist_ok=True)`. The save helper RETURNS the `Path`.
- **`make_real_llm_client()` can raise `RuntimeError`** (missing `LLM_PROVIDER` etc.) — the `/spec-task` handler must catch it and render a config message, never a 500.
- **Facet value shape (from slice 1):** `{"status": "filled"|"needs_human"|"na", "content": str, "reason": str}`; `FACET_KEYS` fixed order: input, auth_permission, business_rule, database, response, error_cases, non_functional, test_cases.
- **Branch first:** create `feat/single-task-spec-mode` before the first commit.

## File Structure

**New**
- `src/ai_dev_system/task_graph/single_task.py` — `build_single_task`, `spec_single_task`.
- `tests/unit/task_graph/test_single_task.py`
- `tests/unit/test_webui_task_spec.py` — `_render_task_spec` + `_save_task_spec` + `_spec_task`.

**Modified**
- `src/ai_dev_system/webui.py` — `_render_task_spec`, `_save_task_spec`, `_spec_task`, home form card, `/spec-task` POST route.

---

## Task 1: Core — `single_task.py`

**Files:**
- Create: `src/ai_dev_system/task_graph/single_task.py`
- Test: `tests/unit/task_graph/test_single_task.py`

**Interfaces:**
- Consumes: `generate_task_facets`, `is_implementation_task` from `ai_dev_system.task_graph.facets`.
- Produces:
  - `build_single_task(idea: str, *, title: str | None = None) -> dict` — minimal atomic coding task (`id="TASK-ADHOC"`, `type="coding"`, `execution_type="atomic"`, `objective`/`description` = idea, `required_inputs=[]`, `expected_outputs=[]`).
  - `spec_single_task(idea: str, llm, *, title: str | None = None) -> dict` — returns `{"task": <task with task["facets"] set>, "facets": <8-facet dict>}`. One LLM call.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/task_graph/test_single_task.py`:

```python
import json

from ai_dev_system.task_graph.single_task import build_single_task, spec_single_task
from ai_dev_system.task_graph.facets import FACET_KEYS, is_implementation_task
from ai_dev_system.debate.llm import StubDebateLLMClient


class _FakeLLM:
    def __init__(self, response): self.response = response
    def complete(self, system, user): return self.response


def _all_filled():
    return json.dumps({k: {"status": "filled", "content": f"{k} c", "reason": ""} for k in FACET_KEYS})


def test_build_single_task_is_minimal_coding_task():
    t = build_single_task("build a CSV importer")
    assert t["type"] == "coding" and t["execution_type"] == "atomic"
    assert t["objective"] == "build a CSV importer"
    assert is_implementation_task(t) is True


def test_build_single_task_title_derives_from_idea_when_absent():
    t = build_single_task("build a CSV importer")
    assert t["title"]  # non-empty
    t2 = build_single_task("x", title="My Task")
    assert t2["title"] == "My Task"


def test_spec_single_task_returns_task_and_eight_facets():
    result = spec_single_task("build a CSV importer", _FakeLLM(_all_filled()))
    assert set(result["facets"].keys()) == set(FACET_KEYS)
    assert result["task"]["facets"]["database"]["status"] == "filled"
    assert result["task"]["objective"] == "build a CSV importer"


def test_spec_single_task_stub_yields_all_needs_human():
    result = spec_single_task("build a CSV importer", StubDebateLLMClient())
    assert all(result["facets"][k]["status"] == "needs_human" for k in FACET_KEYS)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONUTF8=1 python -m pytest tests/unit/task_graph/test_single_task.py -q`
Expected: FAIL — `ModuleNotFoundError: ...task_graph.single_task`.

- [ ] **Step 3: Write the implementation**

Create `src/ai_dev_system/task_graph/single_task.py`:

```python
"""Standalone single-task spec: free text → minimal coding task → 8 facets.

Reuses the slice-1 facet engine (`task_graph.facets`). No project context, no
task graph, no execution — just enough to produce a facet-complete TaskSpec for
one ad-hoc task. One LLM call (the facets).
"""
from __future__ import annotations

from ai_dev_system.task_graph.facets import generate_task_facets

_ADHOC_ID = "TASK-ADHOC"
_TITLE_MAX = 60


def build_single_task(idea: str, *, title: str | None = None) -> dict:
    idea = (idea or "").strip()
    derived = title or (idea[:_TITLE_MAX].rstrip() + ("…" if len(idea) > _TITLE_MAX else ""))
    return {
        "id": _ADHOC_ID,
        "title": derived or "Ad-hoc task",
        "objective": idea,
        "description": idea,
        "type": "coding",
        "execution_type": "atomic",
        "required_inputs": [],
        "expected_outputs": [],
    }


def spec_single_task(idea: str, llm, *, title: str | None = None) -> dict:
    """-> {"task": <task with .facets>, "facets": <8-facet dict>}. One LLM call."""
    task = build_single_task(idea, title=title)
    facets = generate_task_facets(task, {}, None, llm)
    task["facets"] = facets
    return {"task": task, "facets": facets}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONUTF8=1 python -m pytest tests/unit/task_graph/test_single_task.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git switch -c feat/single-task-spec-mode   # first commit only
git add src/ai_dev_system/task_graph/single_task.py tests/unit/task_graph/test_single_task.py
git commit -m "feat: single-task spec core (free text -> minimal coding task -> 8 facets)"
```

---

## Task 2: Webui render + save helpers

**Files:**
- Modify: `src/ai_dev_system/webui.py` (add `_render_task_spec`, `_save_task_spec`)
- Test: `tests/unit/test_webui_task_spec.py`

**Interfaces:**
- Consumes: `FACET_KEYS` from `ai_dev_system.task_graph.facets`.
- Produces:
  - `_render_task_spec(task: dict, facets: dict) -> str` — an HTML card; `filled` → `key: content`, `na` → `key: N/A — reason`, `needs_human` → `key: (cần làm rõ)`. All values `html.escape`d.
  - `_save_task_spec(task: dict, facets: dict, *, storage_root: str) -> Path` — writes `{task, facets}` JSON to `<storage_root>/task_specs/<slug>-<uuid8>.json`, returns the Path.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_webui_task_spec.py`:

```python
import json

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


def test_save_writes_json_and_returns_path(tmp_path):
    facets = _facets()
    task = {"id": "TASK-ADHOC", "title": "My Task", "objective": "do x", "facets": facets}
    path = webui._save_task_spec(task, facets, storage_root=str(tmp_path))
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["task"]["title"] == "My Task"
    assert set(data["facets"].keys()) == set(FACET_KEYS)
    assert path.parent.name == "task_specs"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONUTF8=1 python -m pytest tests/unit/test_webui_task_spec.py -q`
Expected: FAIL — `_render_task_spec` / `_save_task_spec` not defined.

- [ ] **Step 3: Implement**

In `src/ai_dev_system/webui.py`, add imports near the top (with the existing imports):

```python
import re
import uuid
from ai_dev_system.task_graph.facets import FACET_KEYS
```

Add the two helpers (near `_render_report`):

```python
def _render_task_spec(task: dict, facets: dict) -> str:
    rows = []
    for key in FACET_KEYS:
        f = facets.get(key) or {"status": "needs_human", "content": "", "reason": ""}
        status = f.get("status")
        if status == "filled" and f.get("content"):
            val = html.escape(str(f["content"]))
        elif status == "na":
            val = f"<span class='muted'>N/A — {html.escape(str(f.get('reason') or ''))}</span>"
        else:  # needs_human (or empty filled)
            val = "<span class='caveat'>(cần làm rõ)</span>"
        rows.append(f"<tr><td class='muted'>{html.escape(key)}</td><td>{val}</td></tr>")
    title = html.escape(str(task.get("title") or "Task"))
    return (
        f"<div class='card'><h2>Task spec · {title}</h2>"
        "<table>" + "".join(rows) + "</table></div>"
    )


def _save_task_spec(task: dict, facets: dict, *, storage_root: str) -> Path:
    out_dir = Path(storage_root) / "task_specs"
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", str(task.get("title") or "task").lower()).strip("-")[:40] or "task"
    path = out_dir / f"{slug}-{uuid.uuid4().hex[:8]}.json"
    path.write_text(
        json.dumps({"task": task, "facets": facets}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONUTF8=1 python -m pytest tests/unit/test_webui_task_spec.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/webui.py tests/unit/test_webui_task_spec.py
git commit -m "feat: webui render + save helpers for single-task spec"
```

---

## Task 3: Webui wiring — form + `/spec-task` route + orchestrator

**Files:**
- Modify: `src/ai_dev_system/webui.py` (`_spec_task`, home form card, `do_POST` route)
- Test: `tests/unit/test_webui_task_spec.py` (add `_spec_task` test)

**Interfaces:**
- Consumes: `spec_single_task` (Task 1), `_render_task_spec`/`_save_task_spec` (Task 2), `StubDebateLLMClient`, `make_real_llm_client`.
- Produces: `_spec_task(idea: str, mode: str) -> bytes` (full page); a home form posting to `/spec-task`; a `do_POST` branch for `/spec-task`.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_webui_task_spec.py`:

```python
import types


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 python -m pytest tests/unit/test_webui_task_spec.py::test_spec_task_stub_renders_and_saves -q`
Expected: FAIL — `_spec_task` not defined.

- [ ] **Step 3: Implement**

In `src/ai_dev_system/webui.py`:

(a) Add the orchestrator (near `_start`):

```python
def _spec_task(idea: str, mode: str) -> bytes:
    idea = (idea or "").strip()
    if not idea:
        return _page("task spec", "<div class='card muted'>Nhập mô tả task trước đã. "
                     "<a href='/'>← về trang chủ</a></div>")
    try:
        if mode == "stub":
            from ai_dev_system.debate.llm import StubDebateLLMClient
            llm = StubDebateLLMClient()
        else:
            from ai_dev_system.llm_factory import make_real_llm_client
            llm = make_real_llm_client()
    except RuntimeError as exc:
        return _page("task spec", f"<div class='card muted'>LLM chưa cấu hình: "
                     f"{html.escape(str(exc))}</div>")
    from ai_dev_system.task_graph.single_task import spec_single_task
    result = spec_single_task(idea, llm)
    path = _save_task_spec(result["task"], result["facets"],
                           storage_root=_config().storage_root)
    body = (
        _render_task_spec(result["task"], result["facets"])
        + f"<p class='muted'>Đã lưu: {html.escape(str(path))} · <a href='/'>← trang chủ</a></p>"
    )
    return _page("Task spec", body)
```

(b) In `_home()`, add a second form card and include it in the body. After the existing `form = """..."""` block, add:

```python
    task_form = """
    <div class='card'><h2>Đặc tả 1 task</h2>
    <form method='post' action='/spec-task'>
      <label>Mô tả task</label><textarea name='idea' rows='3' placeholder='Mô tả 1 task/feature...' required></textarea>
      <label>Chế độ LLM</label>
      <select name='mode'>
        <option value='stub'>Stub — tức thì (facet giả = needs_human)</option>
        <option value='max'>Claude Max — thật (~vài chục giây)</option>
      </select>
      <button type='submit'>Sinh TaskSpec →</button>
    </form>
    <p class='muted'>Trả về 8 facet (Input/Auth/Business rule/DB/Response/Error/NFR/Test) cho task.</p></div>
    """
```

and change the return to include it:

```python
    return _page("AI Dev System", form + task_form + table)
```

(c) In `do_POST`, add a branch (after the `/abort` branch, before `/start`):

```python
        elif path == "/spec-task":
            length = int(self.headers.get("Content-Length", "0"))
            form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
            idea = (form.get("idea") or [""])[0].strip()
            mode = (form.get("mode") or ["stub"])[0]
            self._send(_spec_task(idea, mode))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONUTF8=1 python -m pytest tests/unit/test_webui_task_spec.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Full-suite regression**

Run: `PYTHONUTF8=1 python -m pytest tests/unit -q`
Expected: PASS. If a webui test asserts the exact home-page body and now fails because the second form card was added, update it minimally to tolerate the new card.

- [ ] **Step 6: Commit**

```bash
git add src/ai_dev_system/webui.py tests/unit/test_webui_task_spec.py
git commit -m "feat: webui single-task spec form + /spec-task route"
```

---

## Manual verification (after all tasks)

1. Restart the webui (`PYTHONUTF8=1 python -m ai_dev_system.webui`), open http://localhost:8765.
2. In "Đặc tả 1 task", paste e.g. "API endpoint to upload a CSV of users", choose **Claude Max**, submit.
3. Confirm the 8 facets render with concrete content (Input/Auth/DB/Response/Error/etc.), and a TaskSpec JSON is saved under `storage/task_specs/`.
4. Repeat in **Stub** mode → all facets show "(cần làm rõ)" instantly, no crash.

## Self-Review

- [ ] **Spec coverage:** core `build_single_task`/`spec_single_task` (T1), render+save (T2), form+route+orchestrator (T3). Spec §2.1–§2.3 mapped. Non-goals (execution, interactive review, profile flavoring) correctly excluded.
- [ ] **Placeholder scan:** none — every step has concrete code/commands.
- [ ] **Type consistency:** `build_single_task`/`spec_single_task` return shapes, the facet dict `{status,content,reason}`, `FACET_KEYS`, `_render_task_spec`/`_save_task_spec`/`_spec_task` signatures consistent across T1–T3.
