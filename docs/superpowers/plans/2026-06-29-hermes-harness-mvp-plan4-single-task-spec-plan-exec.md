# Plan 4 — Single-task: spec → plan → exec (reviewable plan) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the single-task `TASK-TEST → TASK-IMPL` plan a separate, **reviewable, persisted artifact** that the operator approves before execution — instead of the executor secretly rebuilding it at run time.

**Architecture:** Extract the deterministic `_build_task_graph` out of `single_task_executor.run_executor` into a new `task_graph/single_task_plan.py` (`plan_single_task` builds + persists `task_specs/<spec_id>-plan.json`). `run_executor` no longer builds the graph — it **loads the approved plan and refuses to run an unapproved/missing plan** (the gate). The WebUI inserts a `/task-plan` review page between spec-approval and execution: approve → spawn executor; revise → back to spec. Plan generation is **deterministic (no LLM)**, so it runs synchronously in the POST handler — no detached worker.

**Tech Stack:** Python 3, stdlib `http.server` WebUI, file-based artifacts under `storage_root/task_specs/`, SQLite (unchanged), pytest.

**Scope (locked with operator 2026-06-29):** Option A — the targeted refactor + WebUI plan-review parity only. **NOT in this plan:** the `dev_singletask_*` harness/Telegram tools (front-door), spec self-review critic (Plan 6), the notifier (Plan 5). New-project (Phase B) flow is untouched.

## Global Constraints

- **The gate is load-bearing:** `run_executor` MUST refuse to execute when the plan file is missing or `approved` is not `True` — it writes an error status and returns. No fallback to rebuilding the graph.
- **Plan artifact path:** `<storage_root>/task_specs/<spec_id>-plan.json`, mirroring the existing `<spec_id>.json` / `<spec_id>-exec.json` convention. Schema: `{"spec_id","branch","tdd_gate","graph":{"tasks":[...]},"approved":bool,"created_at"}`.
- **Plan generation is deterministic (no LLM, no network, no git):** `branch = f"ai-dev/task-{spec_id[:8]}"`; the task graph is the existing TDD-gate logic verbatim.
- **Backward-compat:** the no-repo (text-only spec, no execution) path is unchanged. `EXEC_TDD_GATE` semantics unchanged (default ON → two tasks; off → single impl task).
- **Do not break existing tests.** These reference the refactor targets and MUST be updated in the same task that moves the symbol: `tests/unit/test_single_task_executor.py` (imports `_build_task_graph`, calls `run_executor`), `tests/integration/test_tdd_first_executor.py` (calls `ste._build_task_graph`). Verify `tests/integration/test_executor_e2e.py` still passes (uses `run_execution`, not `run_executor`).
- **README test-count chore:** every task that adds tests bumps the count in `README.md`; `tests/unit/test_docs_reconciliation.py::test_readme_test_count_matches_collected_count` enforces README == collected count. Set README to the real collected total after running the full suite. New file `single_task_plan.py` is in the existing `task_graph` package → `EXPECTED_PACKAGES` unaffected.
- **No new third-party dependency.** Vietnamese UI strings are fine (files already write UTF-8).

---

### Task 1: `single_task_plan.py` — extract graph builder + persist the plan

**Files:**
- Create: `src/ai_dev_system/task_graph/single_task_plan.py`
- Test: `tests/unit/test_single_task_plan.py`

**Interfaces:**
- Produces (consumed by Task 2 + Task 3):
  - `build_task_graph(task: dict, facets: dict, branch_name: str) -> dict` (the `_build_task_graph` body, **with the unused `base_branch` param dropped**).
  - `branch_name_for(spec_id: str) -> str` → `f"ai-dev/task-{spec_id[:8]}"`.
  - `plan_path(storage_root: str, spec_id: str) -> Path`.
  - `plan_single_task(spec: dict, spec_id: str, *, storage_root: str) -> dict` — builds + writes `<spec_id>-plan.json` with `approved=False`, returns the plan dict.
  - `load_plan(storage_root: str, spec_id: str) -> dict | None`.
  - `approve_plan(storage_root: str, spec_id: str) -> bool` (False if no plan file).

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_single_task_plan.py`:

```python
from __future__ import annotations

import json

import pytest

from ai_dev_system.task_graph.single_task_plan import (
    build_task_graph, branch_name_for, plan_path, plan_single_task,
    load_plan, approve_plan,
)


