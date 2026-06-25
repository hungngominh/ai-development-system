# Task Spec 24-Criteria Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the task spec from 8 facets to 20 facets + 1 field, covering all 24 criteria (17 spec-time + 7 exec-time output documents).

**Architecture:** Split `FACET_KEYS` into `SPEC_FACET_KEYS` (13 LLM-generated at spec time) and `EXEC_FACET_KEYS` (7 human-filled after implementation). `FACET_KEYS = SPEC_FACET_KEYS + EXEC_FACET_KEYS` for backward compatibility. Add `out_of_scope` as a top-level task field. Exec facets default to `{"status": "na", "content": "", "reason": "exec-time — fill after implementation"}` at spec time. Webui renders two labeled sections.

**Tech Stack:** Python 3.12, stdlib only. Tests via pytest. No new dependencies.

## Global Constraints

- Never raise in `generate_task_facets` or `generate_task_facets_agentic` — failures fall back to `_all_needs_human()`.
- `FACET_KEYS` must remain importable from `ai_dev_system.task_graph.facets` (used in webui).
- All test files live under `tests/unit/task_graph/` or `tests/unit/`.
- Run tests with: `python -m pytest tests/unit/task_graph/test_facets.py tests/unit/task_graph/test_facets_agentic.py tests/unit/test_webui_task_spec.py -v`

---

### Task 1: Expand `facets.py` — SPEC/EXEC split + 12 new definitions + updated generators

**Files:**
- Modify: `src/ai_dev_system/task_graph/facets.py`
- Modify: `src/ai_dev_system/task_graph/single_task.py` (add `out_of_scope` field)
- Modify: `tests/unit/task_graph/test_facets.py`

**Interfaces:**
- Produces: `SPEC_FACET_KEYS`, `EXEC_FACET_KEYS`, `FACET_KEYS`, `FACET_STAGE` exported from `facets.py`
- Produces: `build_single_task()` returns dict with `"out_of_scope": ""`
- `generate_task_facets()` returns 20-key dict: 13 spec facets (LLM-filled or needs_human), 7 exec facets (na)

- [ ] **Step 1: Write the failing tests**

Replace the relevant tests in `tests/unit/task_graph/test_facets.py`:

