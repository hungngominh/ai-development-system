# Task Facet Taxonomy (project→task) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-fill an 8-facet engineering spec (input, auth_permission, business_rule, database, response, error_cases, non_functional, test_cases) for each atomic implementation task, let a human review/fill it at Gate 2, and feed it to the coding agent.

**Architecture:** A new `task_graph/facets.py` LLM step runs right after `generate_task_graph` (Phase B), attaching a `facets` dict to each `type=="coding"` task. Facets ride in the task-graph JSON → `TASK_GRAPH_APPROVED` (edited at Gate 2) → `context_snapshot` (materializer) → the agent prompt. Additive and resilient: any LLM failure yields all-`needs_human` facets and never breaks the pipeline.

**Tech Stack:** Python 3.12, stdlib `dataclasses`/`json`, `pytest`.

## Global Constraints

- **Python 3.12**; run tests with `PYTHONUTF8=1 python -m pytest ...` (Windows cp1252 console; Vietnamese/UTF-8 in prompts).
- **LLM interface is `complete(system: str, user: str) -> str`.** Both real clients (`ClaudeCodeLLMClient`, `RealLLMClient` in `llm_factory.py`) and the debate stub (`StubDebateLLMClient`) implement this. Do NOT use the single-arg `llm.complete(prompt)` shape — that is the sibling `enricher.py`'s call, and it actually fails for every real client (binds to `system`, missing `user` → TypeError → silently swallowed). `facets.py` must call `complete(system, user)`.
- **Stub-degradation invariant:** the facet SYSTEM prompt MUST avoid the substrings `question`, `generate`, `moderator`, `synthesis`, `finalize`, `spec` (incl. inside words like "specification"/"specific"). `StubDebateLLMClient.complete` routes on these; avoiding them makes the stub return its non-JSON default → resilient parse → all facets `needs_human`. This keeps the existing stub-based Phase B suite green. Use words like "facet", "engineering detail", "implementation detail" — never "spec"/"specification".
- **Resilience:** `generate_task_facets` NEVER raises. Any exception, non-dict JSON, or missing facet key → that facet (or all 8) become `{"status": "needs_human", "content": "", "reason": ""}`.
- **Additive / no DB migration:** do NOT change `generate_task_graph`, the DB schema, or `task_runs` columns. Facets live in the existing freeform task-graph JSON and the existing `context_snapshot` JSON TEXT column.
- **Scope filter:** facets attach ONLY to `task.get("execution_type") == "atomic" and task.get("type") == "coding"`. Skip design/analysis/testing tasks.
- **Kill-switch:** honor env `AI_DEV_DISABLE_TASK_FACETS=1` (skip facet generation). Do NOT add to `feature_flags.FLAG_ORDER` (rigid linear chain — out of scope).
- **Facet value shape:** every facet is `{"status": "filled"|"needs_human"|"na", "content": str, "reason": str}`. `FACET_KEYS` order is fixed: `input, auth_permission, business_rule, database, response, error_cases, non_functional, test_cases`.
- **Branch first:** create `feat/task-facet-taxonomy` before the first commit.

## File Structure

**New**
- `src/ai_dev_system/task_graph/facets.py` — facet model constants, `is_implementation_task`, prompt builder, `generate_task_facets`, `generate_task_facets_for_graph`.
- `tests/unit/task_graph/test_facets.py`

**Modified**
- `src/ai_dev_system/engine/materializer.py` — `_build_context` carries `facets`.
- `src/ai_dev_system/agents/claude_max_agent.py` — `_build_user` renders a `## Task Specification` section.
- `src/ai_dev_system/gate/terminal_gate2.py` — `facet show/set/na` commands, facet summary in `_render`, approve warning.
- `src/ai_dev_system/debate_pipeline.py` — Phase 2: after `generate_task_graph`, load profile + attach facets before promoting `TASK_GRAPH_GENERATED`.

---

## Task 1: Facet model + generator (`task_graph/facets.py`)