def _task():
    return {"id": "TASK-ADHOC", "objective": "Add X", "description": "desc",
            "done_definition": "done", "type": "coding"}


def _spec():
    return {"idea": "add X", "repo": "/repo",
            "task": _task(),
            "facets": {"test_cases": {"status": "filled", "content": "t", "reason": ""}}}


def test_branch_name_for_uses_first_8():
    assert branch_name_for("abcdef0123456789") == "ai-dev/task-abcdef01"


def test_build_task_graph_tdd_on_two_tasks_with_dep(monkeypatch):
    monkeypatch.delenv("EXEC_TDD_GATE", raising=False)
    g = build_task_graph(_task(), {"x": 1}, "ai-dev/task-abc")
    ids = [t["id"] for t in g["tasks"]]
    assert ids == ["TASK-ADHOC-TEST", "TASK-ADHOC-IMPL"]
    impl = g["tasks"][1]
    assert impl["deps"] == ["TASK-ADHOC-TEST"]
    assert g["tasks"][0]["agent_type"] == "TestAuthorAgent"
    assert impl["agent_type"] == "RepoBranchAgent"


def test_build_task_graph_tdd_off_single_task(monkeypatch):
    monkeypatch.setenv("EXEC_TDD_GATE", "0")
    g = build_task_graph(_task(), {"x": 1}, "ai-dev/task-abc")
    assert [t["id"] for t in g["tasks"]] == ["TASK-ADHOC"]
    assert g["tasks"][0]["agent_type"] == "RepoBranchAgent"


def test_plan_single_task_persists_unapproved_with_graph(tmp_path, monkeypatch):
    monkeypatch.delenv("EXEC_TDD_GATE", raising=False)
    plan = plan_single_task(_spec(), "spec1234abcd", storage_root=str(tmp_path))
    assert plan["approved"] is False
    assert plan["branch"] == "ai-dev/task-spec1234"
    assert [t["id"] for t in plan["graph"]["tasks"]] == ["TASK-ADHOC-TEST", "TASK-ADHOC-IMPL"]
    on_disk = json.loads(plan_path(str(tmp_path), "spec1234abcd").read_text(encoding="utf-8"))
    assert on_disk == plan


def test_load_plan_missing_returns_none(tmp_path):
    assert load_plan(str(tmp_path), "nope") is None


def test_approve_plan_round_trip(tmp_path):
    plan_single_task(_spec(), "spec1234abcd", storage_root=str(tmp_path))
    assert approve_plan(str(tmp_path), "spec1234abcd") is True
    assert load_plan(str(tmp_path), "spec1234abcd")["approved"] is True


def test_approve_plan_no_file_returns_false(tmp_path):
    assert approve_plan(str(tmp_path), "missing") is False
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `python -m pytest tests/unit/test_single_task_plan.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: ai_dev_system.task_graph.single_task_plan`.

- [ ] **Step 3: Implement `single_task_plan.py`**

Create `src/ai_dev_system/task_graph/single_task_plan.py`:

```python
"""Single-task PLAN step: build the TASK-TEST → TASK-IMPL graph from an approved
spec and persist it as a REVIEWABLE artifact before execution. The executor then
runs the *approved* plan instead of rebuilding the graph at exec time.

Plan file: <storage_root>/task_specs/<spec_id>-plan.json
  {"spec_id","branch","tdd_gate","graph":{"tasks":[...]},"approved":bool,"created_at"}

Deterministic: no LLM, no network, no git — safe to run synchronously in a request.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


def _tdd_gate_enabled() -> bool:
    """TDD-first split is ON unless EXEC_TDD_GATE is explicitly falsy."""
    v = os.environ.get("EXEC_TDD_GATE")
    if v is None:
        return True
    return v.strip().lower() not in ("0", "false", "no", "off", "")


def branch_name_for(spec_id: str) -> str:
    return f"ai-dev/task-{spec_id[:8]}"


def build_task_graph(task: dict, facets: dict, branch_name: str) -> dict:
    """Single-task graph. TDD gate on → TASK-TEST → TASK-IMPL (ordered by deps);
    off → the legacy single impl task."""
    base_id = task.get("id") or "TASK-ADHOC"
    objective = task.get("objective") or ""
    description = task.get("description") or ""
    impl_done = task.get("done_definition") or f"Code committed to branch {branch_name}"

    _gate = _tdd_gate_enabled()
    impl_task = {
        "id": f"{base_id}-IMPL" if _gate else base_id,
        "execution_type": "atomic",
        "agent_type": "RepoBranchAgent",
        "phase": "implementation",
        "type": task.get("type") or "coding",
        "objective": objective,
        "description": description,
        "done_definition": impl_done,
        "verification_steps": [],
        "required_inputs": [],
        "expected_outputs": ["implementation_diff"],
        "deps": [],
        "facets": facets,
        "tdd_tests_authored": _gate,
    }
    if not _gate:
        return {"tasks": [impl_task]}

    test_task = {
        "id": f"{base_id}-TEST",
        "execution_type": "atomic",
        "agent_type": "TestAuthorAgent",
        "phase": "test",
        "type": "test",
        "objective": objective,
        "description": description,
        "done_definition": "Failing tests committed from the acceptance source",
        "verification_steps": [],
        "required_inputs": [],
        "expected_outputs": ["test_files"],
        "deps": [],
        "facets": facets,
    }
    impl_task["deps"] = [test_task["id"]]
    return {"tasks": [test_task, impl_task]}


def plan_path(storage_root: str, spec_id: str) -> Path:
    return Path(storage_root) / "task_specs" / f"{spec_id}-plan.json"


def plan_single_task(spec: dict, spec_id: str, *, storage_root: str) -> dict:
    """Build + persist the reviewable plan for an approved spec. approved=False
    until the operator approves it via approve_plan()."""
    task = spec.get("task") or {}
    facets = spec.get("facets") or {}
    branch = branch_name_for(spec_id)
    plan = {
        "spec_id": spec_id,
        "branch": branch,
        "tdd_gate": _tdd_gate_enabled(),
        "graph": build_task_graph(task, facets, branch),
        "approved": False,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    path = plan_path(storage_root, spec_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    return plan


def load_plan(storage_root: str, spec_id: str) -> dict | None:
    path = plan_path(storage_root, spec_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a corrupt plan reads as "no plan" (gate stays closed)
        return None


def approve_plan(storage_root: str, spec_id: str) -> bool:
    """Mark the persisted plan approved. Returns False if no plan file exists."""
    plan = load_plan(storage_root, spec_id)
    if plan is None:
        return False
    plan["approved"] = True
    plan_path(storage_root, spec_id).write_text(
        json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return True
```

- [ ] **Step 4: Run tests, verify pass**

Run: `python -m pytest tests/unit/test_single_task_plan.py -q -p no:cacheprovider`
Expected: PASS (7 passed).

- [ ] **Step 5: Bump README + full suite + commit**

Bump README test count by the number of new tests (7), then run the full suite and set README to the real collected count.
Run: `python -m pytest -q -p no:cacheprovider` → expected 0 failed.

```bash
git add src/ai_dev_system/task_graph/single_task_plan.py tests/unit/test_single_task_plan.py README.md
git commit -m "feat(single-task): plan_single_task builds + persists a reviewable plan"
```

---

### Task 2: `run_executor` consumes the approved plan (the gate)

**Files:**
- Modify: `src/ai_dev_system/task_graph/single_task_executor.py`
- Modify (keep green): `tests/unit/test_single_task_executor.py`, `tests/integration/test_tdd_first_executor.py`
- Test: `tests/unit/test_single_task_executor.py` (add a gate test)

**Interfaces:**
- Consumes: `single_task_plan.load_plan`, `single_task_plan.branch_name_for` (Task 1).
- Behavior change: `run_executor(spec_id, storage_root, database_url)` now reads `<spec_id>-plan.json`; if missing or `approved!=True`, it writes `{"status":"error","error":...}` and returns **without** creating a run row, branch, or artifact. Otherwise it uses `plan["branch"]` and `plan["graph"]`.

- [ ] **Step 1: Write the failing gate test**

Add to `tests/unit/test_single_task_executor.py`:

```python
def test_run_executor_errors_when_plan_not_approved(tmp_path):
    from ai_dev_system.task_graph.single_task_executor import run_executor
    out = tmp_path / "task_specs"
    out.mkdir(parents=True)
    spec_id = "planless1234"
    (out / f"{spec_id}.json").write_text(json.dumps({
        "status": "done", "idea": "x", "repo": str(tmp_path / "repo"),
        "task": {"id": "TASK-ADHOC", "title": "t"}, "facets": {},
    }), encoding="utf-8")
    # No -plan.json written at all → gate must refuse.
    run_executor(spec_id, str(tmp_path), "sqlite:///:memory:")
    status = json.loads((out / f"{spec_id}-exec.json").read_text(encoding="utf-8"))
    assert status["status"] == "error"
    assert "plan" in status["error"].lower()
```

- [ ] **Step 2: Run it, verify it fails**

Run: `python -m pytest tests/unit/test_single_task_executor.py::test_run_executor_errors_when_plan_not_approved -q -p no:cacheprovider`
Expected: FAIL — current `run_executor` ignores the plan and proceeds (status becomes "running"/"error git", not the plan-gate error). *(It may error for a different reason — the assertion on `"plan" in error` is what must drive the implementation.)*

- [ ] **Step 3: Refactor `run_executor` + remove the old builder**

In `src/ai_dev_system/task_graph/single_task_executor.py`:

(a) **Delete** the `_build_task_graph` function (lines 42–86) — it now lives in `single_task_plan.build_task_graph`.

(b) Add an import near the top (after the existing imports):
```python
from ai_dev_system.task_graph.single_task_plan import load_plan, branch_name_for
```

(c) Replace the branch-name line (currently `branch_name = f"ai-dev/task-{spec_id[:8]}"`, ~line 265) and insert the gate. After the `facets = spec.get("facets") or {}` and the existing `if not repo_path:` error block, add the plan gate **before** any git/branch work:

```python
    # Gate: execute only an APPROVED, persisted plan (built at spec-approval time).
    plan = load_plan(storage_root, spec_id)
    if plan is None or not plan.get("approved"):
        msg = "plan chưa được duyệt" if plan is not None else "chưa có plan đã duyệt"
        _exec_log(log_path, f"LỖI: {msg} — không thể execute")
        _write_exec_status(status_path, {"status": "error", "error": msg})
        return

    branch_name = plan.get("branch") or branch_name_for(spec_id)
```

Then **delete** the old `branch_name = f"ai-dev/task-{spec_id[:8]}"` assignment.

(d) Replace the graph-build call (currently `task_graph = _build_task_graph(task, facets, branch_name, base_branch)`, ~line 311) with:
```python
    task_graph = plan["graph"]
```

(Everything else — git checkout, run row, `_create_task_graph_artifact(conn, run_id, task_graph, storage_root)`, `run_execution` — is unchanged.)

- [ ] **Step 4: Fix the existing tests that referenced moved/changed symbols**

In `tests/unit/test_single_task_executor.py`:
- **Delete** `test_tdd_gate_builds_two_tasks_with_dep` and `test_gate_off_builds_single_task` (they tested `_build_task_graph`, now covered by `test_single_task_plan.py`). *(Do not re-import `_build_task_graph` — it no longer exists.)*
- Update `test_run_executor_creates_exec_log_and_status` so it writes an **approved** plan before calling `run_executor`. Add this right before the `run_executor(...)` call:
```python
    from ai_dev_system.task_graph.single_task_plan import plan_single_task, approve_plan
    plan_single_task(json.loads((out_dir / f"{spec_id}.json").read_text(encoding="utf-8")),
                     spec_id, storage_root=str(tmp_path))
    approve_plan(str(tmp_path), spec_id)
```
*(Use the same `out_dir`/`spec_id` variable names already in that test; if the spec file is written under a different variable, adapt the path. The point: an approved `<spec_id>-plan.json` must exist before `run_executor` runs.)*

In `tests/integration/test_tdd_first_executor.py` (line ~96): change `ste._build_task_graph(` to import and call `build_task_graph` from the new module:
```python
from ai_dev_system.task_graph.single_task_plan import build_task_graph
...
graph = build_task_graph(<task>, <facets>, <branch_name>)   # drop the old base_branch arg
```
*(Read the current call to get the exact args; the 4th `base_branch` argument is removed.)*

- [ ] **Step 5: Run the gate test + both affected test files**

Run:
```
python -m pytest tests/unit/test_single_task_executor.py tests/integration/test_tdd_first_executor.py tests/integration/test_executor_e2e.py -q -p no:cacheprovider
```
Expected: PASS (the new gate test passes; the updated tests pass; e2e unaffected).