```python
# At top — update imports
from ai_dev_system.task_graph.facets import (
    FACET_KEYS,
    SPEC_FACET_KEYS,
    EXEC_FACET_KEYS,
    FACET_STAGE,
    is_implementation_task,
    generate_task_facets,
    generate_task_facets_for_graph,
)

# Update helpers — LLM only sees/returns spec facets
def _all_filled_response():
    return json.dumps({k: {"status": "filled", "content": f"{k} detail", "reason": ""}
                       for k in SPEC_FACET_KEYS})

def _all_filled_with_reasoning():
    return json.dumps({
        k: {"status": "filled", "content": f"{k} detail", "reason": "",
            "reasoning": f"Dev: build it. QA: test it. Security: secure it. — {k}"}
        for k in SPEC_FACET_KEYS
    })

# New: constants shape
def test_spec_exec_keys_disjoint_and_union_equals_facet_keys():
    assert set(SPEC_FACET_KEYS) & set(EXEC_FACET_KEYS) == set()
    assert set(SPEC_FACET_KEYS) | set(EXEC_FACET_KEYS) == set(FACET_KEYS)
    assert len(SPEC_FACET_KEYS) == 13
    assert len(EXEC_FACET_KEYS) == 7
    assert len(FACET_KEYS) == 20

def test_facet_stage_covers_all_keys():
    assert set(FACET_STAGE.keys()) == set(FACET_KEYS)
    assert all(FACET_STAGE[k] == "spec" for k in SPEC_FACET_KEYS)
    assert all(FACET_STAGE[k] == "exec" for k in EXEC_FACET_KEYS)

# Update: all 20 keys returned; spec=filled, exec=na
def test_generate_returns_all_facets_spec_filled_exec_na():
    facets = generate_task_facets(_impl_task(), {"functional.md": "f"}, None, _FakeLLM(_all_filled_response()))
    assert set(facets.keys()) == set(FACET_KEYS)
    assert list(facets.keys()) == list(FACET_KEYS)
    assert facets["database"]["status"] == "filled"
    assert facets["database"]["content"] == "database detail"
    for k in EXEC_FACET_KEYS:
        assert facets[k]["status"] == "na"
        assert "exec-time" in facets[k]["reason"]

# New: exec facets default to na even on success
def test_exec_facets_always_na_after_generate():
    facets = generate_task_facets(_impl_task(), {}, None, _FakeLLM(_all_filled_response()))
    for k in EXEC_FACET_KEYS:
        assert facets[k] == {"status": "na", "content": "", "reason": "exec-time — fill after implementation"}

# Update: on LLM error, spec=needs_human, exec=na
def test_generate_resilient_on_llm_error_spec_needs_human_exec_na():
    facets = generate_task_facets(_impl_task(), {}, None, _RaisingLLM())
    assert set(facets.keys()) == set(FACET_KEYS)
    assert all(facets[k]["status"] == "needs_human" for k in SPEC_FACET_KEYS)
    assert all(facets[k]["status"] == "na" for k in EXEC_FACET_KEYS)

# Update: on non-JSON, spec=needs_human, exec=na
def test_generate_resilient_on_non_json():
    facets = generate_task_facets(_impl_task(), {}, None, _FakeLLM("not json"))
    assert all(facets[k]["status"] == "needs_human" for k in SPEC_FACET_KEYS)
    assert all(facets[k]["status"] == "na" for k in EXEC_FACET_KEYS)

# New: new spec facet keys are present in FACET_DEFINITIONS
def test_all_facet_keys_have_definitions():
    from ai_dev_system.task_graph.facets import FACET_DEFINITIONS
    for k in FACET_KEYS:
        assert k in FACET_DEFINITIONS, f"missing definition for {k!r}"

# New: build_single_task includes out_of_scope
def test_build_single_task_has_out_of_scope():
    from ai_dev_system.task_graph.single_task import build_single_task
    task = build_single_task("add CSV import")
    assert "out_of_scope" in task
    assert task["out_of_scope"] == ""
```

- [ ] **Step 2: Run tests to verify they fail**

```
python -m pytest tests/unit/task_graph/test_facets.py -v -k "spec_exec_keys or facet_stage or all_facets_spec_filled or exec_facets_always or resilient_on_llm_error_spec or resilient_on_non_json or all_facet_keys_have or out_of_scope"
```

Expected: ImportError on `SPEC_FACET_KEYS` (not yet exported), multiple failures.

- [ ] **Step 3: Implement `facets.py` changes**

Replace the entire `FACET_KEYS`, `FACET_DEFINITIONS`, `_all_needs_human`, `_build_facet_prompt`, `generate_task_facets` sections:

```python
SPEC_FACET_KEYS: tuple[str, ...] = (
    "input", "auth_permission", "business_rule", "database",
    "response", "error_cases", "non_functional", "test_cases",
    "validation_rules", "api_endpoints", "security_rules",
    "concurrency_rules", "logging_audit",
)

EXEC_FACET_KEYS: tuple[str, ...] = (
    "impl_document", "api_review", "db_review",
    "business_rule_mapping", "test_evidence", "deployment_note", "change_log",
)

FACET_KEYS: tuple[str, ...] = SPEC_FACET_KEYS + EXEC_FACET_KEYS

FACET_STAGE: dict[str, str] = {
    **{k: "spec" for k in SPEC_FACET_KEYS},
    **{k: "exec" for k in EXEC_FACET_KEYS},
}

FACET_DEFINITIONS: dict[str, str] = {
    "input": "Input data/params/artifacts: shape, source, validity constraints.",
    "auth_permission": "Who may run this; authn/authz required; permission boundaries.",
    "business_rule": "Domain logic/constraints for this task (flavored by the product vertical).",
    "database": "Schema/table/migration changes; query patterns; integrity.",
    "response": "Output/return shape: structure, status codes, format.",
    "error_cases": "Known failure modes and how each is handled (flavored by the vertical).",
    "non_functional": "Task-level performance/security/logging/reliability.",
    "test_cases": "Concrete test scenarios (unit/integration) for this task.",
    "validation_rules": "Input validation constraints, required fields, format rules, allowed values.",
    "api_endpoints": "REST endpoints this task creates or modifies: method, path, purpose.",
    "security_rules": "Rate limits, HTTPS, CSRF protection, data sanitisation, OWASP mitigations.",
    "concurrency_rules": "Race conditions, locking strategy, idempotency, retry behaviour.",
    "logging_audit": "What must be logged, at what level, and what audit trail is required.",
    "impl_document": "Developer implementation notes: approach taken, key decisions, gotchas.",
    "api_review": "API review notes: contract changes, versioning impact, consumer impact.",
    "db_review": "Database review notes: migration steps, rollback plan, index strategy.",
    "business_rule_mapping": "Map of business rules to code locations (file:function).",
    "test_evidence": "Test run evidence: passed/failed counts, coverage, CI link.",
    "deployment_note": "Deployment steps, env vars, feature flags, rollout order.",
    "change_log": "Summary of changes made in this task for the changelog.",
}
```