**Files:**
- Create: `src/ai_dev_system/task_graph/facets.py`
- Test: `tests/unit/task_graph/test_facets.py`

**Interfaces:**
- Produces:
  - `FACET_KEYS: tuple[str, ...]` = `("input","auth_permission","business_rule","database","response","error_cases","non_functional","test_cases")`
  - `is_implementation_task(task: dict) -> bool` — `execution_type=="atomic" and type=="coding"`
  - `generate_task_facets(task: dict, spec_content: dict[str, str], profile: dict | None, llm) -> dict[str, dict]` — returns the 8-key facets dict; never raises.
  - `generate_task_facets_for_graph(tasks: list[dict], spec_content: dict[str, str], profile: dict | None, llm) -> list[dict]` — mutates each implementation task in place (sets `task["facets"]`), returns the list. Honors `AI_DEV_DISABLE_TASK_FACETS`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/task_graph/test_facets.py`:

```python
import json

from ai_dev_system.task_graph.facets import (
    FACET_KEYS,
    is_implementation_task,
    generate_task_facets,
    generate_task_facets_for_graph,
)


class _FakeLLM:
    """complete(system, user) -> fixed response; records the system prompt."""
    def __init__(self, response: str):
        self.response = response
        self.system_seen = None
    def complete(self, system: str, user: str) -> str:
        self.system_seen = system
        return self.response


class _RaisingLLM:
    def complete(self, system: str, user: str) -> str:
        raise RuntimeError("llm down")


def _impl_task(tid="TASK-IMPL"):
    return {"id": tid, "execution_type": "atomic", "type": "coding",
            "objective": "build the thing", "description": "...",
            "required_inputs": ["design_doc"], "expected_outputs": ["implementation"]}


def _all_filled_response():
    return json.dumps({k: {"status": "filled", "content": f"{k} detail", "reason": ""}
                       for k in FACET_KEYS})


def test_is_implementation_task_only_coding_atomic():
    assert is_implementation_task(_impl_task()) is True
    assert is_implementation_task({"execution_type": "atomic", "type": "design"}) is False
    assert is_implementation_task({"execution_type": "composite", "type": "coding"}) is False


def test_generate_returns_all_eight_facets_filled():
    facets = generate_task_facets(_impl_task(), {"functional.md": "f"}, None, _FakeLLM(_all_filled_response()))
    assert set(facets.keys()) == set(FACET_KEYS)
    assert facets["database"]["status"] == "filled"
    assert facets["database"]["content"] == "database detail"


def test_generate_na_and_needs_human_pass_through():
    resp = json.dumps({
        **{k: {"status": "filled", "content": "x", "reason": ""} for k in FACET_KEYS},
        "database": {"status": "na", "content": "", "reason": "no persistence"},
        "auth_permission": {"status": "needs_human", "content": "", "reason": ""},
    })
    facets = generate_task_facets(_impl_task(), {}, None, _FakeLLM(resp))
    assert facets["database"] == {"status": "na", "content": "", "reason": "no persistence"}
    assert facets["auth_permission"]["status"] == "needs_human"


def test_generate_resilient_on_llm_error_all_needs_human():
    facets = generate_task_facets(_impl_task(), {}, None, _RaisingLLM())
    assert set(facets.keys()) == set(FACET_KEYS)
    assert all(facets[k]["status"] == "needs_human" for k in FACET_KEYS)


def test_generate_resilient_on_non_json():
    facets = generate_task_facets(_impl_task(), {}, None, _FakeLLM("not json"))
    assert all(facets[k]["status"] == "needs_human" for k in FACET_KEYS)


def test_missing_key_in_response_becomes_needs_human():
    resp = json.dumps({"input": {"status": "filled", "content": "c", "reason": ""}})
    facets = generate_task_facets(_impl_task(), {}, None, _FakeLLM(resp))
    assert facets["input"]["status"] == "filled"
    assert facets["response"]["status"] == "needs_human"  # absent → needs_human


