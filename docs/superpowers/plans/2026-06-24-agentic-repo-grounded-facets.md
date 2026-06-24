# Agentic Repo-Grounded Facets (Level B) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the user gives a repo path, generate the 8 task facets by letting `claude -p` read that repo (read-only) and ground each facet in real code — run in the background so the webui doesn't block.

**Architecture:** A new `facets_agentic.py` shells the `claude` CLI in read-only, non-interactive mode (`cwd=repo`) and parses its JSON. `single_task.spec_single_task` routes to it when a `repo_path` is given (else the slice-2 text path). A detached worker runs it in the background, writing a status file the webui polls.

**Tech Stack:** Python 3.12, stdlib (`subprocess`, `json`, `argparse`, `uuid`, `pathlib`), `pytest`.

## Global Constraints

- **Python 3.12**; run tests with `PYTHONUTF8=1 python -m pytest ...`. Tests MUST NOT invoke the real `claude` CLI — always inject/mock the subprocess runner.
- **Read-only, non-interactive claude command** (verified flags): `-p <prompt>`, `--output-format json`, `--permission-mode bypassPermissions`, `--disallowedTools Edit Write Bash PowerShell WebFetch WebSearch`, `--max-turns 15`, optional `--model <m>`. Run with subprocess `cwd=<repo>`, `timeout`, `capture_output=True, text=True, encoding="utf-8", errors="replace"`. Resolve the executable via `ClaudeCodeLLMClient._resolve_claude_cmd()` (handles Windows `claude.exe`).
- **Never raise / resilient:** `generate_task_facets_agentic` returns all-`needs_human` on ANY failure — missing/invalid repo path, non-zero exit, `TimeoutExpired`, non-JSON wrapper, non-JSON inner text, missing facet key. Reuse `_all_needs_human()` and `_coerce_facet()` from `task_graph/facets.py`.
- **Defensive output parsing:** `claude -p --output-format json` returns a wrapper whose exact shape is version-dependent. Extract the assistant text defensively: try `wrapper["result"]` (str), else join assistant `content` from `wrapper["messages"]`, else fall back to the raw stdout; then `_strip_outer_code_fence` (from `ClaudeCodeLLMClient`) and `json.loads`. Any step failing → all-`needs_human`.
- **Facet shape (slice 1):** `{"status": "filled"|"needs_human"|"na", "content": str, "reason": str}`; `FACET_KEYS` fixed order: input, auth_permission, business_rule, database, response, error_cases, non_functional, test_cases.
- **Honesty:** the prompt instructs the agent to mark a facet `needs_human` when it finds no code evidence, and to ignore `.env`/secrets/`node_modules`/build output.
- **Async only for repo mode:** when `repo_path` is given, the webui spawns a DETACHED worker (Windows `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`, else `start_new_session`) and redirects to a polling page. The no-repo (slice-2 text) path stays synchronous and unchanged.
- **Branch first:** create `feat/agentic-repo-facets` before the first commit.

## File Structure

**New**
- `src/ai_dev_system/task_graph/facets_agentic.py` — `generate_task_facets_agentic`, command builder, output parser.
- `src/ai_dev_system/task_graph/single_task_worker.py` — `run_worker` + `main()` (argparse).
- `tests/unit/task_graph/test_facets_agentic.py`, `tests/unit/task_graph/test_single_task_worker.py`, additions to `tests/unit/test_webui_task_spec.py`.

**Modified**
- `src/ai_dev_system/task_graph/single_task.py` — `repo_path` routing in `spec_single_task`.
- `src/ai_dev_system/webui.py` — repo field on the form, async `/spec-task` for repo mode, `_task_spec_page` + `/task-spec` route.

---

## Task 1: `facets_agentic.py` — agentic facet generation

**Files:**
- Create: `src/ai_dev_system/task_graph/facets_agentic.py`
- Test: `tests/unit/task_graph/test_facets_agentic.py`