- [ ] **Step 6: Bump README + full suite + commit**

Net test delta = +1 gate test −2 deleted builder tests = **−1** (adjust README to the real collected count after the full run).
Run: `python -m pytest -q -p no:cacheprovider` → 0 failed.

```bash
git add src/ai_dev_system/task_graph/single_task_executor.py tests/unit/test_single_task_executor.py tests/integration/test_tdd_first_executor.py README.md
git commit -m "refactor(single-task): run_executor runs the approved plan; gate blocks unapproved/missing plan"
```

---

### Task 3: WebUI plan-review page + routing (spec → plan → review → exec)

**Files:**
- Modify: `src/ai_dev_system/webui.py`
- Test: `tests/unit/test_webui_task_plan.py` (new)

**Interfaces:**
- Consumes: `single_task_plan.plan_single_task`, `load_plan`, `approve_plan` (Task 1).
- Adds: `_task_plan_page(spec_id) -> bytes`; GET `/task-plan`; POST `/task-plan` (action `approve`|`revise`); rewires POST `/task-spec` to build the plan and redirect to `/task-plan` instead of spawning the executor directly.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_webui_task_plan.py`:

```python
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
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `python -m pytest tests/unit/test_webui_task_plan.py -q -p no:cacheprovider`
Expected: FAIL — `_task_plan_page` / `_approve_task_plan_and_exec` don't exist.

- [ ] **Step 3: Implement the page + approve helper + routing**

In `src/ai_dev_system/webui.py`:

(a) Add an import near the other `task_graph` imports at the top:
```python
from ai_dev_system.task_graph.single_task_plan import plan_single_task, load_plan, approve_plan
```

(b) Add the render page + approve helper (place them near `_task_spec_page` / `_spawn_task_executor`):
```python
def _task_plan_page(spec_id: str) -> bytes:
    plan = load_plan(str(_config().storage_root), spec_id)
    if plan is None:
        return _page("plan", "<div class='card muted'>Không tìm thấy plan. "
                     "<a href='/'>← trang chủ</a></div>")
    branch = html.escape(str(plan.get("branch") or ""))
    rows = ""
    for t in plan.get("graph", {}).get("tasks", []):
        rows += (
            "<tr>"
            f"<td class='muted'>{html.escape(str(t.get('id') or ''))}</td>"
            f"<td>{html.escape(str(t.get('phase') or ''))}</td>"
            f"<td>{html.escape(str(t.get('agent_type') or ''))}</td>"
            f"<td>{html.escape(str(t.get('objective') or ''))}</td>"
            "</tr>"
        )
    facets = plan.get("graph", {}).get("tasks", [{}])[0].get("facets", {}) or {}
    filled = [k for k, f in facets.items() if isinstance(f, dict) and f.get("status") == "filled"]
    approved_badge = ("<span class='badge b-done'>Đã duyệt ✓</span>"
                      if plan.get("approved") else "")
    body = (
        f"<div class='card'><h2>Plan · {branch} {approved_badge}</h2>"
        "<table><tr><th>Task</th><th>Phase</th><th>Agent</th><th>Objective</th></tr>"
        f"{rows}</table>"
        f"<p class='muted'>Facets đã điền: {html.escape(', '.join(filled)) or '(none)'}</p>"
        "<form method='POST' action='/task-plan' style='display:inline;margin-right:8px'>"
        f"<input type='hidden' name='id' value='{html.escape(spec_id)}'>"
        "<input type='hidden' name='action' value='approve'>"
        "<button type='submit'>Duyệt &amp; Chạy</button></form>"
        "<form method='POST' action='/task-plan' style='display:inline'>"
        f"<input type='hidden' name='id' value='{html.escape(spec_id)}'>"
        "<input type='hidden' name='action' value='revise'>"
        "<button type='submit' class='secondary'>Sửa spec</button></form>"
        "</div>"
    )
    return _page("Plan", body)


def _approve_task_plan_and_exec(spec_id: str) -> None:
    """Mark the plan approved and spawn the executor (the gate → exec)."""
    approve_plan(str(_config().storage_root), spec_id)
    _spawn_task_executor(spec_id)
```