Update `_all_needs_human()`:
```python
def _all_needs_human() -> dict[str, dict]:
    result = {k: _needs_human() for k in SPEC_FACET_KEYS}
    for k in EXEC_FACET_KEYS:
        result[k] = {"status": "na", "content": "", "reason": "exec-time — fill after implementation"}
    return result
```

Update `_build_facet_prompt()` — change the two references from `FACET_KEYS` to `SPEC_FACET_KEYS` and update the count string:
```python
def _build_facet_prompt(task: dict, spec_content: dict[str, str], profile: dict | None):
    facet_lines = "\n".join(f"- {k}: {FACET_DEFINITIONS[k]}" for k in SPEC_FACET_KEYS)
    system = (
        "You are a senior engineer detailing one implementation task. "
        "Before filling each facet, briefly consider three engineering lenses:\n"
        "• Developer — what must be built and how\n"
        "• QA / Security — edge cases, auth risks, error modes\n"
        "• Data — schema implications, query patterns, integrity\n\n"
        "For each of the 13 engineering facets, write a concrete task-level detail, OR "
        'mark it "na" (with a reason) when irrelevant, OR "needs_human" when you cannot '
        "determine it from the given context.\n"
        "Return ONLY a JSON object keyed by the 13 facet names; each value is:\n"
        '{"status": "filled"|"na"|"needs_human", "content": "...", "reason": "...", '
        '"reasoning": "one sentence summarising the key insight from the lenses above"}.\n'
        "Facets:\n" + facet_lines
    )
    sections = []
    for name in ("functional.md", "design.md", "non-functional.md", "acceptance-criteria.md"):
        body = (spec_content or {}).get(name, "")
        if body:
            sections.append(f"### {name}\n{body[:1200]}")
    profile_block = ""
    if profile and profile.get("key_dimensions"):
        profile_block = (
            "\nPRODUCT VERTICAL (flavor business_rule & error_cases accordingly):\n"
            f"- vertical: {profile.get('vertical', '')}\n"
            f"- key dimensions: {'; '.join(profile.get('key_dimensions', []))}\n"
        )
    user = (
        f"# Task {task.get('id', '?')}\n"
        f"objective: {task.get('objective', '')}\n"
        f"description: {task.get('description', '')}\n"
        f"inputs: {task.get('required_inputs', [])}\n"
        f"outputs: {task.get('expected_outputs', [])}\n"
        f"{profile_block}\n"
        "## Project context\n" + ("\n\n".join(sections) if sections else "(none)")
    )
    return system, user
```

Update `generate_task_facets()`:
```python
def generate_task_facets(task: dict, spec_content: dict[str, str], profile: dict | None, llm) -> dict[str, dict]:
    """Fill the 13 spec facets for one task. Never raises; failures → all needs_human."""
    system, user = _build_facet_prompt(task, spec_content, profile)
    try:
        raw = llm.complete(system=system, user=user)
        data = json.loads(raw)
    except Exception:
        return _all_needs_human()
    if not isinstance(data, dict):
        return _all_needs_human()
    result = {k: _coerce_facet(data.get(k)) for k in SPEC_FACET_KEYS}
    for k in EXEC_FACET_KEYS:
        result[k] = {"status": "na", "content": "", "reason": "exec-time — fill after implementation"}
    return result
```