**Interfaces:**
- Consumes: `FACET_KEYS`, `FACET_DEFINITIONS`, `_coerce_facet`, `_all_needs_human` from `ai_dev_system.task_graph.facets`; `ClaudeCodeLLMClient._resolve_claude_cmd` / `_strip_outer_code_fence` from `ai_dev_system.llm_factory`.
- Produces: `generate_task_facets_agentic(task: dict, repo_path: str, *, model: str | None = None, timeout: int = 300, run=subprocess.run) -> dict[str, dict]` — never raises.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/task_graph/test_facets_agentic.py`:

```python
import json
import subprocess

from ai_dev_system.task_graph.facets_agentic import generate_task_facets_agentic, _build_command
from ai_dev_system.task_graph.facets import FACET_KEYS


def _task():
    return {"id": "TASK-ADHOC", "objective": "add CSV import", "description": "...",
            "type": "coding", "execution_type": "atomic",
            "required_inputs": [], "expected_outputs": []}


def _wrapper(inner: str):
    # mimic `claude -p --output-format json` wrapper
    return json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": inner})


def _ok_inner():
    return json.dumps({k: {"status": "filled", "content": f"{k} c", "reason": ""} for k in FACET_KEYS})


class _FakeRun:
    def __init__(self, completed): self.completed = completed; self.calls = []
    def __call__(self, cmd, **kw): self.calls.append((cmd, kw)); return self.completed


