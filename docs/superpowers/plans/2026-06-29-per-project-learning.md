# Per-Project Personalized Learning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the failure-learning loop two-tier — universal lessons stay global, project-specific lessons live inside the target repo at `<repo>/.ai-dev/rules/` — and close the broken link where learned lessons never reach the implementer on the TDD-first `claude -p` path.

**Architecture:** A new project-tier rule loader reads `<repo>/.ai-dev/rules/*.yaml` and matches it against the task context the same way the global `RuleRegistry` does. The two production agents (`RepoBranchAgent`, `TestAuthorAgent`) — which today silently drop the `file_rules` they are handed — inject global + project lessons into their prompt. On a human reject, the lesson is written to the project tier (resolved from the spec's repo) instead of the global package dir. A DO-NOT-SAVE guardrail keeps transient/infra failures out of the durable rule files.

**Tech Stack:** Python 3.11+, PyYAML, SQLite (stdlib), pytest. No new dependencies. No DB schema change.

**Spec:** [docs/superpowers/specs/2026-06-29-per-project-learning-design.md](../specs/2026-06-29-per-project-learning-design.md)

## Global Constraints

- **Env flag:** `AI_DEV_PROJECT_RULES` (default **on**). Values `0` / `false` / `off` / `no` (case-insensitive) disable the project tier → exact current behaviour (safe rollback).
- **Storage location:** project lessons live at `<repo>/.ai-dev/rules/learned-<scope>.yaml`, committed in the target repo. Global lessons stay at `src/ai_dev_system/rules/definitions/`.
- **Scope by location, not schema:** do NOT add a `project_id` field to `applies_to` and do NOT change `RuleRegistry.match_rules` semantics or the DB schema. The tier is the directory.
- **Learning must never break execution:** all new loaders/writers are best-effort and must never raise into the task path (mirror the existing `# noqa: BLE001 - learning must never break …` pattern).
- **Lesson text cap:** lessons remain ≤ `MAX_LESSON_LEN` (280) chars (existing `_clean`).
- **YAML shape is identical across tiers:** `{name, applies_to: {task_types, tags}, file_rules, skill_rules}`.
- **Test style:** match existing tests — patch `ai_dev_system.agents.repo_branch_agent.subprocess.Popen` / `.subprocess.run` and `ClaudeCodeLLMClient._resolve_claude_cmd`; use `tmp_path`; pytest.

### Deviation from spec (intentional, lower-risk)

The spec described moving `RuleRegistry` into a per-run, multi-dir construction inside the worker. This plan instead loads the **project tier in the agent** (which already holds `repo_path` and the resolved `context` with `type`/`tags`). This achieves the two-tier behaviour with **localized, robust** changes and no engine/worker plumbing. Two follow-ups remain out of scope here and are listed at the end: (a) per-run reload of the **global** registry (defect #3, intra-run staleness), (b) `review.json` mining.

---

### Task 1: Inject `file_rules` into agent prompts (P0 — closes the open loop)

Today `RepoBranchAgent.run` and `TestAuthorAgent.run` accept `file_rules` and never use it (verified: the name appears only in the signature). This task makes both prompt builders render the lessons, and has the agents pass the lessons they already receive. This is the prerequisite that makes every later task observable.

**Files:**
- Modify: `src/ai_dev_system/agents/repo_branch_agent.py` (add `_format_lessons`; change `_build_execution_prompt`; change `RepoBranchAgent.run`)
- Modify: `src/ai_dev_system/agents/test_author_agent.py` (import `_format_lessons`; change `_build_test_prompt`; change `TestAuthorAgent.run`)
- Test: `tests/unit/agents/test_lessons_injection.py` (new)

**Interfaces:**
- Produces: `_format_lessons(file_rules) -> str` in `repo_branch_agent.py` — returns `""` for empty input, else a `"## LESSONS FROM PAST FAILURES (apply these)"` block. Used by Task 3 and imported by `test_author_agent.py`.
- Produces: `_build_execution_prompt(context: dict, file_rules=()) -> str` and `_build_test_prompt(context: dict, file_rules=()) -> str` (new optional second parameter, back-compatible).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/agents/test_lessons_injection.py`:

```python
"""Learned lessons (file_rules) are rendered into agent prompts (closes the
open learning loop where RepoBranchAgent/TestAuthorAgent dropped file_rules)."""
from __future__ import annotations

from ai_dev_system.agents.repo_branch_agent import (
    _format_lessons,
    _build_execution_prompt,
)
from ai_dev_system.agents.test_author_agent import _build_test_prompt


def _ctx() -> dict:
    return {
        "objective": "Add login",
        "description": "JWT login",
        "done_definition": "returns JWT",
        "type": "coding",
        "facets": {},
    }


def test_format_lessons_empty_is_blank():
    assert _format_lessons([]) == ""
    assert _format_lessons(None) == ""


def test_format_lessons_renders_block():
    block = _format_lessons(["Run migrations before integration tests"])
    assert "LESSONS FROM PAST FAILURES" in block
    assert "Run migrations before integration tests" in block


def test_execution_prompt_includes_lessons():
    p = _build_execution_prompt(_ctx(), ["Always validate the email field"])
    assert "LESSONS FROM PAST FAILURES" in p
    assert "Always validate the email field" in p


def test_execution_prompt_without_lessons_has_no_block():
    p = _build_execution_prompt(_ctx())
    assert "LESSONS FROM PAST FAILURES" not in p


def test_test_prompt_includes_lessons():
    p = _build_test_prompt(_ctx(), ["Cover the 401 path"])
    assert "LESSONS FROM PAST FAILURES" in p
    assert "Cover the 401 path" in p
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/unit/agents/test_lessons_injection.py -v`
Expected: FAIL — `ImportError: cannot import name '_format_lessons'` (and `_build_*_prompt` rejects the second positional arg).

- [ ] **Step 3: Add `_format_lessons` and thread it into `_build_execution_prompt`**

In `src/ai_dev_system/agents/repo_branch_agent.py`, add this helper immediately above `def _build_execution_prompt(` (currently at line 90):

```python
def _format_lessons(file_rules) -> str:
    """Render learned lessons (file_rules) as a prompt block.

    Returns "" when there are no lessons, so callers can unconditionally append
    it. Lessons are corrective rules mined from earlier failed attempts — the
    agent MUST honour them (this is the seam that closes the learning loop).
    """
    rules = [str(r).strip() for r in (file_rules or []) if str(r).strip()]
    if not rules:
        return ""
    bullets = "\n".join(f"- {r}" for r in rules)
    return (
        "\n## LESSONS FROM PAST FAILURES (apply these)\n"
        "Corrective rules learned from earlier failed attempts on this work. "
        "Honour every one:\n"
        f"{bullets}\n"
    )
```

Then change `_build_execution_prompt` to accept `file_rules` and append the block. Replace the signature line and the final `return (...)` so the function reads:

```python
def _build_execution_prompt(context: dict, file_rules=()) -> str:
    facets = context.get("facets") or {}
    filled_lines: list[str] = []
    for key in SPEC_FACET_KEYS:
        f = facets.get(key) or {}
        if f.get("status") == "filled" and f.get("content", "").strip():
            filled_lines.append(f"### {key}\n{f['content']}")

    spec_section = "\n\n".join(filled_lines) if filled_lines else "(no spec facets filled)"

    base = (
        "You are implementing a coding task in THIS repository. "
        "Read existing code to understand patterns and conventions before writing anything. "
        "Tests already exist on this branch and are currently FAILING — implement the "
        "feature until they pass, then commit.\n\n"
        f"## Task\n"
        f"**Objective:** {context.get('objective', '')}\n"
        f"**Description:** {context.get('description', '')}\n"
        f"**Done when:** {context.get('done_definition', '')}\n\n"
        f"## Technical Specification\n{spec_section}\n\n"
        "## Rules\n"
        "- Follow existing code style and patterns in this repo\n"
        "- Tests already exist and are RED — make them pass; do NOT delete or weaken "
        "tests to make them pass\n"
        "- You MAY edit a test ONLY if it is genuinely wrong; if so, explain why in the "
        "commit message\n"
        "- Run the full test suite before committing\n"
        "- Commit with: `git add -A && git commit -m '<type>: <summary>'`\n"
        "- Do NOT push to remote\n"
    )
    return base + _format_lessons(file_rules)
```

- [ ] **Step 4: Thread `_format_lessons` into the test-author prompt**

In `src/ai_dev_system/agents/test_author_agent.py`, extend the existing import from `repo_branch_agent` (lines 18-20) to include `_format_lessons`:

```python
from ai_dev_system.agents.repo_branch_agent import (
    _invoke_claude, _append_log, _max_turns, _git, _extract_summary, _format_lessons,
)
```

Then change `_build_test_prompt` to accept `file_rules` and append the block. The function currently ends with the `## Rules` string — wrap its return:

```python
def _build_test_prompt(context: dict, file_rules=()) -> str:
    base = (
        "You are writing TESTS for a coding task in THIS repository, BEFORE any "
        "implementation exists (test-driven development). Read existing test files "
        "to match the project's test framework and conventions first.\n\n"
        "## Task\n"
        f"**Objective:** {context.get('objective', '')}\n"
        f"**Description:** {context.get('description', '')}\n"
        f"**Done when:** {context.get('done_definition', '')}\n\n"
        "## Acceptance source — your tests MUST encode these\n"
        f"{build_test_source(context)}\n\n"
        "## Rules\n"
        "- Write ONLY tests. Do NOT implement the feature.\n"
        "- Each acceptance item must have a test asserting the OBSERVABLE behaviour "
        "(not implementation detail).\n"
        "- Run the tests. They MUST FAIL (RED) because the implementation is absent. "
        "Confirm they fail for the right reason (assertion / missing symbol), not a "
        "syntax error.\n"
        "- Commit with: `git add -A && git commit -m 'test: <summary>'`\n"
        "- Do NOT push to remote.\n"
    )
    return base + _format_lessons(file_rules)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m pytest tests/unit/agents/test_lessons_injection.py -v`
Expected: PASS (5 passed)

- [ ] **Step 6: Pass `file_rules` from each agent's `run` into its prompt builder**

In `src/ai_dev_system/agents/repo_branch_agent.py`, `RepoBranchAgent.run`, change the `run1 = _invoke_claude(...)` call (line ~337) to pass `file_rules` into the builder:

```python
        run1 = _invoke_claude(
            claude, self.repo_path, _build_execution_prompt(context, file_rules),
            max_turns, timeout_s, self.live_log_path, model=model, effort=effort,
        )
```

In `src/ai_dev_system/agents/test_author_agent.py`, `TestAuthorAgent.run`, change the `run1 = _invoke_claude(...)` call (line ~142):

```python
        run1 = _invoke_claude(
            claude, self.repo_path, _build_test_prompt(context, file_rules),
            _max_turns(), timeout_s, self.live_log_path, model=model, effort=effort,
        )
```

- [ ] **Step 7: Add a test proving the lesson reaches the CLI prompt, then run all agent tests**

Append to `tests/unit/agents/test_lessons_injection.py`:

```python
import json
from unittest.mock import patch
from ai_dev_system.agents.repo_branch_agent import RepoBranchAgent


def _capture_prompt_run(agent, monkeypatch, file_rules):
    monkeypatch.setenv("EXEC_REVIEW_GATE", "0")  # implementer in isolation
    captured = {}

    def _fake_popen(cmd, **kw):
        captured["cmd"] = cmd

        class FakePopen:
            returncode = 0
            stdout = iter([json.dumps({"type": "result", "result": "Done."}) + "\n"])
            stderr = iter([])

            def wait(self, timeout=None):
                self.returncode = 0

        return FakePopen()

    def _fake_run(cmd, **kw):
        import subprocess
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="(no diff)", stderr="")

    with patch("ai_dev_system.agents.repo_branch_agent.ClaudeCodeLLMClient._resolve_claude_cmd",
               return_value="claude"), \
         patch("ai_dev_system.agents.repo_branch_agent.subprocess.Popen", side_effect=_fake_popen), \
         patch("ai_dev_system.agents.repo_branch_agent.subprocess.run", side_effect=_fake_run):
        agent.run("TASK-ADHOC", str(agent.repo_path) + "/out", context=_ctx(), file_rules=file_rules)
    return captured["cmd"]


def test_repo_branch_run_puts_lesson_in_cli_prompt(tmp_path, monkeypatch):
    agent = RepoBranchAgent(str(tmp_path), "ai-dev/task-abc", "main")
    cmd = _capture_prompt_run(agent, monkeypatch, ["Never log secrets"])
    # cmd == [claude, "-p", PROMPT, ...]; the prompt carries the lesson.
    assert "Never log secrets" in cmd[2]
```

Run: `python -m pytest tests/unit/agents/test_lessons_injection.py tests/unit/agents/test_repo_branch_agent.py tests/unit/agents/test_test_author_agent.py -v`
Expected: PASS (all)

- [ ] **Step 8: Commit**

```bash
git add src/ai_dev_system/agents/repo_branch_agent.py src/ai_dev_system/agents/test_author_agent.py tests/unit/agents/test_lessons_injection.py
git commit -m "fix(learning): inject file_rules into RepoBranch/TestAuthor prompts (close open loop)"
```

---

### Task 2: Project-tier rule loader

A standalone module that resolves `<repo>/.ai-dev/rules/` and matches its YAML rules against a task's context, reusing `RuleRegistry`. Independently testable with no agent wiring yet.

**Files:**
- Create: `src/ai_dev_system/rules/project_rules.py`
- Test: `tests/unit/rules/test_project_rules.py` (new)

**Interfaces:**
- Produces: `project_rules_dir(repo_path) -> Path` → `<repo>/.ai-dev/rules` (does not create it).
- Produces: `load_project_file_rules(repo_path, context: dict) -> list[str]` → matched `file_rules`, or `[]` when disabled / missing dir / no match. Never raises. Consumed by Task 3 (agents) and Task 4 (`project_rules_dir` for the write path).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/rules/test_project_rules.py`:

```python
"""Project-tier learned-rule loader (<repo>/.ai-dev/rules)."""
from __future__ import annotations

from pathlib import Path

import yaml

from ai_dev_system.rules.project_rules import project_rules_dir, load_project_file_rules


def _write_rule(repo: Path, name: str, task_types, file_rules):
    d = repo / ".ai-dev" / "rules"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.yaml").write_text(
        yaml.safe_dump(
            {"name": name, "applies_to": {"task_types": task_types, "tags": []},
             "file_rules": file_rules, "skill_rules": []},
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_project_rules_dir():
    assert project_rules_dir("/repo") == Path("/repo", ".ai-dev", "rules")


def test_no_dir_returns_empty(tmp_path):
    assert load_project_file_rules(str(tmp_path), {"type": "coding"}) == []


def test_matches_by_type(tmp_path):
    _write_rule(tmp_path, "learned-coding", ["coding"], ["Validate inputs"])
    assert load_project_file_rules(str(tmp_path), {"type": "coding"}) == ["Validate inputs"]


def test_no_match_other_type(tmp_path):
    _write_rule(tmp_path, "learned-coding", ["coding"], ["Validate inputs"])
    assert load_project_file_rules(str(tmp_path), {"type": "docs"}) == []


def test_disabled_env_returns_empty(tmp_path, monkeypatch):
    _write_rule(tmp_path, "learned-coding", ["coding"], ["Validate inputs"])
    monkeypatch.setenv("AI_DEV_PROJECT_RULES", "0")
    assert load_project_file_rules(str(tmp_path), {"type": "coding"}) == []


def test_empty_repo_path_returns_empty():
    assert load_project_file_rules("", {"type": "coding"}) == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/unit/rules/test_project_rules.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ai_dev_system.rules.project_rules'`

- [ ] **Step 3: Write the module**

Create `src/ai_dev_system/rules/project_rules.py`:

```python
# src/ai_dev_system/rules/project_rules.py
"""Project-tier learned rules: lessons stored INSIDE the target repo.

The failure-learning loop is two-tier, separated by location:

* GLOBAL  — rules shipped with the tool (``rules/definitions/``), matched by the
  worker and handed to the agent as ``file_rules``.
* PROJECT — lessons learned from THIS repo's own runs, committed in the target
  repo at ``<repo>/.ai-dev/rules/``, loaded here.

Both tiers share the identical YAML shape and are matched the same way
(``task_type``/``tags``) via ``RuleRegistry`` — the only difference is location.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from ai_dev_system.rules.registry import RuleRegistry

logger = logging.getLogger(__name__)

# <repo>/.ai-dev/rules
_PROJECT_RULES_SUBDIR = (".ai-dev", "rules")


def project_rules_dir(repo_path: str | Path) -> Path:
    """Return ``<repo>/.ai-dev/rules`` for a target repo (not created here)."""
    return Path(repo_path, *_PROJECT_RULES_SUBDIR)


def _enabled() -> bool:
    """Project tier is ON by default; ``AI_DEV_PROJECT_RULES`` in
    {0,false,off,no} (case-insensitive) disables it."""
    raw = os.environ.get("AI_DEV_PROJECT_RULES")
    if raw is None:
        return True
    return raw.strip().lower() not in ("0", "false", "off", "no")


def load_project_file_rules(repo_path: str | Path, context: dict) -> list[str]:
    """Match the target repo's project-tier rules against this task's context.

    Returns the ``file_rules`` whose ``applies_to`` matches the task's
    ``type``/``tags``, or ``[]`` when the tier is disabled, the repo has no
    ``.ai-dev/rules`` dir, or nothing matches. Never raises — a broken project
    rule file must never fail the task.
    """
    if not _enabled() or not repo_path:
        return []
    rules_dir = project_rules_dir(repo_path)
    if not rules_dir.is_dir():
        return []
    try:
        registry = RuleRegistry(rules_dir=rules_dir)
        match_task = {
            "task_type": (context.get("type") or "").strip(),
            "tags": list(context.get("tags") or []),
        }
        return registry.match_rules(match_task).file_rules
    except Exception:  # noqa: BLE001 - project rules must never break execution
        logger.exception("Failed to load project rules from %s", rules_dir)
        return []
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/unit/rules/test_project_rules.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/rules/project_rules.py tests/unit/rules/test_project_rules.py
git commit -m "feat(learning): add project-tier rule loader (<repo>/.ai-dev/rules)"
```

---

### Task 3: Wire the project tier into both agents

Merge the worker's global `file_rules` with the project tier loaded from the repo, then inject the union. After this task the read side is fully two-tier.

**Files:**
- Modify: `src/ai_dev_system/agents/repo_branch_agent.py` (add `_merge_rules`; use it + `load_project_file_rules` in `RepoBranchAgent.run`)
- Modify: `src/ai_dev_system/agents/test_author_agent.py` (import `_merge_rules`; use it + `load_project_file_rules` in `TestAuthorAgent.run`)
- Test: `tests/unit/agents/test_lessons_injection.py` (extend)

**Interfaces:**
- Consumes: `load_project_file_rules(repo_path, context)` from Task 2; `_format_lessons` from Task 1.
- Produces: `_merge_rules(global_rules, project_rules) -> list[str]` in `repo_branch_agent.py` (order-preserving union, global first). Imported by `test_author_agent.py`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/rules/test_project_rules.py` is not right — these are agent tests. Append to `tests/unit/agents/test_lessons_injection.py`:

```python
import yaml
from ai_dev_system.agents.repo_branch_agent import _merge_rules


def test_merge_rules_dedups_preserving_order():
    assert _merge_rules(["a"], ["a", "b"]) == ["a", "b"]
    assert _merge_rules([], ["x"]) == ["x"]
    assert _merge_rules(["g"], []) == ["g"]


def _write_project_rule(repo, task_types, file_rules):
    d = repo / ".ai-dev" / "rules"
    d.mkdir(parents=True, exist_ok=True)
    (d / "learned-coding.yaml").write_text(
        yaml.safe_dump(
            {"name": "learned-coding",
             "applies_to": {"task_types": task_types, "tags": []},
             "file_rules": file_rules, "skill_rules": []},
            sort_keys=False),
        encoding="utf-8",
    )


def test_run_injects_project_tier_lesson(tmp_path, monkeypatch):
    _write_project_rule(tmp_path, ["coding"], ["Project lesson Z"])
    agent = RepoBranchAgent(str(tmp_path), "ai-dev/task-abc", "main")
    # No global file_rules — the lesson must come purely from the project tier.
    cmd = _capture_prompt_run(agent, monkeypatch, [])
    assert "Project lesson Z" in cmd[2]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/unit/agents/test_lessons_injection.py -k "merge_rules or project_tier" -v`
Expected: FAIL — `ImportError: cannot import name '_merge_rules'` and the project lesson is absent from the prompt.

- [ ] **Step 3: Add `_merge_rules` and use the project tier in `RepoBranchAgent.run`**

In `src/ai_dev_system/agents/repo_branch_agent.py`, add the helper next to `_format_lessons`:

```python
def _merge_rules(global_rules, project_rules) -> list[str]:
    """Order-preserving union of global + project lessons (global first)."""
    merged: list[str] = []
    for r in list(global_rules or []) + list(project_rules or []):
        s = str(r).strip()
        if s and s not in merged:
            merged.append(s)
    return merged
```

Add the import near the top of the file (with the other `ai_dev_system` imports):

```python
from ai_dev_system.rules.project_rules import load_project_file_rules
```

In `RepoBranchAgent.run`, compute the effective rules right after `context = context or {}` (line ~325) and pass them into the builder:

```python
        project_rules = load_project_file_rules(self.repo_path, context)
        effective_rules = _merge_rules(file_rules, project_rules)
```

then change the `run1` call to use `effective_rules`:

```python
        run1 = _invoke_claude(
            claude, self.repo_path, _build_execution_prompt(context, effective_rules),
            max_turns, timeout_s, self.live_log_path, model=model, effort=effort,
        )
```

- [ ] **Step 4: Use the project tier in `TestAuthorAgent.run`**

In `src/ai_dev_system/agents/test_author_agent.py`, add `_merge_rules` to the `repo_branch_agent` import and import the loader:

```python
from ai_dev_system.agents.repo_branch_agent import (
    _invoke_claude, _append_log, _max_turns, _git, _extract_summary,
    _format_lessons, _merge_rules,
)
from ai_dev_system.rules.project_rules import load_project_file_rules
```

In `TestAuthorAgent.run`, after `context = context or {}` (line ~130), compute the effective rules and pass them in:

```python
        project_rules = load_project_file_rules(self.repo_path, context)
        effective_rules = _merge_rules(file_rules, project_rules)
```

```python
        run1 = _invoke_claude(
            claude, self.repo_path, _build_test_prompt(context, effective_rules),
            _max_turns(), timeout_s, self.live_log_path, model=model, effort=effort,
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/agents/test_lessons_injection.py tests/unit/rules/test_project_rules.py -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add src/ai_dev_system/agents/repo_branch_agent.py src/ai_dev_system/agents/test_author_agent.py tests/unit/agents/test_lessons_injection.py
git commit -m "feat(learning): agents inject global + project-tier lessons"
```

---

### Task 4: Write learned lessons to the project tier on reject

On a human reject (the learning trigger on the single-task path), resolve the target repo from the spec and write the lesson to `<repo>/.ai-dev/rules/` instead of the global package dir. Falls back to the global dir when the repo can't be resolved, preserving today's behaviour.

**Files:**
- Modify: `src/ai_dev_system/webui.py` (add `_project_rules_dir_for_spec`; route `_learn_from_rejection`)
- Test: `tests/unit/test_webui_reject_learning.py` (extend)

**Interfaces:**
- Consumes: `project_rules_dir(repo_path)` from Task 2.
- Produces: `_project_rules_dir_for_spec(spec_id) -> Optional[Path]` in `webui.py`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_webui_reject_learning.py`:

```python
import json
from pathlib import Path

import ai_dev_system.webui as webui


def test_project_rules_dir_for_spec_resolves_repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    specs = tmp_path / "task_specs"
    specs.mkdir()
    (specs / "spec1.json").write_text(json.dumps({"repo": str(repo)}), encoding="utf-8")
    monkeypatch.setattr(webui, "_config",
                        lambda: Config(storage_root=str(tmp_path), database_url="sqlite:///x"))

    got = webui._project_rules_dir_for_spec("spec1")
    assert got == Path(repo, ".ai-dev", "rules")


def test_project_rules_dir_for_spec_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(webui, "_config",
                        lambda: Config(storage_root=str(tmp_path), database_url="sqlite:///x"))
    assert webui._project_rules_dir_for_spec("nope") is None


def test_reject_writes_to_project_tier(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'c.db'}"
    run_id, tr = _seed_failed_task(url)
    repo = tmp_path / "repo"
    repo.mkdir()
    specs = tmp_path / "task_specs"
    specs.mkdir()
    (specs / "spec1.json").write_text(json.dumps({"repo": str(repo)}), encoding="utf-8")
    monkeypatch.setattr(webui, "_config",
                        lambda: Config(storage_root=str(tmp_path), database_url=url))

    # rules_dir NOT passed → must resolve to the project tier from the spec.
    name = webui._learn_from_rejection(
        "spec1", run_id, {"type": "coding", "tags": []}, "endpoint ignores auth check",
    )
    assert name and name.startswith("learned-")
    assert (repo / ".ai-dev" / "rules" / "learned-coding.yaml").exists()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/unit/test_webui_reject_learning.py -k "project_tier or for_spec" -v`
Expected: FAIL — `AttributeError: module 'ai_dev_system.webui' has no attribute '_project_rules_dir_for_spec'`

- [ ] **Step 3: Add the resolver and route the write path**

In `src/ai_dev_system/webui.py`, add (near `_learn_from_rejection`; ensure `import json` and `from pathlib import Path` are present at the top — `Path` already is):

```python
def _project_rules_dir_for_spec(spec_id: str):
    """Resolve <repo>/.ai-dev/rules from the task spec, or None if unavailable."""
    try:
        cfg = _config()
        spec_path = Path(cfg.storage_root) / "task_specs" / f"{spec_id}.json"
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        repo = spec.get("repo")
        if not repo:
            return None
        from ai_dev_system.rules.project_rules import project_rules_dir
        return project_rules_dir(repo)
    except Exception:  # noqa: BLE001 - resolution must never break reject
        return None
```

Then, inside `_learn_from_rejection`, change the `learn_from_failure(...)` call's `rules_dir` argument (currently `rules_dir=rules_dir or _RULES_DEFS_DIR`) to prefer the project tier:

```python
            target_dir = rules_dir or _project_rules_dir_for_spec(spec_id) or _RULES_DEFS_DIR
            result = learn_from_failure(
                conn, run_id, scope_task,
                rules_dir=target_dir, source="gate", rejection_reason=reason,
            )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/test_webui_reject_learning.py -v`
Expected: PASS (all — existing tests still pass because they pass `rules_dir=tmp_path` explicitly, which wins)

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/webui.py tests/unit/test_webui_reject_learning.py
git commit -m "feat(learning): write reject lessons to project tier (<repo>/.ai-dev/rules)"
```

---

### Task 5: DO-NOT-SAVE guardrail (anti-rot)

Stop transient/infra failures and negative "tool is broken" claims from becoming permanent rules (Hermes' "harden into refusals" failure mode). A conservative keyword filter applied to the lesson basis, logging every drop (no silent loss).

**Files:**
- Modify: `src/ai_dev_system/rules/learning.py` (add markers + `_is_transient_lesson`; filter in `lessons_from_verification` and `lesson_from_rejection`)
- Test: `tests/unit/rules/test_learning.py` (extend)

**Interfaces:**
- Produces: `_is_transient_lesson(text: str) -> bool` in `learning.py`. Internal; behaviour is observed through `lessons_from_verification` / `lesson_from_rejection`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/rules/test_learning.py`:

```python
from ai_dev_system.rules.learning import (
    lessons_from_verification, lesson_from_rejection, _is_transient_lesson,
)


class _Crit:
    def __init__(self, verdict, reasoning):
        self.verdict = verdict
        self.reasoning = reasoning
        self.criterion_text = reasoning


class _Report:
    overall = "HAS_FAIL"

    def __init__(self, criteria):
        self.criteria = criteria


def test_is_transient_lesson_flags_infra():
    assert _is_transient_lesson("connection timed out to localhost:5432")
    assert _is_transient_lesson("npm: command not found")
    assert not _is_transient_lesson("returns None instead of the computed total")


def test_verification_drops_transient_keeps_real():
    report = _Report([
        _Crit("FAIL", "connection timed out to the database"),
        _Crit("FAIL", "the function returns None instead of the computed total"),
    ])
    lessons = lessons_from_verification(report)
    assert any("computed total" in l for l in lessons)
    assert not any("timed out" in l for l in lessons)


def test_rejection_drops_transient():
    assert lesson_from_rejection("the build is flaky, just rerun it") == []


def test_rejection_keeps_real():
    assert lesson_from_rejection("missing input validation on the email field")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/unit/rules/test_learning.py -k "transient or drops or keeps_real" -v`
Expected: FAIL — `ImportError: cannot import name '_is_transient_lesson'`

- [ ] **Step 3: Add the guardrail and apply it**

In `src/ai_dev_system/rules/learning.py`, add after `MAX_LESSON_LEN` (line ~47):

```python
# Markers of a transient/environment-dependent failure or a negative "tool is
# broken" claim. These must NOT harden into a permanent rule (they "harden into
# self-citing refusals"). Borrowed from Hermes' background-review DO-NOT-SAVE
# guardrail. Conservative on purpose; every drop is logged (no silent loss).
_DO_NOT_SAVE_MARKERS = (
    "timed out", "connection refused", "connection reset", "econnreset",
    "rate limit exceeded", "429 ", "flaky", "transient",
    " 502 ", " 503 ", " 504 ", "command not found", "no such file",
    "permission denied", "module not found", "modulenotfounderror",
    "disk full", "out of memory", "is broken", "doesn't work", "does not work",
)
# NOTE (false-positive guard): markers are specific on purpose. Do NOT use bare
# "rate limit" / "timeout" — they collide with legitimate domain lessons like
# "output ignores rate limiting" (an existing test at test_learning.py:154 mints
# exactly that rule and MUST stay green). Prefer "rate limit exceeded" / "429".


def _is_transient_lesson(text: str) -> bool:
    low = (text or "").lower()
    return any(marker in low for marker in _DO_NOT_SAVE_MARKERS)
```

In `lessons_from_verification`, skip a transient basis. Change the loop body so that after computing `basis` (and before building the lesson) it drops transient ones:

```python
        if not basis:
            continue
        if _is_transient_lesson(basis):
            logger.info("Learning loop: dropping transient verification lesson: %s", basis[:80])
            continue
        lesson = _clean(f"Avoid repeating this failure: {basis}")
```

In `lesson_from_rejection`, drop transient reasons up front:

```python
def lesson_from_rejection(reason: str) -> list[str]:
    """Derive a lesson from a human gate / webui-Accept rejection reason."""
    cleaned = _clean(reason or "")
    if not cleaned:
        return []
    if _is_transient_lesson(cleaned):
        logger.info("Learning loop: dropping transient rejection lesson: %s", cleaned[:80])
        return []
    return [_clean(f"Reviewer rejected prior output: {cleaned}")]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/rules/test_learning.py -v`
Expected: PASS (all — existing learning tests unaffected; their reasons contain no markers)

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/rules/learning.py tests/unit/rules/test_learning.py
git commit -m "feat(learning): DO-NOT-SAVE guardrail drops transient/infra lessons"
```

---

### Task 6: Integration test — learn on run 1, apply on run 2 (closed-loop proof)

End-to-end proof that a lesson minted on one run (written to the project tier) is loaded and injected on the next run of the same repo. This is the test that proves the loop is closed.

**Files:**
- Test: `tests/integration/test_per_project_learning.py` (new)

**Interfaces:**
- Consumes: `learn_from_failure` (write), `load_project_file_rules` + `RepoBranchAgent` (read) — all from earlier tasks.

- [ ] **Step 1: Write the test**

Create `tests/integration/test_per_project_learning.py`:

```python
"""Closed-loop proof: a project-tier lesson learned on run 1 is injected on run 2."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from ai_dev_system.rules.learning import learn_from_failure
from ai_dev_system.rules.project_rules import project_rules_dir, load_project_file_rules
from ai_dev_system.agents.repo_branch_agent import RepoBranchAgent


def _ctx():
    return {"objective": "Add login", "description": "", "done_definition": "",
            "type": "coding", "facets": {}}


def test_lesson_learned_then_applied(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    rules_dir = project_rules_dir(repo)

    # ── RUN 1: a reject mints a project-tier lesson ──
    result = learn_from_failure(
        None, "run1",
        {"task_run_id": "t1", "task_type": "coding", "tags": []},
        rules_dir=rules_dir, source="gate",
        rejection_reason="endpoint skips input validation",
    )
    assert result is not None
    assert (rules_dir / "learned-coding.yaml").exists()

    # The loader sees it for a matching task.
    assert any("input validation" in r
               for r in load_project_file_rules(str(repo), {"type": "coding"}))

    # ── RUN 2: the implementer's CLI prompt carries the lesson ──
    monkeypatch.setenv("EXEC_REVIEW_GATE", "0")
    agent = RepoBranchAgent(str(repo), "ai-dev/task-xyz", "main")
    captured = {}

    def _fake_popen(cmd, **kw):
        captured["cmd"] = cmd

        class FakePopen:
            returncode = 0
            stdout = iter([json.dumps({"type": "result", "result": "Done."}) + "\n"])
            stderr = iter([])

            def wait(self, timeout=None):
                self.returncode = 0

        return FakePopen()

    def _fake_run(cmd, **kw):
        import subprocess
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="(no diff)", stderr="")

    with patch("ai_dev_system.agents.repo_branch_agent.ClaudeCodeLLMClient._resolve_claude_cmd",
               return_value="claude"), \
         patch("ai_dev_system.agents.repo_branch_agent.subprocess.Popen", side_effect=_fake_popen), \
         patch("ai_dev_system.agents.repo_branch_agent.subprocess.run", side_effect=_fake_run):
        agent.run("TASK-XYZ", str(tmp_path / "out"), context=_ctx(), file_rules=[])

    assert "input validation" in captured["cmd"][2]
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `python -m pytest tests/integration/test_per_project_learning.py -v`
Expected: PASS (1 passed)

- [ ] **Step 3: Run the full suite to confirm no regression**

Run: `python -m pytest -q`
Expected: PASS (all previously-passing tests still pass; new tests included)

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_per_project_learning.py
git commit -m "test(learning): closed-loop integration — learn on run 1, apply on run 2"
```

---

## Open decisions (carry from spec — resolve before/with execution)

1. **Committing lessons to git.** This plan WRITES `<repo>/.ai-dev/rules/*.yaml` to disk (enough for the loop to work — `load_project_file_rules` reads the dir regardless of git state). Auto-committing them as `chore(ai-dev): learn <scope>` for PR review is **deferred** — add as a follow-up once you confirm the commit policy.
2. **Auto-promotion project→global** when a lesson recurs across projects: **deferred**.
3. **Default destination for auto-lessons:** this plan routes reject-lessons to the **project tier**; the global tier keeps only hand-authored + existing learned files (back-compat).

## Out of scope — sequenced follow-ups

- **Per-run reload of the GLOBAL registry** (spec defect #3, intra-run staleness): `worker.py:24` builds `_rule_registry` once at import. Moving it into the run path is a separate, worker-touching change; not needed for the per-project value.
- **`review.json` mining** (widen lesson source beyond FAIL/reject): a post-task librarian step.
- **Task-failure classification** (`classify_task_failure → should_learn`) so "built wrong as a thrown exception" mints a lesson — currently `EXECUTION_ERROR` mints nothing.
- **Pre-existing bug noted while reading** (not addressed here): `test_author_agent.py:195` references `model`/`effort` that are out of scope inside `_review_and_repair` → `NameError` when a test-review fix round runs. Worth a separate small fix.

## Self-review notes

- **Spec coverage:** two-tier storage (Tasks 2-4), in-repo location (Tasks 2,4), file_rules injection / closed loop (Tasks 1,3,6), DO-NOT-SAVE guardrail (Task 5), back-compat off-switch (`AI_DEV_PROJECT_RULES`, Task 2). PATCH-before-CREATE fuzzy dedup and review.json mining are explicitly deferred above.
- **Type consistency:** `_format_lessons(file_rules)`, `_merge_rules(global, project)`, `load_project_file_rules(repo_path, context)`, `project_rules_dir(repo_path)`, `_project_rules_dir_for_spec(spec_id)`, `_is_transient_lesson(text)` — names used identically across tasks.
- **No placeholders:** every code step shows full code; every test step shows the command + expected result.
- **Regression caught in review:** an early marker list contained bare `"rate limit"`, which matched the legitimate lesson `"output ignores rate limiting"` in `test_learning.py:154` (would have broken that test). Markers were tightened to `"rate limit exceeded"` / `"429 "`. Re-confirm by running `python -m pytest tests/unit/rules/test_learning.py tests/integration/test_learning_loop.py -v` after Task 5.