- [ ] **Step 4: Add `out_of_scope` to `single_task.py`**

In `build_single_task()`, add `"out_of_scope": ""` to the returned dict:
```python
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
        "out_of_scope": "",
    }
```

- [ ] **Step 5: Run tests to verify they pass**

```
python -m pytest tests/unit/task_graph/test_facets.py -v
```

Expected: all pass. Note: the old test named `test_generate_returns_all_eight_facets_filled` can be deleted since `test_generate_returns_all_facets_spec_filled_exec_na` replaces it. Also delete `test_generate_resilient_on_llm_error_all_needs_human` (replaced by `test_generate_resilient_on_llm_error_spec_needs_human_exec_na`).

- [ ] **Step 6: Commit**

```
git add src/ai_dev_system/task_graph/facets.py src/ai_dev_system/task_graph/single_task.py tests/unit/task_graph/test_facets.py
git commit -m "feat: expand facets to 20 (13 spec + 7 exec) + out_of_scope field"
```

---

### Task 2: Update `facets_agentic.py` to use `SPEC_FACET_KEYS`

**Files:**
- Modify: `src/ai_dev_system/task_graph/facets_agentic.py`
- Modify: `tests/unit/task_graph/test_facets_agentic.py`

**Interfaces:**
- Consumes: `SPEC_FACET_KEYS`, `EXEC_FACET_KEYS` from `facets.py` (Task 1)
- Produces: `generate_task_facets_agentic()` returns 20-key dict (13 spec filled/na/needs_human, 7 exec na)

- [ ] **Step 1: Write failing tests**

Add/update in `tests/unit/task_graph/test_facets_agentic.py`:

```python
# Update import
from ai_dev_system.task_graph.facets import FACET_KEYS, SPEC_FACET_KEYS, EXEC_FACET_KEYS

# Update helper — LLM only returns spec facets
def _ok_inner():
    return json.dumps({k: {"status": "filled", "content": f"{k} c", "reason": ""}
                       for k in SPEC_FACET_KEYS})

# New: all 20 keys returned; exec are na
def test_exec_facets_are_na_in_agentic_result(tmp_path):
    run = _FakeRun(_cp(stdout=_wrapper(_ok_inner())))
    facets = generate_task_facets_agentic(_task(), str(tmp_path), run=run)
    assert set(facets.keys()) == set(FACET_KEYS)
    for k in EXEC_FACET_KEYS:
        assert facets[k]["status"] == "na"
        assert "exec-time" in facets[k]["reason"]

# New: prompt mentions 13 facets, not 8
def test_prompt_mentions_13_spec_facets(tmp_path):
    run = _FakeRun(_cp(stdout=_wrapper(_ok_inner())))
    generate_task_facets_agentic(_task(), str(tmp_path), run=run)
    prompt_arg = run.calls[0][0][run.calls[0][0].index("-p") + 1]
    assert "13" in prompt_arg
```

- [ ] **Step 2: Run tests to verify they fail**

```
python -m pytest tests/unit/task_graph/test_facets_agentic.py -v -k "exec_facets_are_na or prompt_mentions_13"
```

Expected: `test_exec_facets_are_na_in_agentic_result` FAIL (only 8 keys returned), `test_prompt_mentions_13_spec_facets` FAIL.

- [ ] **Step 3: Implement `facets_agentic.py` changes**

Update the import at top:
```python
from ai_dev_system.task_graph.facets import (
    SPEC_FACET_KEYS,
    EXEC_FACET_KEYS,
    FACET_DEFINITIONS,
    _all_needs_human,
    _coerce_facet,
)
```