def _cp(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(args=["claude"], returncode=returncode, stdout=stdout, stderr=stderr)


def test_happy_path_parses_facets(tmp_path):
    run = _FakeRun(_cp(stdout=_wrapper(_ok_inner())))
    facets = generate_task_facets_agentic(_task(), str(tmp_path), run=run)
    assert set(facets.keys()) == set(FACET_KEYS)
    assert facets["database"]["status"] == "filled"


def test_command_is_read_only_and_uses_repo_cwd(tmp_path):
    run = _FakeRun(_cp(stdout=_wrapper(_ok_inner())))
    generate_task_facets_agentic(_task(), str(tmp_path), run=run)
    cmd, kw = run.calls[0]
    assert kw["cwd"] == str(tmp_path)
    assert "--permission-mode" in cmd and "bypassPermissions" in cmd
    assert "--disallowedTools" in cmd
    for banned in ("Edit", "Write", "Bash"):
        assert banned in cmd
    assert "-p" in cmd


def test_missing_repo_path_yields_needs_human_without_running():
    run = _FakeRun(_cp(stdout=_wrapper(_ok_inner())))
    facets = generate_task_facets_agentic(_task(), "/no/such/dir", run=run)
    assert all(facets[k]["status"] == "needs_human" for k in FACET_KEYS)
    assert run.calls == []  # never ran the subprocess


def test_nonzero_exit_yields_needs_human(tmp_path):
    run = _FakeRun(_cp(stdout="", returncode=1, stderr="boom"))
    facets = generate_task_facets_agentic(_task(), str(tmp_path), run=run)
    assert all(facets[k]["status"] == "needs_human" for k in FACET_KEYS)


def test_timeout_yields_needs_human(tmp_path):
    def _raise(cmd, **kw): raise subprocess.TimeoutExpired(cmd, 1)
    facets = generate_task_facets_agentic(_task(), str(tmp_path), run=_raise)
    assert all(facets[k]["status"] == "needs_human" for k in FACET_KEYS)


def test_non_json_wrapper_yields_needs_human(tmp_path):
    run = _FakeRun(_cp(stdout="not json at all"))
    facets = generate_task_facets_agentic(_task(), str(tmp_path), run=run)
    assert all(facets[k]["status"] == "needs_human" for k in FACET_KEYS)


def test_inner_non_json_yields_needs_human(tmp_path):
    run = _FakeRun(_cp(stdout=_wrapper("the database uses postgres")))  # inner is prose, not JSON
    facets = generate_task_facets_agentic(_task(), str(tmp_path), run=run)
    assert all(facets[k]["status"] == "needs_human" for k in FACET_KEYS)


def test_missing_facet_key_becomes_needs_human(tmp_path):
    inner = json.dumps({"input": {"status": "filled", "content": "c", "reason": ""}})
    run = _FakeRun(_cp(stdout=_wrapper(inner)))
    facets = generate_task_facets_agentic(_task(), str(tmp_path), run=run)
    assert facets["input"]["status"] == "filled"
    assert facets["response"]["status"] == "needs_human"


def test_build_command_includes_model_when_given(tmp_path):
    cmd = _build_command("claude", "PROMPT", model="opus")
    assert "--model" in cmd and "opus" in cmd
    assert cmd[0] == "claude" and "-p" in cmd
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONUTF8=1 python -m pytest tests/unit/task_graph/test_facets_agentic.py -q`
Expected: FAIL — `ModuleNotFoundError: ...facets_agentic`.

- [ ] **Step 3: Write the implementation**

Create `src/ai_dev_system/task_graph/facets_agentic.py`:

```python
"""Agentic, repo-grounded facet generation (Level B).

Runs the `claude` CLI in read-only, non-interactive mode with the target repo as
cwd, letting it Read/Grep/Glob the actual code to ground each facet. Never raises:
any failure yields all-`needs_human`. Tests inject `run` (a subprocess.run-like
callable); the real `claude` CLI is never invoked under test.
"""
from __future__ import annotations

import json
import os
import subprocess

from ai_dev_system.task_graph.facets import (
    FACET_KEYS,
    FACET_DEFINITIONS,
    _all_needs_human,
    _coerce_facet,
)
from ai_dev_system.llm_factory import ClaudeCodeLLMClient

# Read-only, non-interactive flags (verified against Claude Code CLI).
_READONLY_FLAGS = [
    "--output-format", "json",
    "--permission-mode", "bypassPermissions",
    "--disallowedTools", "Edit", "Write", "Bash", "PowerShell", "WebFetch", "WebSearch",
    "--max-turns", "15",
]


def _build_prompt(task: dict) -> str:
    facet_lines = "\n".join(f"- {k}: {FACET_DEFINITIONS[k]}" for k in FACET_KEYS)
    return (
        "You are detailing ONE implementation task against THIS repository. Use "
        "Read/Grep/Glob to inspect the actual code relevant to the task (data "
        "models, schema/migrations, the modules this task touches). For each of the "
        "8 engineering facets below, write a concrete, code-grounded detail and cite "
        "the file path(s) you used. Mark a facet \"na\" (with a reason) when "
        "irrelevant, or \"needs_human\" when you find NO evidence in the code — do "
        "NOT invent. Ignore .env, secrets, node_modules, and build output.\n\n"
        f"TASK:\n- objective: {task.get('objective', '')}\n"
        f"- description: {task.get('description', '')}\n\n"
        "Return ONLY a JSON object keyed by the 8 facet names; each value is "
        '{"status": "filled"|"na"|"needs_human", "content": "...", "reason": "..."}.\n'
        "Facets:\n" + facet_lines
    )


def _build_command(claude: str, prompt: str, *, model: str | None = None) -> list[str]:
    cmd = [claude, "-p", prompt, *_READONLY_FLAGS]
    if model:
        cmd += ["--model", model]
    return cmd


def _extract_text(stdout: str) -> str:
    """Pull the assistant text out of the --output-format json wrapper, defensively."""
    wrapper = json.loads(stdout)  # may raise → caller catches
    if isinstance(wrapper, dict):
        result = wrapper.get("result")
        if isinstance(result, str):
            return result
        messages = wrapper.get("messages")
        if isinstance(messages, list):
            parts = []
            for m in messages:
                c = m.get("content") if isinstance(m, dict) else None
                if isinstance(c, str):
                    parts.append(c)
            if parts:
                return "\n".join(parts)
    # Unknown shape — fall back to the raw stdout (maybe it was already the JSON).
    return stdout


def generate_task_facets_agentic(
    task: dict,
    repo_path: str,
    *,
    model: str | None = None,
    timeout: int = 300,
    run=subprocess.run,
) -> dict[str, dict]:
    """8 facets grounded in the repo at `repo_path`, via read-only `claude -p`.
    Never raises — any failure returns all-`needs_human`."""
    if not repo_path or not os.path.isdir(repo_path):
        return _all_needs_human()
    try:
        claude = ClaudeCodeLLMClient._resolve_claude_cmd()
        cmd = _build_command(claude, _build_prompt(task), model=model)
        proc = run(
            cmd, cwd=repo_path, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        if proc.returncode != 0:
            return _all_needs_human()
        text = ClaudeCodeLLMClient._strip_outer_code_fence(_extract_text(proc.stdout))
        data = json.loads(text)
    except Exception:
        return _all_needs_human()
    if not isinstance(data, dict):
        return _all_needs_human()
    return {k: _coerce_facet(data.get(k)) for k in FACET_KEYS}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONUTF8=1 python -m pytest tests/unit/task_graph/test_facets_agentic.py -q`
Expected: PASS (9 passed).

- [ ] **Step 5: Commit**

```bash
git switch -c feat/agentic-repo-facets   # first commit only
git add src/ai_dev_system/task_graph/facets_agentic.py tests/unit/task_graph/test_facets_agentic.py
git commit -m "feat: agentic repo-grounded facet generation (read-only claude -p)"
```

---

## Task 2: `single_task.py` — route to agentic when `repo_path` given

**Files:**
- Modify: `src/ai_dev_system/task_graph/single_task.py`
- Test: `tests/unit/task_graph/test_single_task.py` (add cases)

**Interfaces:**
- Consumes: `generate_task_facets_agentic` (Task 1), existing `generate_task_facets` (slice 1).
- Produces: `spec_single_task(idea, llm, *, title=None, repo_path=None) -> {"task", "facets"}` — `repo_path` set → agentic (ignores `llm`); else slice-2 text path.

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/task_graph/test_single_task.py`:

```python
def test_spec_single_task_uses_agentic_when_repo_given(monkeypatch):
    import ai_dev_system.task_graph.single_task as st
    called = {}
    def _fake_agentic(task, repo_path, **kw):
        called["repo"] = repo_path
        from ai_dev_system.task_graph.facets import FACET_KEYS
        return {k: {"status": "filled", "content": "x", "reason": ""} for k in FACET_KEYS}
    monkeypatch.setattr(st, "generate_task_facets_agentic", _fake_agentic)
    result = st.spec_single_task("add CSV import", None, repo_path="/some/repo")
    assert called["repo"] == "/some/repo"
    assert result["task"]["facets"]["input"]["status"] == "filled"


def test_spec_single_task_uses_text_path_when_no_repo():
    from ai_dev_system.debate.llm import StubDebateLLMClient
    from ai_dev_system.task_graph.facets import FACET_KEYS
    result = spec_single_task("add CSV import", StubDebateLLMClient())  # no repo_path
    assert all(result["facets"][k]["status"] == "needs_human" for k in FACET_KEYS)  # stub Mode A
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONUTF8=1 python -m pytest tests/unit/task_graph/test_single_task.py -q`
Expected: FAIL — `spec_single_task()` got an unexpected keyword `repo_path`.

- [ ] **Step 3: Implement**

In `src/ai_dev_system/task_graph/single_task.py`, add the import and route:

```python
from ai_dev_system.task_graph.facets import generate_task_facets
from ai_dev_system.task_graph.facets_agentic import generate_task_facets_agentic


def spec_single_task(idea: str, llm, *, title: str | None = None, repo_path: str | None = None) -> dict:
    """-> {"task": <task with .facets>, "facets": <8-facet dict>}.

    repo_path set → agentic, repo-grounded facets (llm unused).
    else → text/spec facets via `llm` (slice-2 path).
    """
    task = build_single_task(idea, title=title)
    if repo_path:
        facets = generate_task_facets_agentic(task, repo_path)
    else:
        facets = generate_task_facets(task, {}, None, llm)
    task["facets"] = facets
    return {"task": task, "facets": facets}
```

(Keep `build_single_task` unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONUTF8=1 python -m pytest tests/unit/task_graph/test_single_task.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/task_graph/single_task.py tests/unit/task_graph/test_single_task.py
git commit -m "feat: route single-task spec to agentic facets when repo_path given"
```

---

## Task 3: `single_task_worker.py` — background worker

**Files:**
- Create: `src/ai_dev_system/task_graph/single_task_worker.py`
- Test: `tests/unit/task_graph/test_single_task_worker.py`

**Interfaces:**
- Consumes: `spec_single_task` (Task 2).
- Produces: `run_worker(spec_id: str, idea: str, repo: str | None, *, storage_root: str) -> Path` — writes `<storage_root>/task_specs/<spec_id>.json` with `{"status":"done","task","facets"}` (or `{"status":"error","error":...}` on failure), returns the Path. `main(argv=None)` — argparse CLI.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/task_graph/test_single_task_worker.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 python -m pytest tests/unit/task_graph/test_single_task_worker.py -q`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

Create `src/ai_dev_system/task_graph/single_task_worker.py`:

```python
"""Background worker: produce a single-task TaskSpec and write a status file.

Spawned detached by the webui for repo-grounded (agentic) specs so the HTTP
request doesn't block. Writes <storage_root>/task_specs/<id>.json with a status.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ai_dev_system.task_graph.single_task import spec_single_task
from ai_dev_system.llm_factory import make_real_llm_client


def run_worker(spec_id: str, idea: str, repo: str | None, *, storage_root: str) -> Path:
    out_dir = Path(storage_root) / "task_specs"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{spec_id}.json"
    try:
        # repo mode → agentic (llm unused); no repo → text path needs a real client.
        llm = None if repo else make_real_llm_client()
        result = spec_single_task(idea, llm, repo_path=repo)
        payload = {"status": "done", "idea": idea, "repo": repo,
                   "task": result["task"], "facets": result["facets"]}
    except Exception as exc:  # noqa: BLE001
        payload = {"status": "error", "idea": idea, "repo": repo, "error": str(exc)}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--id", required=True)
    p.add_argument("--idea", required=True)
    p.add_argument("--repo", default=None)
    p.add_argument("--storage-root", required=True)
    args = p.parse_args(argv)
    run_worker(args.id, args.idea, args.repo or None, storage_root=args.storage_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONUTF8=1 python -m pytest tests/unit/task_graph/test_single_task_worker.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/task_graph/single_task_worker.py tests/unit/task_graph/test_single_task_worker.py
git commit -m "feat: background worker for single-task spec (status file)"
```

---

## Task 4: Webui — repo field, async spawn, polling page

**Files:**
- Modify: `src/ai_dev_system/webui.py`
- Test: `tests/unit/test_webui_task_spec.py` (add cases)

**Interfaces:**
- Consumes: `_render_task_spec`/`_save_task_spec` (slice 2), `single_task_worker` (Task 3), `_config`.
- Produces: `_task_spec_page(spec_id: str) -> bytes` (renders from the status file); `_spawn_task_spec_worker(idea: str, repo: str) -> str` (writes running file, spawns detached worker, returns spec_id); a `/task-spec` GET route + repo-aware `/spec-task` POST.

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_webui_task_spec.py`:

```python
import json as _json
import types as _types


def test_task_spec_page_running(tmp_path, monkeypatch):
    monkeypatch.setattr(webui, "_config", lambda: _types.SimpleNamespace(storage_root=str(tmp_path)))
    d = tmp_path / "task_specs"; d.mkdir()
    (d / "id1.json").write_text(_json.dumps({"status": "running", "idea": "x"}), encoding="utf-8")
    page = webui._task_spec_page("id1").decode("utf-8")
    assert "đang chạy" in page.lower() or "running" in page.lower()
    assert "http-equiv" in page  # auto-refresh while running


def test_task_spec_page_done_renders_facets(tmp_path, monkeypatch):
    monkeypatch.setattr(webui, "_config", lambda: _types.SimpleNamespace(storage_root=str(tmp_path)))
    from ai_dev_system.task_graph.facets import FACET_KEYS
    facets = {k: {"status": "filled", "content": f"{k} c", "reason": ""} for k in FACET_KEYS}
    d = tmp_path / "task_specs"; d.mkdir()
    (d / "id2.json").write_text(_json.dumps(
        {"status": "done", "task": {"title": "My Task"}, "facets": facets}), encoding="utf-8")
    page = webui._task_spec_page("id2").decode("utf-8")
    assert "Task spec" in page and "input c" in page


def test_task_spec_page_error(tmp_path, monkeypatch):
    monkeypatch.setattr(webui, "_config", lambda: _types.SimpleNamespace(storage_root=str(tmp_path)))
    d = tmp_path / "task_specs"; d.mkdir()
    (d / "id3.json").write_text(_json.dumps({"status": "error", "error": "kaboom"}), encoding="utf-8")
    page = webui._task_spec_page("id3").decode("utf-8")
    assert "kaboom" in page


def test_task_spec_page_unknown_id(tmp_path, monkeypatch):
    monkeypatch.setattr(webui, "_config", lambda: _types.SimpleNamespace(storage_root=str(tmp_path)))
    page = webui._task_spec_page("nope")
    assert isinstance(page, (bytes, bytearray))  # no crash on missing file
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONUTF8=1 python -m pytest tests/unit/test_webui_task_spec.py -q`
Expected: FAIL — `_task_spec_page` not defined.

- [ ] **Step 3: Implement**

In `src/ai_dev_system/webui.py`:

(a) Add the polling-page renderer + spawner (near `_render_task_spec`):

```python
def _task_spec_page(spec_id: str) -> bytes:
    path = Path(_config().storage_root) / "task_specs" / f"{spec_id}.json"
    if not path.exists():
        return _page("task spec", "<div class='card muted'>Không tìm thấy TaskSpec. "
                     "<a href='/'>← trang chủ</a></div>")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return _page("task spec", f"<div class='card muted'>Lỗi đọc TaskSpec: {html.escape(str(exc))}</div>")
    status = data.get("status")
    if status == "done":
        return _page("Task spec", _render_task_spec(data.get("task") or {}, data.get("facets") or {})
                     + "<p class='muted'><a href='/'>← trang chủ</a></p>")
    if status == "error":
        return _page("task spec", "<div class='card muted'>Lỗi sinh TaskSpec: "
                     f"{html.escape(str(data.get('error') or ''))}</div>")
    # running
    return _page("task spec",
                 "<div class='card'><h2>Đang sinh TaskSpec (agentic đọc repo)…</h2>"
                 "<p class='muted'>Trang tự refresh mỗi 5s.</p></div>",
                 head_extra="<meta http-equiv='refresh' content='5'>")


def _spawn_task_spec_worker(idea: str, repo: str) -> str:
    spec_id = uuid.uuid4().hex[:12]
    out_dir = Path(_config().storage_root) / "task_specs"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{spec_id}.json").write_text(
        json.dumps({"status": "running", "idea": idea, "repo": repo}, ensure_ascii=False),
        encoding="utf-8")
    popen_kwargs: dict = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True
    subprocess.Popen(
        [sys.executable, "-m", "ai_dev_system.task_graph.single_task_worker",
         "--id", spec_id, "--idea", idea, "--repo", repo,
         "--storage-root", str(_config().storage_root)],
        cwd=str(Path(__file__).resolve().parents[2]), **popen_kwargs,
    )
    return spec_id
```

(b) In `_home()`, add a repo field to the task form (inside the existing `task_form` string, after the idea textarea / before the mode select):

```python
      <label>Đường dẫn repo (tuỳ chọn — bật agentic đọc code thật)</label>
      <input name='repo' placeholder='vd: E:\\Work\\my-app'>
```

(c) In `do_POST`, update the `/spec-task` branch to read `repo` and branch async vs sync:

```python
        elif path == "/spec-task":
            length = int(self.headers.get("Content-Length", "0"))
            form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
            idea = (form.get("idea") or [""])[0].strip()
            mode = (form.get("mode") or ["stub"])[0]
            repo = (form.get("repo") or [""])[0].strip()
            if repo:
                spec_id = _spawn_task_spec_worker(idea, repo)
                self._send(_page("task spec",
                    "<div class='card'><h2>Đã khởi động (agentic) ✓</h2>"
                    f"<p class='muted'>Đọc repo + sinh facet ở chạy nền.</p></div>",
                    head_extra=f"<meta http-equiv='refresh' content='2;url=/task-spec?id={urllib.parse.quote(spec_id)}'>"))
            else:
                self._send(_spec_task(idea, mode))
```

(d) In `do_GET`, add a `/task-spec` route (next to the existing `/run` route):

```python
            elif parsed.path == "/task-spec":
                qs = urllib.parse.parse_qs(parsed.query)
                self._send(_task_spec_page((qs.get("id") or [""])[0]))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONUTF8=1 python -m pytest tests/unit/test_webui_task_spec.py -q`
Expected: PASS.

- [ ] **Step 5: Full-suite regression**

Run: `PYTHONUTF8=1 python -m pytest tests/unit -q`
Expected: PASS. If a webui home-page test asserts the exact form body and fails only because the `repo` field was added, update it minimally.

- [ ] **Step 6: Commit**

```bash
git add src/ai_dev_system/webui.py tests/unit/test_webui_task_spec.py
git commit -m "feat: webui async agentic single-task spec (repo field + /task-spec polling)"
```

---

## Manual verification (after all tasks)

1. Restart the webui; open http://localhost:8765, "Đặc tả 1 task".
2. Paste a task + a **real repo path** (e.g. this repo), submit → redirected to `/task-spec?id=...` showing "Đang sinh…", auto-refreshing.
3. After the agentic run, the page shows the 8 facets with **code-grounded** content (file paths cited), or `needs_human` where no evidence; the TaskSpec JSON is in `storage/task_specs/<id>.json`.
4. Leave the repo field blank → the existing synchronous text path (stub/Max) still works.

## Self-Review

- [ ] **Spec coverage:** agentic core + read-only flags + defensive parse + resilience (T1), repo routing (T2), background worker + status file (T3), webui repo field + async spawn + polling page (T4). Spec §2–§3 mapped. Non-goals (multi-repo, retrieval, execute, orphan-reconcile) excluded.
- [ ] **Placeholder scan:** none — every step has concrete code/commands. Version-dependent details (wrapper shape) are handled defensively in `_extract_text`, not deferred.
- [ ] **Type consistency:** `generate_task_facets_agentic(task, repo_path, *, model, timeout, run)`, `_build_command(claude, prompt, *, model)`, `spec_single_task(..., repo_path=None)`, `run_worker(spec_id, idea, repo, *, storage_root) -> Path`, `_task_spec_page(spec_id) -> bytes`, `_spawn_task_spec_worker(idea, repo) -> str`, and the facet `{status,content,reason}` shape are consistent across T1–T4.
```