def test_system_prompt_avoids_stub_router_substrings():
    llm = _FakeLLM(_all_filled_response())
    generate_task_facets(_impl_task(), {}, None, llm)
    low = llm.system_seen.lower()
    for banned in ("question", "generate", "moderator", "synthesis", "finalize", "spec"):
        assert banned not in low, f"system prompt must avoid {banned!r}"


def test_for_graph_only_attaches_to_impl_tasks():
    tasks = [
        _impl_task("TASK-IMPL"),
        {"id": "TASK-DESIGN", "execution_type": "atomic", "type": "design"},
    ]
    generate_task_facets_for_graph(tasks, {}, None, _FakeLLM(_all_filled_response()))
    assert "facets" in tasks[0]
    assert "facets" not in tasks[1]


def test_for_graph_kill_switch(monkeypatch):
    monkeypatch.setenv("AI_DEV_DISABLE_TASK_FACETS", "1")
    tasks = [_impl_task()]
    generate_task_facets_for_graph(tasks, {}, None, _FakeLLM(_all_filled_response()))
    assert "facets" not in tasks[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONUTF8=1 python -m pytest tests/unit/task_graph/test_facets.py -q`
Expected: FAIL — `ModuleNotFoundError: ...task_graph.facets`.

- [ ] **Step 3: Write the implementation**

Create `src/ai_dev_system/task_graph/facets.py`:

```python
"""Per-task engineering facets for atomic implementation tasks.

Runs after `generate_task_graph` (Phase B): for each coding task, an LLM fills
8 facets so the executing agent receives concrete Input/Auth/Business-rule/
Database/Response/Error/NFR/Test detail instead of guessing. Reviewed at Gate 2.

LLM interface is `complete(system, user)` (the working real-client shape — the
sibling enricher's single-arg `complete(prompt)` silently fails on real clients).
Resilient: any failure yields all-`needs_human` facets; never raises.
"""
from __future__ import annotations

import json
import os

FACET_KEYS: tuple[str, ...] = (
    "input", "auth_permission", "business_rule", "database",
    "response", "error_cases", "non_functional", "test_cases",
)

# Human-readable intent per facet — drives the prompt and is stable doc.
FACET_DEFINITIONS: dict[str, str] = {
    "input": "Input data/params/artifacts: shape, source, validity constraints.",
    "auth_permission": "Who may run this; authn/authz required; permission boundaries.",
    "business_rule": "Domain logic/constraints for this task (flavored by the product vertical).",
    "database": "Schema/table/migration changes; query patterns; integrity.",
    "response": "Output/return shape: structure, status codes, format.",
    "error_cases": "Known failure modes and how each is handled (flavored by the vertical).",
    "non_functional": "Task-level performance/security/logging/reliability.",
    "test_cases": "Concrete test scenarios (unit/integration) for this task.",
}

_VALID_STATUS = {"filled", "needs_human", "na"}
_DISABLE_ENV = "AI_DEV_DISABLE_TASK_FACETS"


def is_implementation_task(task: dict) -> bool:
    return task.get("execution_type") == "atomic" and task.get("type") == "coding"


def _needs_human() -> dict:
    return {"status": "needs_human", "content": "", "reason": ""}


def _all_needs_human() -> dict[str, dict]:
    return {k: _needs_human() for k in FACET_KEYS}


def _coerce_facet(raw) -> dict:
    if not isinstance(raw, dict):
        return _needs_human()
    status = raw.get("status")
    if status not in _VALID_STATUS:
        return _needs_human()
    return {
        "status": status,
        "content": str(raw.get("content") or ""),
        "reason": str(raw.get("reason") or ""),
    }


def _build_facet_prompt(task: dict, spec_content: dict[str, str], profile: dict | None):
    facet_lines = "\n".join(f"- {k}: {FACET_DEFINITIONS[k]}" for k in FACET_KEYS)
    system = (
        "You are a senior engineer detailing one implementation task. For each of "
        "the 8 engineering facets below, write a concrete task-level detail, OR mark "
        'it "na" (with a reason) when irrelevant, OR "needs_human" when you cannot '
        "determine it from the given context. Return ONLY a JSON object keyed by the "
        "8 facet names; each value is "
        '{"status": "filled"|"na"|"needs_human", "content": "...", "reason": "..."}.\n'
        "Facets:\n" + facet_lines
    )
    # Only the most relevant project sections, truncated.
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


def generate_task_facets(task: dict, spec_content: dict[str, str], profile: dict | None, llm) -> dict[str, dict]:
    """Fill the 8 facets for one task. Never raises; failures → all needs_human."""
    system, user = _build_facet_prompt(task, spec_content, profile)
    try:
        raw = llm.complete(system=system, user=user)
        data = json.loads(raw)
    except Exception:
        return _all_needs_human()
    if not isinstance(data, dict):
        return _all_needs_human()
    return {k: _coerce_facet(data.get(k)) for k in FACET_KEYS}


def generate_task_facets_for_graph(tasks, spec_content, profile, llm):
    """Attach `task['facets']` to each atomic coding task. Honors kill-switch."""
    if os.environ.get(_DISABLE_ENV) == "1":
        return tasks
    for task in tasks:
        if is_implementation_task(task):
            task["facets"] = generate_task_facets(task, spec_content, profile, llm)
    return tasks
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONUTF8=1 python -m pytest tests/unit/task_graph/test_facets.py -q`
Expected: PASS (9 passed).

- [ ] **Step 5: Commit**

```bash
git switch -c feat/task-facet-taxonomy   # first commit only
git add src/ai_dev_system/task_graph/facets.py tests/unit/task_graph/test_facets.py
git commit -m "feat: per-task facet generator (8 facets, resilient, complete(system,user))"
```

---

## Task 2: Materializer carries facets into the execution snapshot

**Files:**
- Modify: `src/ai_dev_system/engine/materializer.py` (`_build_context`)
- Test: `tests/unit/engine/test_materializer_facets.py` (create)

**Interfaces:**
- Consumes: a task dict that may carry `task["facets"]`.
- Produces: `context_snapshot["facets"]` (the dict, or `{}` when absent).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/engine/test_materializer_facets.py`:

```python
from ai_dev_system.engine.materializer import _build_context


def test_build_context_includes_facets_when_present():
    task = {"id": "TASK-IMPL", "type": "coding",
            "facets": {"input": {"status": "filled", "content": "a CSV", "reason": ""}}}
    ctx = _build_context(task)
    assert ctx["facets"]["input"]["content"] == "a CSV"


def test_build_context_facets_default_empty():
    ctx = _build_context({"id": "TASK-DESIGN"})
    assert ctx["facets"] == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 python -m pytest tests/unit/engine/test_materializer_facets.py -q`
Expected: FAIL — `KeyError: 'facets'`.

- [ ] **Step 3: Implement**

In `src/ai_dev_system/engine/materializer.py`, add one line to the `_build_context` return dict (after `expected_outputs`):

```python
        "expected_outputs": list(task.get("expected_outputs", [])),
        "facets": dict(task.get("facets") or {}),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 python -m pytest tests/unit/engine/test_materializer_facets.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/engine/materializer.py tests/unit/engine/test_materializer_facets.py
git commit -m "feat: carry task facets into context_snapshot"
```

---

## Task 3: Agent prompt renders the facets

**Files:**
- Modify: `src/ai_dev_system/agents/claude_max_agent.py` (`_build_user`)
- Test: `tests/unit/agents/test_claude_max_agent_facets.py` (create)

**Interfaces:**
- Consumes: `context["facets"]` (the dict from Task 2).
- Produces: a `## Task Specification` section in the agent's user prompt, listing `filled` facets (skip `na`, flag `needs_human`). No section when no facets are filled/needs_human.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/agents/test_claude_max_agent_facets.py`:

```python
from ai_dev_system.agents.claude_max_agent import ClaudeMaxAgent


def _agent():
    return ClaudeMaxAgent.__new__(ClaudeMaxAgent)  # no __init__ needed for _build_user


def test_filled_facets_render_section():
    ctx = {"task_id": "T", "objective": "o", "facets": {
        "input": {"status": "filled", "content": "a CSV file", "reason": ""},
        "database": {"status": "na", "content": "", "reason": "stateless"},
        "auth_permission": {"status": "needs_human", "content": "", "reason": ""},
    }}
    out = _agent()._build_user("T", ctx, [])
    assert "## Task Specification" in out
    assert "input: a CSV file" in out
    assert "database" not in out.split("## Task Specification")[1]  # na hidden
    assert "auth_permission" in out and "needs clarification" in out  # needs_human flagged


def test_no_section_when_no_useful_facets():
    ctx = {"task_id": "T", "objective": "o", "facets": {
        "input": {"status": "na", "content": "", "reason": "n/a"},
    }}
    out = _agent()._build_user("T", ctx, [])
    assert "## Task Specification" not in out


def test_no_section_when_facets_absent():
    out = _agent()._build_user("T", {"task_id": "T", "objective": "o"}, [])
    assert "## Task Specification" not in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 python -m pytest tests/unit/agents/test_claude_max_agent_facets.py -q`
Expected: FAIL — no `## Task Specification`.

- [ ] **Step 3: Implement**

In `src/ai_dev_system/agents/claude_max_agent.py`, inside `_build_user`, before the final `return "\n".join(lines)`, add a facet section:

```python
        facets = context.get("facets") or {}
        facet_lines = []
        for key, facet in facets.items():
            if not isinstance(facet, dict):
                continue
            status = facet.get("status")
            if status == "filled" and facet.get("content"):
                facet_lines.append(f"- {key}: {facet['content']}")
            elif status == "needs_human":
                facet_lines.append(f"- {key}: (needs clarification — confirm before relying on it)")
            # status == "na" → skip
        if facet_lines:
            lines.append("")
            lines.append("## Task Specification")
            lines.extend(facet_lines)

        return "\n".join(lines)
```

(Replace the existing bare `return "\n".join(lines)` with the block above.)

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 python -m pytest tests/unit/agents/test_claude_max_agent_facets.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/agents/claude_max_agent.py tests/unit/agents/test_claude_max_agent_facets.py
git commit -m "feat: render task facets into the coding agent prompt"
```

---

## Task 4: Gate 2 facet review (commands + summary + approve warning)

**Files:**
- Modify: `src/ai_dev_system/gate/terminal_gate2.py`
- Test: `tests/unit/gate/test_terminal_gate2_facets.py` (create)

**Interfaces:**
- Consumes: tasks that may carry `task["facets"]` (from Task 1).
- Produces: `facet` command verb (`facet show <ID>`, `facet set <ID> <key> <text>`, `facet na <ID> <key> <reason>`); facet summary line in `_render`; a warning (not a block) on `approve` when any `needs_human` facet remains.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/gate/test_terminal_gate2_facets.py`:

```python
import io

from ai_dev_system.gate.terminal_gate2 import TerminalGate2IO


def _envelope():
    # Use the real 4-task skeleton so `approve` (which re-runs validate_graph)
    # passes; attach facets to the coding task.
    from ai_dev_system.task_graph.skeleton import build_skeleton
    tasks = build_skeleton()
    impl = next(t for t in tasks if t["id"] == "TASK-IMPL")
    impl["facets"] = {
        "input": {"status": "needs_human", "content": "", "reason": ""},
        "database": {"status": "filled", "content": "adds users table", "reason": ""},
    }
    return {"tasks": tasks}


def _run(commands):
    it = iter(commands)
    out = io.StringIO()
    gate = TerminalGate2IO(prompt_fn=lambda *a: next(it), out=out)
    status, edited = gate.collect_edits(_envelope())
    return status, edited, out.getvalue()


def test_facet_set_marks_filled():
    status, edited, _ = _run(["facet set TASK-IMPL input 'a CSV upload'", "approve"])
    f = edited["tasks"][0]["facets"]["input"]
    assert f["status"] == "filled" and f["content"] == "a CSV upload"
    assert status == "approve"


def test_facet_na_marks_na_with_reason():
    status, edited, _ = _run(["facet na TASK-IMPL database 'no persistence'", "approve"])
    f = edited["tasks"][0]["facets"]["database"]
    assert f["status"] == "na" and f["reason"] == "no persistence"


def test_facet_show_renders_facets():
    _, _, text = _run(["facet show TASK-IMPL", "approve"])
    assert "input" in text and "needs_human" in text


def test_approve_warns_on_needs_human_but_proceeds():
    status, _, text = _run(["approve"])
    assert status == "approve"           # not blocked
    assert "needs_human" in text or "needs clarification" in text  # warned


def test_render_shows_facet_summary():
    _, _, text = _run(["list", "approve"])
    assert "facets:" in text  # e.g. "facets: 1 filled / 1 needs-human / 0 N/A"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 python -m pytest tests/unit/gate/test_terminal_gate2_facets.py -q`
Expected: FAIL — `facet` unknown command / no summary.

- [ ] **Step 3: Implement**

In `src/ai_dev_system/gate/terminal_gate2.py`:

(a) Import the canonical facet keys at the top of the file (reuse, don't redefine), next to the existing `from ai_dev_system.task_graph.validator import validate_graph`:

```python
from ai_dev_system.task_graph.facets import FACET_KEYS
```

(b) Add to `_HELP` (before `"  approve ..."`):

```python
    "  facet show <ID>           show a task's 8 facets\n"
    "  facet set <ID> <key> <v>  fill/replace a facet (status→filled)\n"
    "  facet na <ID> <key> <why> mark a facet not-applicable\n"
```

(b) In `_dispatch`, add a branch before the unknown-command fallthrough:

```python
        if verb == "facet" and len(parts) >= 3:
            self._facet(tasks, parts[1].lower(), parts[2],
                        parts[3] if len(parts) >= 4 else "",
                        " ".join(parts[4:]) if len(parts) >= 5 else "")
            return None
```

(c) Make `approve` warn on remaining `needs_human`. Replace the approve branch body with:

```python
        if verb in ("approve", "confirm", "a"):
            errors = validate_graph(tasks)
            if errors:
                self._emit("Cannot approve — graph is invalid:")
                for e in errors:
                    self._emit(f"  - {e}")
                return None
            pending = self._needs_human_facets(tasks)
            if pending:
                self._emit(f"[gate2] WARNING: {len(pending)} facet(s) still needs_human:")
                for tid, key in pending:
                    self._emit(f"  - {tid}.{key}")
                self._emit("Approving anyway (facets are advisory).")
            return ("approve", edited)
```

(d) Add the helper methods (near `_set`). They use the imported `FACET_KEYS` (no local redefinition):

```python
    def _needs_human_facets(self, tasks):
        out = []
        for t in tasks:
            for key, f in (t.get("facets") or {}).items():
                if isinstance(f, dict) and f.get("status") == "needs_human":
                    out.append((t["id"], key))
        return out

    def _facet(self, tasks, op, tid, key, value):
        task = self._find(tasks, tid)
        if task is None:
            self._emit(f"No such task: {tid}")
            return
        facets = task.setdefault("facets", {})
        if op == "show":
            self._emit(f"--- {tid} facets ---")
            for k in FACET_KEYS:
                f = facets.get(k) or {"status": "needs_human", "content": "", "reason": ""}
                self._emit(f"  {k}: [{f.get('status')}] {f.get('content') or f.get('reason')}")
            return
        if key not in FACET_KEYS:
            self._emit(f"Unknown facet {key!r}. Valid: {', '.join(FACET_KEYS)}")
            return
        if op == "set":
            facets[key] = {"status": "filled", "content": value, "reason": ""}
            self._emit(f"{tid}.{key} = filled: {value!r}")
        elif op == "na":
            facets[key] = {"status": "na", "content": "", "reason": value}
            self._emit(f"{tid}.{key} = na ({value!r})")
        else:
            self._emit("Usage: facet show|set|na <ID> [<key> <value>]")
```

(e) In `_render`, after the objective line, add a facet summary when the task has facets:

```python
            facets = t.get("facets")
            if facets:
                filled = sum(1 for f in facets.values() if isinstance(f, dict) and f.get("status") == "filled")
                nh = sum(1 for f in facets.values() if isinstance(f, dict) and f.get("status") == "needs_human")
                na = sum(1 for f in facets.values() if isinstance(f, dict) and f.get("status") == "na")
                self._emit(f"      facets: {filled} filled / {nh} needs-human / {na} N/A")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONUTF8=1 python -m pytest tests/unit/gate/test_terminal_gate2_facets.py -q`
Expected: PASS.

Then run the existing Gate 2 suite for regressions:
Run: `PYTHONUTF8=1 python -m pytest tests/unit/gate -q -k gate2`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/gate/terminal_gate2.py tests/unit/gate/test_terminal_gate2_facets.py
git commit -m "feat: Gate 2 facet review (show/set/na + needs_human approve warning)"
```

---

## Task 5: Phase 2 wiring — attach facets before promotion

**Files:**
- Modify: `src/ai_dev_system/debate_pipeline.py` (Phase 2, around the `generate_task_graph` call)
- Test: `tests/unit/test_phase2_facets_wiring.py` (create — tests the profile loader helper)

**Interfaces:**
- Consumes: `generate_task_facets_for_graph` (Task 1), the `spec_content` dict and `llm_client` already in scope, and the run's `current_artifacts`.
- Produces: `envelope["tasks"]` carry `facets` before `TASK_GRAPH_GENERATED` is promoted.

- [ ] **Step 1: Write the failing test (profile loader helper)**

Create `tests/unit/test_phase2_facets_wiring.py`:

```python
import json
from pathlib import Path

from ai_dev_system.debate_pipeline import _load_project_profile_dict


def test_load_profile_returns_dict_from_debate_report(tmp_path):
    report = {"brief": {"_project_profile": {"vertical": "couples app", "key_dimensions": ["x"]}}}
    art_dir = tmp_path / "art"
    art_dir.mkdir()
    (art_dir / "debate_report.json").write_text(json.dumps(report), encoding="utf-8")

    class _Conn:
        def execute(self, *a):
            class _C:
                def fetchone(self_):
                    return {"content_ref": str(art_dir)}
            return _C()
    profile = _load_project_profile_dict(_Conn(), {"debate_report_id": "abc"})
    assert profile["vertical"] == "couples app"


def test_load_profile_none_when_missing():
    class _Conn:
        def execute(self, *a):
            class _C:
                def fetchone(self_):
                    return None
            return _C()
    assert _load_project_profile_dict(_Conn(), {}) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 python -m pytest tests/unit/test_phase2_facets_wiring.py -q`
Expected: FAIL — `_load_project_profile_dict` not defined.

- [ ] **Step 3: Implement the helper + wiring**

In `src/ai_dev_system/debate_pipeline.py`:

(a) Add the import near the other task_graph imports:

```python
from ai_dev_system.task_graph.facets import generate_task_facets_for_graph
```

(b) Add the resilient helper (module level, near `_load_intake_brief`):

```python
def _load_project_profile_dict(conn, current_artifacts: dict) -> dict | None:
    """Read brief._project_profile from the run's DEBATE_REPORT artifact, or None.

    Lets per-task facet generation carry the vertical flavor (Spec 1). Never raises.
    """
    try:
        report_id = (current_artifacts or {}).get("debate_report_id")
        if not report_id:
            return None
        row = conn.execute(
            "SELECT content_ref FROM artifacts WHERE artifact_id = ?", (report_id,)
        ).fetchone()
        if row is None:
            return None
        path = Path(row["content_ref"]) / "debate_report.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        profile = (data.get("brief") or {}).get("_project_profile")
        return profile if isinstance(profile, dict) else None
    except Exception:
        return None
```

(c) In the Phase 2 body, right after `envelope = generate_task_graph(...)` and BEFORE `_write_json_to_temp_debate(...)`, attach facets:

```python
    envelope = generate_task_graph(spec_content, approved_answers, spec_artifact_id, llm_client)
    profile_dict = _load_project_profile_dict(conn, current_artifacts)
    generate_task_facets_for_graph(envelope["tasks"], spec_content, profile_dict, llm_client)
    temp_tg = _write_json_to_temp_debate(config, task_run_tg, envelope)
```

(`current_artifacts` is already loaded earlier in this function — confirm the variable name in scope and reuse it; if it is named differently, match the actual code.)

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 python -m pytest tests/unit/test_phase2_facets_wiring.py -q`
Expected: PASS.

- [ ] **Step 5: Run the Phase B integration suite for regressions**

Run: `PYTHONUTF8=1 python -m pytest tests/integration -q`
Expected: PASS — under the stub LLM, facets resolve to `needs_human` and execution still completes. If a Phase B test asserts the exact task-graph envelope shape and now fails only because `facets` keys were added to coding tasks, update that assertion to tolerate the new key (do not remove the facet step).

- [ ] **Step 6: Commit**

```bash
git add src/ai_dev_system/debate_pipeline.py tests/unit/test_phase2_facets_wiring.py
git commit -m "feat: attach per-task facets in Phase 2 before Gate 2"
```

---

## Task 6: Full-suite regression

**Files:** none (verification + any stale-assertion fixes only).

- [ ] **Step 1: Run the full unit suite**

Run: `PYTHONUTF8=1 python -m pytest tests/unit -q`
Expected: PASS. If a pre-existing test fails ONLY because coding tasks now carry a `facets` key (exact-dict equality on a task), update that assertion minimally to tolerate the key. If a failure is a real behavioral regression, STOP and report it — do not paper over.

- [ ] **Step 2: Run the integration suite**

Run: `PYTHONUTF8=1 python -m pytest tests/integration -q`
Expected: PASS.

- [ ] **Step 3: Commit any stale-assertion fixes**

```bash
git add <only the specific test files changed>
git commit -m "test: tolerate facets key on coding tasks"
```

(Skip the commit if nothing changed.)

---

## Manual verification (after all tasks)

1. With a real Claude Max client, run a project through to Gate 2.
2. At Gate 2, run `facet show TASK-IMPL` — confirm the 8 facets are populated (or `needs_human`), `facet set`/`facet na` edit them, and `approve` warns about any remaining `needs_human`.
3. Confirm the executing agent's prompt (in the run log) contains a `## Task Specification` section with the facets.
4. Under stub mode, confirm Phase B still completes (facets all `needs_human`, no section rendered).

## Self-Review

- [ ] **Spec coverage:** facet model+generator (T1), materializer carry (T2), agent render (T3), Gate 2 review (T4), Phase 2 wiring + Spec-1 profile compose (T5), regression (T6). Spec §4.1–§4.6 mapped. Out-of-scope items (standalone task entry, facet debate, testing-task facets) correctly excluded.
- [ ] **Placeholder scan:** none — every step has concrete code/commands.
- [ ] **Type consistency:** `FACET_KEYS`, the facet dict shape `{status,content,reason}`, `is_implementation_task`, `generate_task_facets(_for_graph)`, `_load_project_profile_dict`, and `complete(system,user)` are consistent across T1–T5.
- [ ] **Known pre-existing issue (NOT in scope):** `task_graph/enricher.py` calls `llm.complete(prompt)` (single arg) which fails on every real client → enrichment is a silent no-op. Documented here; not fixed by this plan.