Update `_build_prompt()` — change `FACET_KEYS` → `SPEC_FACET_KEYS` and "8" → "13":
```python
def _build_prompt(task: dict) -> str:
    facet_lines = "\n".join(f"- {k}: {FACET_DEFINITIONS[k]}" for k in SPEC_FACET_KEYS)
    return (
        "You are detailing ONE implementation task against THIS repository. Use "
        "Read/Grep/Glob to inspect the actual code relevant to the task (data "
        "models, schema/migrations, the modules this task touches). For each of the "
        "13 engineering facets below, write a concrete, code-grounded detail and cite "
        "the file path(s) you used. Mark a facet \"na\" (with a reason) when "
        "irrelevant, or \"needs_human\" when you find NO evidence in the code — do "
        "NOT invent. Ignore .env, secrets, node_modules, and build output.\n\n"
        f"TASK:\n- objective: {task.get('objective', '')}\n"
        f"- description: {task.get('description', '')}\n\n"
        "Return ONLY a JSON object keyed by the 13 facet names; each value is "
        '{"status": "filled"|"na"|"needs_human", "content": "...", "reason": "..."}.\n'
        "Facets:\n" + facet_lines
    )
```

Update `generate_task_facets_agentic()` — last line change:
```python
    # was: return {k: _coerce_facet(data.get(k)) for k in FACET_KEYS}
    result = {k: _coerce_facet(data.get(k)) for k in SPEC_FACET_KEYS}
    for k in EXEC_FACET_KEYS:
        result[k] = {"status": "na", "content": "", "reason": "exec-time — fill after implementation"}
    return result
```

- [ ] **Step 4: Run tests to verify pass**

```
python -m pytest tests/unit/task_graph/test_facets_agentic.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```
git add src/ai_dev_system/task_graph/facets_agentic.py tests/unit/task_graph/test_facets_agentic.py
git commit -m "feat: agentic facets use SPEC_FACET_KEYS (13), exec facets default to na"
```

---

### Task 3: Update worker + webui — log strings, grouped rendering

**Files:**
- Modify: `src/ai_dev_system/task_graph/single_task_worker.py`
- Modify: `src/ai_dev_system/webui.py`
- Modify: `tests/unit/test_webui_task_spec.py`

**Interfaces:**
- Consumes: `SPEC_FACET_KEYS`, `EXEC_FACET_KEYS`, `FACET_KEYS` from `facets.py`
- Produces: webui renders two sections — "Spec facets" (13) and "Implementation documents" (7)

- [ ] **Step 1: Write failing tests**

Add to `tests/unit/test_webui_task_spec.py`:

```python
# Add import at top
from ai_dev_system.task_graph.facets import FACET_KEYS, SPEC_FACET_KEYS, EXEC_FACET_KEYS

# New: render groups spec and exec into separate sections
def test_render_shows_spec_and_exec_sections():
    facets = _facets()
    html_out = webui._render_task_spec({"title": "T"}, facets)
    assert "Spec facets" in html_out or "spec" in html_out.lower()
    assert "Implementation" in html_out or "impl" in html_out.lower()

# New: exec facets with na show exec-time reason
def test_render_exec_na_shows_reason():
    facets = _facets()
    for k in EXEC_FACET_KEYS:
        facets[k] = {"status": "na", "content": "", "reason": "exec-time — fill after implementation"}
    html_out = webui._render_task_spec({"title": "T"}, facets)
    assert "exec-time" in html_out
```

- [ ] **Step 2: Run tests to verify they fail**

```
python -m pytest tests/unit/test_webui_task_spec.py -v -k "spec_and_exec_sections or exec_na_shows"
```

Expected: FAIL (no section headers in current render).

- [ ] **Step 3: Update `single_task_worker.py`**

Import `SPEC_FACET_KEYS` and update hardcoded count references:

```python
# Add import at top of run_worker or module level:
from ai_dev_system.task_graph.facets import SPEC_FACET_KEYS as _SPEC_KEYS
```

Change lines 104–109 (the stats block):
```python
        facets = result["facets"]
        spec_facets = {k: v for k, v in facets.items() if k in _SPEC_KEYS}
        filled = sum(1 for f in spec_facets.values() if f.get("status") == "filled")
        na = sum(1 for f in spec_facets.values() if f.get("status") == "na")
        needs_human = sum(1 for f in spec_facets.values() if f.get("status") == "needs_human")
        _spec_log(log_path, f"Sinh facets xong — filled={filled} na={na} needs_human={needs_human}")
        if needs_human == len(_SPEC_KEYS):
            _spec_log(log_path, f"CẢNH BÁO: tất cả {len(_SPEC_KEYS)} spec facets đều needs_human — có thể claude CLI đã timeout hoặc lỗi nội bộ")