(c) **Rewire POST `/task-spec`** — in `do_POST`, the `elif path == "/task-spec":` branch currently spawns the executor and redirects to `/task-exec`. Replace the inner block so that, when a repo is present, it **builds the plan and redirects to `/task-plan`** instead:
```python
                    edits = {key: (form.get(f"facet_{key}") or [""])[0] for key in FACET_KEYS}
                    _save_task_spec_edits(spec_id, edits, storage_root=str(_config().storage_root))
                    try:
                        _spec_data = json.loads(
                            (Path(_config().storage_root) / "task_specs" / f"{spec_id}.json")
                            .read_text(encoding="utf-8")
                        )
                        if _spec_data.get("repo"):
                            plan_single_task(_spec_data, spec_id,
                                             storage_root=str(_config().storage_root))
                            redirect = f"/task-plan?id={urllib.parse.quote(spec_id)}"
                        else:
                            redirect = f"/task-spec?id={urllib.parse.quote(spec_id)}"
                    except Exception:  # noqa: BLE001
                        redirect = f"/task-spec?id={urllib.parse.quote(spec_id)}"
```

(d) **Add GET `/task-plan`** in `do_GET` (next to the `/task-exec` branch):
```python
            elif parsed.path == "/task-plan":
                qs = urllib.parse.parse_qs(parsed.query)
                self._send(_task_plan_page((qs.get("id") or [""])[0]))
```

(e) **Add POST `/task-plan`** in `do_POST` (next to the `/task-spec` branch):
```python
            elif path == "/task-plan":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                spec_id = (form.get("id") or [""])[0].strip()
                action = (form.get("action") or [""])[0].strip()
                if spec_id and action == "approve":
                    _approve_task_plan_and_exec(spec_id)
                    redirect = f"/task-exec?id={urllib.parse.quote(spec_id)}"
                elif spec_id:  # revise
                    redirect = f"/task-spec?id={urllib.parse.quote(spec_id)}"
                else:
                    redirect = "/"
                self._send(_page("plan", "<div class='card'><h2>OK ✓</h2></div>",
                                 head_extra=f"<meta http-equiv='refresh' content='1;url={html.escape(redirect)}'>"))
```

- [ ] **Step 4: Run tests, verify pass**

Run: `python -m pytest tests/unit/test_webui_task_plan.py -q -p no:cacheprovider`
Expected: PASS (3 passed).

- [ ] **Step 5: Bump README + full suite + commit**

Bump README by +3 (new tests); run the full suite and set README to the real collected count.
Run: `python -m pytest -q -p no:cacheprovider` → 0 failed.

```bash
git add src/ai_dev_system/webui.py tests/unit/test_webui_task_plan.py README.md
git commit -m "feat(webui): /task-plan review gate between spec approval and execution"
```

---

## Acceptance (whole plan)

Maps to the spec's single-task acceptance criterion (design doc line 319 — *"plan artifact persisted; exec consumes the approved plan; the review gate blocks exec until approved"*):
- **Persisted plan:** `task_specs/<spec_id>-plan.json` written at spec-approval (Task 1 + Task 3c).
- **Exec consumes approved plan:** `run_executor` loads `plan["graph"]`/`plan["branch"]` (Task 2).
- **Gate blocks exec:** missing/unapproved plan → error status, no run (Task 2, `test_run_executor_errors_when_plan_not_approved`).
- **Reviewable in WebUI:** `/task-plan` shows test+impl tasks + branch + facets, with Approve/Revise (Task 3).

## Self-Review (plan author)

- **Spec coverage:** the 4 refactor steps in the spec's "Single-task flow: spec → plan → exec (targeted refactor)" map to Tasks 1 (extract+persist), 2 (run_executor takes plan; gate), 3 (review checkpoint + webui parity). `auto_approve_plan` is explicitly out (spec: "a later toggle, not v1"). ✓
- **No placeholders:** every step has real code or a concrete edit with line anchors. The two "adapt the variable name" notes (Task 2 Step 4) are unavoidable because they patch an existing test whose locals must be read first — flagged, not hand-waved. ✓
- **Type/name consistency:** `plan_single_task`, `load_plan`, `approve_plan`, `build_task_graph`, `branch_name_for`, `plan_path`, `_task_plan_page`, `_approve_task_plan_and_exec` are used identically across tasks. Plan schema keys (`spec_id/branch/tdd_gate/graph/approved/created_at`) consistent. ✓
- **Breakage guard:** the 3 existing tests referencing moved symbols are explicitly updated in the task that moves them; full-suite gate per task catches misses. ✓