```

Also update the log string on line 99:
```python
            _spec_log(log_path, f"Đang gọi LLM sinh {len(_SPEC_KEYS)} spec facets…")
```

- [ ] **Step 4: Update `webui.py` — grouped rendering + import**

Update the import line at top of `webui.py`:
```python
from ai_dev_system.task_graph.facets import FACET_KEYS, SPEC_FACET_KEYS, EXEC_FACET_KEYS
```

Replace `_render_task_spec()` body (lines 320–358) — add section headers between spec and exec facets:

```python
def _render_task_spec(task: dict, facets: dict, spec_id: str | None = None) -> str:
    def _row(key: str) -> str:
        f = facets.get(key) or {"status": "needs_human", "content": "", "reason": ""}
        status = f.get("status")
        content = f.get("content") or ""
        if spec_id:
            escaped = html.escape(content)
            reasoning = html.escape(str(f.get("reasoning") or "")).strip()
            reasoning_block = (
                f"<details><summary class='muted' style='font-size:12px;cursor:pointer'>"
                f"reasoning</summary><div class='muted' style='font-size:12px;margin:4px 0 6px'>"
                f"{reasoning}</div></details>"
                if reasoning else ""
            )
            val = (
                reasoning_block
                + f"<textarea name='facet_{html.escape(key)}' rows='3' "
                f"placeholder='Nhập nội dung...'>{escaped}</textarea>"
            )
        elif status == "filled" and content:
            val = html.escape(content)
        elif status == "na":
            val = f"<span class='muted'>N/A — {html.escape(str(f.get('reason') or ''))}</span>"
        else:
            val = "<span class='caveat'>(cần làm rõ)</span>"
        return f"<tr><td class='muted'>{html.escape(key)}</td><td>{val}</td></tr>"

    spec_rows = "".join(_row(k) for k in SPEC_FACET_KEYS)
    exec_rows = "".join(_row(k) for k in EXEC_FACET_KEYS)
    title = html.escape(str(task.get("title") or "Task"))
    table = (
        "<table>"
        "<tr><th colspan='2' style='color:#5fb0f0;padding-top:10px'>Spec facets (13)</th></tr>"
        + spec_rows
        + "<tr><th colspan='2' style='color:#5fd07f;padding-top:14px'>Implementation documents (7)</th></tr>"
        + exec_rows
        + "</table>"
    )
    if spec_id:
        return (
            f"<form method='POST' action='/task-spec'>"
            f"<input type='hidden' name='id' value='{html.escape(spec_id)}'>"
            f"<div class='card'><h2>Task spec · {title}</h2>"
            f"{table}"
            f"<button type='submit'>Lưu &amp; Duyệt</button>"
            f"</div></form>"
        )
    return f"<div class='card'><h2>Task spec · {title}</h2>{table}</div>"
```

Also update the home-page form description on line ~170:
```python
    <p class='muted'>Trả về 20 facet (13 spec + 7 impl-docs) cho task.</p>
```

- [ ] **Step 5: Run all affected tests**

```
python -m pytest tests/unit/task_graph/test_facets.py tests/unit/task_graph/test_facets_agentic.py tests/unit/test_webui_task_spec.py tests/unit/task_graph/test_single_task_worker.py -v
```

Expected: all pass.

- [ ] **Step 6: Run full test suite to check for regressions**

```
python -m pytest tests/unit/ -v --tb=short
```

Expected: all pass (no regressions from FACET_KEYS expansion).

- [ ] **Step 7: Commit**

```
git add src/ai_dev_system/task_graph/single_task_worker.py src/ai_dev_system/webui.py tests/unit/test_webui_task_spec.py
git commit -m "feat: webui renders spec/exec facet sections; worker logs use SPEC_FACET_KEYS count"
```
