# Task spec/plan files in repo (two-gate flow) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In the single-task repo-bound bot flow, publish a task-named spec file and a task-named plan file into the bound repo on the feature branch (committed + pushed, GitHub link to the user), and split approval into two gates — approve spec → generate plan → approve plan → execute.

**Architecture:** A new lightweight `git_ops.py` holds git primitives extracted from `single_task_executor` plus three new helpers (`ensure_branch_from_base`, `commit_paths`, `blob_url`). A new `repo_docs.py` renders markdown and publishes a doc to the branch. The `single_task_worker` gains `--mode {spec,plan}`: spec mode publishes the spec file, plan mode (run only after spec approval) builds + publishes the plan file. The gateway tools (`dev_pipeline.py`) route the two gates **off disk state**: spec ready + no plan → spec gate; plan file present → plan gate. All git IO runs inside the worker subprocess, never the gateway thread.

**Tech Stack:** Python 3, stdlib `subprocess`/`pathlib`/`unicodedata`, pytest. Git CLI. No new dependencies.

## Global Constraints

- Single-task repo-bound flow only — do not touch the full-project debate path.
- Files must be **task-named** (no generic `spec.md`/`plan.md`); unique per task so they never collide after merge.
- Both files go on the **same** feature branch `ai-dev/task-{spec_id[:8]}` used by execution.
- Updates = **new commit** (never amend / force-push).
- `publish_doc` and all git helpers must **never raise** into the caller — a push/auth failure returns `None`/`False` and the flow continues without a link.
- Force UTF-8 when writing files (`encoding="utf-8"`); Vietnamese titles must not crash.
- Gateway/daemon thread must do **no git and no LLM** — publishing happens in the worker subprocess.

---

### Task 1: `git_ops.py` — shared git primitives + new helpers

**Files:**
- Create: `src/ai_dev_system/task_graph/git_ops.py`
- Test: `tests/unit/task_graph/test_git_ops.py`

**Interfaces:**
- Produces (used by Tasks 2 & 3):
  - `run_git(args: list[str], cwd: str) -> subprocess.CompletedProcess`
  - `current_branch(repo_path: str) -> str`
  - `base_branch(repo_path: str) -> str`
  - `checkout_branch(repo_path: str, branch_name: str) -> None` (checkout, else `checkout -b` from current)
  - `normalize_github_url(remote: str) -> str`
  - `push_branch_compare(repo_path: str, branch: str, base: str) -> dict` (`{"pushed","compare_url","push_error"}`)
  - `ensure_branch_from_base(repo_path: str, branch: str) -> None` (checkout existing branch; else fork from `base_branch`)
  - `commit_paths(repo_path: str, paths: list[str], message: str) -> bool` (True if a commit was created; False on "nothing to commit")
  - `blob_url(remote_url: str, branch: str, relpath: str) -> str | None`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/task_graph/test_git_ops.py
import subprocess
from pathlib import Path

import pytest

from ai_dev_system.task_graph import git_ops


def test_normalize_github_url_variants():
    assert git_ops.normalize_github_url("https://github.com/o/r.git") == "https://github.com/o/r"
    assert git_ops.normalize_github_url("git@github.com:o/r.git") == "https://github.com/o/r"
    assert git_ops.normalize_github_url("ssh://git@github.com/o/r.git") == "https://github.com/o/r"
    assert git_ops.normalize_github_url("https://github.com/o/r/") == "https://github.com/o/r"


def test_blob_url_github_and_non_github():
    assert (
        git_ops.blob_url("git@github.com:o/r.git", "ai-dev/task-ab12", ".ai-dev/tasks/x-spec.md")
        == "https://github.com/o/r/blob/ai-dev/task-ab12/.ai-dev/tasks/x-spec.md"
    )
    # backslashes normalized to forward slashes
    assert git_ops.blob_url(
        "https://github.com/o/r", "b", ".ai-dev\\tasks\\x.md"
    ) == "https://github.com/o/r/blob/b/.ai-dev/tasks/x.md"
    # non-GitHub remote → None
    assert git_ops.blob_url("https://gitlab.com/o/r.git", "b", "x.md") is None


def _init_repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    p = str(path)
    git_ops.run_git(["init"], p)
    git_ops.run_git(["config", "user.email", "t@t.t"], p)
    git_ops.run_git(["config", "user.name", "t"], p)
    git_ops.run_git(["checkout", "-b", "master"], p)
    (path / "README.md").write_text("hi", encoding="utf-8")
    git_ops.run_git(["add", "-A"], p)
    git_ops.run_git(["commit", "-m", "init"], p)
    return p


def test_ensure_branch_from_base_creates_then_reuses(tmp_path):
    p = _init_repo(tmp_path / "repo")
    git_ops.ensure_branch_from_base(p, "ai-dev/task-xyz")
    assert git_ops.current_branch(p) == "ai-dev/task-xyz"
    # idempotent: switch away then ensure again → checks out existing branch
    git_ops.run_git(["checkout", "master"], p)
    git_ops.ensure_branch_from_base(p, "ai-dev/task-xyz")
    assert git_ops.current_branch(p) == "ai-dev/task-xyz"


def test_commit_paths_returns_false_on_nothing_to_commit(tmp_path):
    p = _init_repo(tmp_path / "repo")
    (Path(p) / "a.txt").write_text("x", encoding="utf-8")
    assert git_ops.commit_paths(p, ["a.txt"], "add a") is True
    assert git_ops.commit_paths(p, ["a.txt"], "noop") is False  # no changes
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/task_graph/test_git_ops.py -q`
Expected: FAIL (ModuleNotFoundError: `git_ops`).

- [ ] **Step 3: Implement `git_ops.py`**

```python
# src/ai_dev_system/task_graph/git_ops.py
"""Shared git CLI helpers for single-task flows (executor + repo docs).

Every helper is best-effort and small; the heavier helpers (publish, push)
never raise into the caller so a missing remote / auth failure degrades to
"no link" rather than sinking the run.
"""
from __future__ import annotations

import subprocess


def run_git(args: list[str], cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def current_branch(repo_path: str) -> str:
    proc = run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo_path)
    if proc.returncode != 0:
        raise RuntimeError(f"git rev-parse failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def base_branch(repo_path: str) -> str:
    """Repo's default integration branch (master/main). Never an ai-dev/ branch."""
    current = current_branch(repo_path)
    if not current.startswith("ai-dev/"):
        return current
    for candidate in ("master", "main"):
        if run_git(["rev-parse", "--verify", candidate], repo_path).returncode == 0:
            return candidate
    proc = run_git(["symbolic-ref", "refs/remotes/origin/HEAD", "--short"], repo_path)
    if proc.returncode == 0:
        return proc.stdout.strip().removeprefix("origin/")
    return "master"


def checkout_branch(repo_path: str, branch_name: str) -> None:
    """Checkout branch, creating it from the CURRENT ref if missing."""
    if run_git(["checkout", branch_name], repo_path).returncode == 0:
        return
    proc = run_git(["checkout", "-b", branch_name], repo_path)
    if proc.returncode != 0:
        raise RuntimeError(f"git checkout -b {branch_name!r} failed: {proc.stderr.strip()}")


def ensure_branch_from_base(repo_path: str, branch: str) -> None:
    """Make `branch` the current branch. If it exists, check it out; otherwise
    fork it from the repo's real base (master/main), NOT from a leftover
    ai-dev/ branch."""
    if run_git(["rev-parse", "--verify", branch], repo_path).returncode == 0:
        co = run_git(["checkout", branch], repo_path)
        if co.returncode != 0:
            raise RuntimeError(f"git checkout {branch!r} failed: {co.stderr.strip()}")
        return
    base = base_branch(repo_path)
    run_git(["checkout", base], repo_path)  # best-effort; may already be on base
    created = run_git(["checkout", "-b", branch], repo_path)
    if created.returncode != 0:
        co = run_git(["checkout", branch], repo_path)  # race: created meanwhile
        if co.returncode != 0:
            raise RuntimeError(f"git checkout -b {branch!r} failed: {created.stderr.strip()}")


def commit_paths(repo_path: str, paths: list[str], message: str) -> bool:
    """Stage `paths` and commit. Returns True if a commit was created, False if
    there was nothing to commit (identical content)."""
    run_git(["add", *paths], repo_path)
    proc = run_git(["commit", "-m", message], repo_path)
    return proc.returncode == 0


def normalize_github_url(remote: str) -> str:
    remote = (remote or "").strip()
    if remote.endswith(".git"):
        remote = remote[:-4]
    if remote.startswith("git@github.com:"):
        remote = "https://github.com/" + remote[len("git@github.com:"):]
    elif remote.startswith("ssh://git@github.com/"):
        remote = "https://github.com/" + remote[len("ssh://git@github.com/"):]
    return remote.rstrip("/")


def push_branch_compare(repo_path: str, branch: str, base: str) -> dict:
    """Push `branch` to origin and build a GitHub compare URL. Never raises."""
    info: dict = {"pushed": False, "compare_url": None, "push_error": None}
    push = run_git(["push", "-u", "origin", branch], repo_path)
    if push.returncode != 0:
        info["push_error"] = (push.stderr or push.stdout or "").strip()[:300]
        return info
    info["pushed"] = True
    remote = run_git(["remote", "get-url", "origin"], repo_path)
    if remote.returncode == 0 and remote.stdout.strip():
        base_url = normalize_github_url(remote.stdout.strip())
        if "github.com/" in base_url:
            info["compare_url"] = f"{base_url}/compare/{base}...{branch}"
    return info


def blob_url(remote_url: str, branch: str, relpath: str) -> str | None:
    """GitHub blob URL for a file on a branch, or None for non-GitHub remotes."""
    base = normalize_github_url(remote_url)
    if "github.com/" not in base:
        return None
    rel = relpath.replace("\\", "/").lstrip("/")
    return f"{base}/blob/{branch}/{rel}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/task_graph/test_git_ops.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/task_graph/git_ops.py tests/unit/task_graph/test_git_ops.py
git commit -m "feat(git_ops): shared git helpers + ensure_branch_from_base/commit_paths/blob_url"
```

---

### Task 2: Point `single_task_executor` at `git_ops` (no behavior change)

**Files:**
- Modify: `src/ai_dev_system/task_graph/single_task_executor.py:30-108` (replace helper definitions with imports + aliases)
- Modify: `tests/unit/test_single_task_executor.py` (patch targets/imports → `git_ops`)

**Interfaces:**
- Consumes: all `git_ops` functions from Task 1.
- Produces: the executor keeps its private alias names (`_git`, `_git_current_branch`, `_git_base_branch`, `_git_checkout_branch`, `_normalize_github_url`, `_push_branch_compare`) so the rest of the file is untouched.

- [ ] **Step 1: Replace the helper block with imports**

Delete the function bodies for `_git`, `_git_current_branch`, `_git_base_branch`, `_git_checkout_branch`, `_normalize_github_url`, `_push_branch_compare` (lines 34–108) and replace the `# Git helpers` section with:

```python
# ---------------------------------------------------------------------------
# Git helpers (shared — see ai_dev_system.task_graph.git_ops)
# ---------------------------------------------------------------------------
from ai_dev_system.task_graph.git_ops import (  # noqa: E402
    run_git as _git,
    current_branch as _git_current_branch,
    base_branch as _git_base_branch,
    checkout_branch as _git_checkout_branch,
    normalize_github_url as _normalize_github_url,
    push_branch_compare as _push_branch_compare,
)
```

Leave the rest of `single_task_executor.py` exactly as-is (it calls the aliases). The module-level `import subprocess` is still used by other code paths; keep it.

- [ ] **Step 2: Update the executor tests' patch targets**

In `tests/unit/test_single_task_executor.py`, the unit tests for the moved helpers must target `git_ops`. Change:
- imports `from ai_dev_system.task_graph.single_task_executor import _git_checkout_branch` → `from ai_dev_system.task_graph.git_ops import checkout_branch as _git_checkout_branch` (and similarly `_normalize_github_url` → `normalize_github_url`, `_push_branch_compare` → `push_branch_compare`).
- `patch("ai_dev_system.task_graph.single_task_executor.subprocess.run", ...)` (lines ~33, 41, 55, 74, 100, 111) → `patch("ai_dev_system.task_graph.git_ops.subprocess.run", ...)`.

Leave the full `run_executor` test (lines ~219-224) patching `single_task_executor._git_current_branch` / `._git_checkout_branch` as-is **only if** `run_executor` calls those aliases directly. It calls `_git_base_branch` and `_git_checkout_branch`. `_git_base_branch` is now `git_ops.base_branch`, which internally calls `git_ops.current_branch` — so a patch on `single_task_executor._git_current_branch` no longer intercepts base detection. Fix that test by patching the base branch instead:
- replace `patch("ai_dev_system.task_graph.single_task_executor._git_current_branch", return_value="main")` with `patch("ai_dev_system.task_graph.single_task_executor._git_base_branch", return_value="main")`.
- keep `patch("ai_dev_system.task_graph.single_task_executor._git_checkout_branch")` (the executor calls this alias directly, so the patch still applies).

- [ ] **Step 3: Run the executor tests**

Run: `python -m pytest tests/unit/test_single_task_executor.py -q`
Expected: PASS (all existing tests green).

- [ ] **Step 4: Commit**

```bash
git add src/ai_dev_system/task_graph/single_task_executor.py tests/unit/test_single_task_executor.py
git commit -m "refactor(executor): use shared git_ops helpers (no behavior change)"
```

---

### Task 3: `repo_docs.py` — slugs, relpaths, renderers, publish

**Files:**
- Create: `src/ai_dev_system/task_graph/repo_docs.py`
- Test: `tests/unit/task_graph/test_repo_docs.py`

**Interfaces:**
- Consumes: `git_ops.{ensure_branch_from_base, commit_paths, run_git, blob_url}`; `single_task_plan.branch_name_for`.
- Produces (used by Task 4):
  - `slugify(title: str, maxlen: int = 40) -> str`
  - `spec_doc_relpath(spec_id: str, title: str) -> str`
  - `plan_doc_relpath(spec_id: str, title: str) -> str`
  - `render_spec_md(spec: dict, spec_id: str) -> str`
  - `render_plan_md(spec: dict, plan: dict) -> str`
  - `publish_doc(repo_path: str, branch: str, relpath: str, content: str, commit_msg: str) -> str | None`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/task_graph/test_repo_docs.py
from pathlib import Path

from ai_dev_system.task_graph import repo_docs, git_ops


def test_slugify_handles_vietnamese_and_empty():
    assert repo_docs.slugify("Bổ sung trường OwnerId") == "bo-sung-truong-ownerid"
    assert repo_docs.slugify("") == "task"
    assert repo_docs.slugify("!!!") == "task"


def test_relpaths_unique_and_task_named():
    sp = repo_docs.spec_doc_relpath("abcd1234ef", "Add logout")
    pl = repo_docs.plan_doc_relpath("abcd1234ef", "Add logout")
    assert sp == ".ai-dev/tasks/task-abcd1234-add-logout-spec.md"
    assert pl == ".ai-dev/tasks/task-abcd1234-add-logout-plan.md"
    # different spec_id → different path
    assert repo_docs.spec_doc_relpath("zzzz9999aa", "Add logout") != sp


def test_render_spec_md_sections():
    spec = {
        "idea": "Add OwnerId to List endpoint",
        "task": {"title": "Add OwnerId", "objective": "expose owner id"},
        "facets": {"scope": {"status": "filled", "value": "endpoint List"}},
        "findings": ["needs index on OwnerId"],
    }
    md = repo_docs.render_spec_md(spec, "abcd1234ef")
    assert "# Add OwnerId" in md
    assert "Mục tiêu" in md
    assert "expose owner id" in md
    assert "scope" in md
    assert "needs index on OwnerId" in md


def test_render_plan_md_steps_and_gate():
    spec = {"task": {"title": "Add OwnerId"}}
    plan = {
        "spec_id": "abcd1234ef", "branch": "ai-dev/task-abcd1234", "tdd_gate": True,
        "graph": {"tasks": [
            {"id": "T-TEST", "objective": "write tests", "agent_type": "TestAuthorAgent",
             "phase": "test", "done_definition": "failing tests committed", "deps": []},
            {"id": "T-IMPL", "objective": "implement", "agent_type": "RepoBranchAgent",
             "phase": "implementation", "done_definition": "code committed", "deps": ["T-TEST"]},
        ]},
    }
    md = repo_docs.render_plan_md(spec, plan)
    assert "# Plan — Add OwnerId" in md
    assert "2 bước" in md
    assert "TestAuthorAgent" in md and "RepoBranchAgent" in md
    assert "T-TEST" in md  # dep shown


def _init_repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    p = str(path)
    for a in (["init"], ["config", "user.email", "t@t.t"], ["config", "user.name", "t"],
              ["checkout", "-b", "master"]):
        git_ops.run_git(a, p)
    (path / "README.md").write_text("hi", encoding="utf-8")
    git_ops.run_git(["add", "-A"], p)
    git_ops.run_git(["commit", "-m", "init"], p)
    return p


def test_publish_doc_commits_on_branch_and_updates(tmp_path):
    # bare remote so push -u origin succeeds offline
    bare = tmp_path / "remote.git"
    git_ops.run_git(["init", "--bare", str(bare)], str(tmp_path))
    repo = _init_repo(tmp_path / "work")
    git_ops.run_git(["remote", "add", "origin", str(bare)], repo)

    rel = ".ai-dev/tasks/task-abcd1234-x-spec.md"
    url = repo_docs.publish_doc(repo, "ai-dev/task-abcd1234", rel, "v1", "docs: spec")
    # non-GitHub (file) remote → no blob URL, but file is committed on the branch
    assert url is None
    assert git_ops.current_branch(repo) == "ai-dev/task-abcd1234"
    assert (Path(repo) / rel).read_text(encoding="utf-8") == "v1"
    log1 = git_ops.run_git(["log", "--oneline", "ai-dev/task-abcd1234"], repo).stdout
    assert "docs: spec" in log1

    # second publish rewrites + adds a new commit (no force-push)
    repo_docs.publish_doc(repo, "ai-dev/task-abcd1234", rel, "v2", "docs: update spec")
    assert (Path(repo) / rel).read_text(encoding="utf-8") == "v2"
    log2 = git_ops.run_git(["log", "--oneline", "ai-dev/task-abcd1234"], repo).stdout
    assert "docs: update spec" in log2 and "docs: spec" in log2


def test_publish_doc_no_repo_returns_none(tmp_path):
    assert repo_docs.publish_doc("", "b", "x.md", "c", "m") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/task_graph/test_repo_docs.py -q`
Expected: FAIL (ModuleNotFoundError: `repo_docs`).

- [ ] **Step 3: Implement `repo_docs.py`**

```python
# src/ai_dev_system/task_graph/repo_docs.py
"""Render a single-task spec/plan as markdown and publish it to the bound repo's
feature branch (commit + push), returning a GitHub blob URL the bot can send.

All git IO is best-effort: publish_doc never raises — a push/auth failure (or no
repo) returns None and the calling worker simply records no link.
"""
from __future__ import annotations

import logging
import re
import time
import unicodedata
from pathlib import Path

from ai_dev_system.task_graph import git_ops

logger = logging.getLogger(__name__)


def slugify(title: str, maxlen: int = 40) -> str:
    if not title:
        return "task"
    t = str(title).replace("đ", "d").replace("Đ", "D")
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode("ascii")
    t = re.sub(r"[^a-zA-Z0-9]+", "-", t).strip("-").lower()
    t = t[:maxlen].strip("-")
    return t or "task"


def spec_doc_relpath(spec_id: str, title: str) -> str:
    return f".ai-dev/tasks/task-{spec_id[:8]}-{slugify(title)}-spec.md"


def plan_doc_relpath(spec_id: str, title: str) -> str:
    return f".ai-dev/tasks/task-{spec_id[:8]}-{slugify(title)}-plan.md"


def _title_of(spec: dict) -> str:
    task = spec.get("task") or {}
    return str(task.get("title") or spec.get("idea") or "Task").strip() or "Task"


def render_spec_md(spec: dict, spec_id: str) -> str:
    from ai_dev_system.task_graph.single_task_plan import branch_name_for
    task = spec.get("task") or {}
    title = _title_of(spec)
    branch = branch_name_for(spec_id)
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    lines = [
        f"# {title}",
        "",
        f"> Task `task-{spec_id[:8]}` · branch `{branch}` · cập nhật {ts}",
        "",
        f"**Mục tiêu:** {task.get('objective') or spec.get('idea') or ''}",
        "",
    ]
    facets = spec.get("facets") or {}
    if facets:
        lines.append("**Facets:**")
        for key, f in facets.items():
            status = (f or {}).get("status", "")
            val = (f or {}).get("value")
            val = f" — {val}" if isinstance(val, str) and val else ""
            lines.append(f"- **{key}** ({status}){val}")
        lines.append("")
    findings = spec.get("findings") or []
    if findings:
        lines.append("**Findings:**")
        lines += [f"- {x}" for x in findings]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_plan_md(spec: dict, plan: dict) -> str:
    title = _title_of(spec)
    branch = plan.get("branch") or ""
    gate = "on" if plan.get("tdd_gate") else "off"
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    tasks = ((plan.get("graph") or {}).get("tasks")) or []
    n = len(tasks) if isinstance(tasks, list) else 0
    lines = [
        f"# Plan — {title}",
        "",
        f"> branch `{branch}` · TDD gate: {gate} · cập nhật {ts}",
        "",
        f"## {n} bước",
        "",
    ]
    for i, t in enumerate(tasks, 1):
        deps = ", ".join(t.get("deps") or []) or "—"
        lines.append(
            f"{i}. **{t.get('objective') or t.get('id')}** — "
            f"agent `{t.get('agent_type')}`, phase `{t.get('phase')}`"
        )
        lines.append(f"   - Done: {t.get('done_definition') or ''}")
        lines.append(f"   - Deps: {deps}")
    return "\n".join(lines).rstrip() + "\n"


def publish_doc(repo_path: str, branch: str, relpath: str, content: str,
                commit_msg: str) -> str | None:
    """Ensure branch → write file → commit → push. Returns GitHub blob URL or None."""
    if not repo_path:
        return None
    try:
        git_ops.ensure_branch_from_base(repo_path, branch)
        abs_path = Path(repo_path) / relpath
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(content, encoding="utf-8")
        git_ops.commit_paths(repo_path, [relpath], commit_msg)
        push = git_ops.run_git(["push", "-u", "origin", branch], repo_path)
        if push.returncode != 0:
            logger.warning("publish_doc push failed: %s", (push.stderr or "").strip()[:200])
            return None
        remote = git_ops.run_git(["remote", "get-url", "origin"], repo_path)
        if remote.returncode != 0 or not remote.stdout.strip():
            return None
        return git_ops.blob_url(remote.stdout.strip(), branch, relpath)
    except Exception:  # noqa: BLE001 — publishing must never sink the flow
        logger.exception("publish_doc failed for %s", relpath)
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/task_graph/test_repo_docs.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/task_graph/repo_docs.py tests/unit/task_graph/test_repo_docs.py
git commit -m "feat(repo_docs): render + publish task spec/plan markdown to the branch"
```

---

### Task 4: Worker `--mode {spec,plan}` — publish spec, then plan

**Files:**
- Modify: `src/ai_dev_system/task_graph/single_task_worker.py` (publish spec in `run_worker`; add `run_plan_worker`; add `--mode` to `main`; make `--idea` optional)
- Test: `tests/unit/task_graph/test_single_task_worker_publish.py`

**Interfaces:**
- Consumes: `repo_docs.{spec_doc_relpath, plan_doc_relpath, render_spec_md, render_plan_md, publish_doc}`; `single_task_plan.{plan_single_task, plan_path, branch_name_for}`.
- Produces:
  - `run_plan_worker(spec_id: str, *, storage_root: str, database_url: str | None = None) -> dict` (returns the plan dict; writes `doc_url` into `<spec_id>-plan.json` when publish returns a URL)
  - `run_worker` writes `spec_doc_url` into `<spec_id>.json` when the spec is published.
  - `main` dispatches on `--mode` (`spec` default; `plan`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/task_graph/test_single_task_worker_publish.py
import json
from pathlib import Path
from unittest.mock import patch

from ai_dev_system.task_graph import single_task_worker as w
from ai_dev_system.task_graph.single_task_plan import plan_path


def _seed_spec(root: Path, spec_id: str, repo: str):
    d = root / "task_specs"; d.mkdir(parents=True, exist_ok=True)
    (d / f"{spec_id}.json").write_text(json.dumps({
        "status": "done", "idea": "add owner id", "repo": repo,
        "task": {"title": "Add OwnerId", "objective": "expose owner id"}, "facets": {},
        "clarify": {"needed": False, "questions": []},
    }), encoding="utf-8")


def test_run_plan_worker_builds_plan_and_records_doc_url(tmp_path):
    root = tmp_path / "storage"
    _seed_spec(root, "spec1234ab", "/repos/app")
    with patch("ai_dev_system.task_graph.single_task_worker.publish_doc",
               return_value="https://github.com/o/r/blob/b/plan.md") as pub:
        plan = w.run_plan_worker("spec1234ab", storage_root=str(root))
    assert pub.called
    assert plan["doc_url"] == "https://github.com/o/r/blob/b/plan.md"
    saved = json.loads(plan_path(str(root), "spec1234ab").read_text(encoding="utf-8"))
    assert saved["doc_url"] == "https://github.com/o/r/blob/b/plan.md"


def test_run_plan_worker_no_url_leaves_plan_clean(tmp_path):
    root = tmp_path / "storage"
    _seed_spec(root, "spec1234ab", "/repos/app")
    with patch("ai_dev_system.task_graph.single_task_worker.publish_doc", return_value=None):
        plan = w.run_plan_worker("spec1234ab", storage_root=str(root))
    assert "doc_url" not in plan


def test_main_mode_plan_dispatches(tmp_path):
    root = tmp_path / "storage"
    _seed_spec(root, "spec1234ab", "/repos/app")
    with patch("ai_dev_system.task_graph.single_task_worker.run_plan_worker") as rp:
        w.main(["--id", "spec1234ab", "--mode", "plan", "--storage-root", str(root)])
    rp.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/task_graph/test_single_task_worker_publish.py -q`
Expected: FAIL (`run_plan_worker` not defined; `publish_doc` not importable from worker).

- [ ] **Step 3: Add the imports + spec publish to `run_worker`**

At the top of `single_task_worker.py`, add after the existing imports:

```python
from ai_dev_system.task_graph.repo_docs import (
    spec_doc_relpath, plan_doc_relpath, render_spec_md, render_plan_md, publish_doc,
)
from ai_dev_system.task_graph.single_task_plan import (
    plan_single_task, plan_path, branch_name_for,
)
```

In `run_worker`, immediately **before** `path.write_text(...)` (current line 137), insert:

```python
        # Publish the spec doc to the repo branch (off the gateway thread) when the
        # spec is final and not blocked on clarify. Best-effort: no link on failure.
        if (payload.get("status") == "done" and repo
                and not (payload.get("clarify") or {}).get("needed")):
            title = (payload.get("task") or {}).get("title") or idea
            url = publish_doc(
                repo, branch_name_for(spec_id),
                spec_doc_relpath(spec_id, title),
                render_spec_md(payload, spec_id),
                f"docs(ai-dev): spec for {spec_id[:8]}",
            )
            if url:
                payload["spec_doc_url"] = url
            _spec_log(log_path, f"Spec doc: {url or '(local only / no push)'}")
```

- [ ] **Step 4: Add `run_plan_worker`**

After `run_worker` (before `main`), add:

```python
def run_plan_worker(spec_id: str, *, storage_root: str,
                    database_url: str | None = None) -> dict:
    """Plan gate: build the reviewable plan for an already-approved spec and
    publish <id>-plan.md to the repo branch. Records doc_url in the plan file."""
    out_dir = Path(storage_root) / "task_specs"
    log_path = out_dir / f"{spec_id}.log"
    spec = json.loads((out_dir / f"{spec_id}.json").read_text(encoding="utf-8"))
    plan = plan_single_task(spec, spec_id, storage_root=storage_root)
    repo = spec.get("repo")
    if repo:
        title = (spec.get("task") or {}).get("title") or spec.get("idea")
        url = publish_doc(
            repo, plan["branch"], plan_doc_relpath(spec_id, title),
            render_plan_md(spec, plan),
            f"docs(ai-dev): plan for {spec_id[:8]}",
        )
        if url:
            plan["doc_url"] = url
            plan_path(storage_root, spec_id).write_text(
                json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
        _spec_log(log_path, f"Plan doc: {url or '(local only / no push)'}")
    return plan
```

- [ ] **Step 5: Add `--mode` to `main` and relax `--idea`**

Replace `main` with:

```python
def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--id", required=True)
    p.add_argument("--idea", default="")
    p.add_argument("--repo", default=None)
    p.add_argument("--mode", choices=["spec", "plan"], default="spec")
    p.add_argument("--storage-root", required=True)
    p.add_argument("--database-url", default=None)
    args = p.parse_args(argv)
    if args.mode == "plan":
        run_plan_worker(args.id, storage_root=args.storage_root,
                        database_url=args.database_url)
    else:
        run_worker(args.id, args.idea, args.repo or None,
                   storage_root=args.storage_root, database_url=args.database_url)
    return 0
```

- [ ] **Step 6: Run the new tests + the existing worker tests**

Run: `python -m pytest tests/unit/task_graph/test_single_task_worker_publish.py tests/unit/task_graph/test_single_task_worker.py tests/unit/task_graph/test_single_task_worker_clarify.py -q`
Expected: PASS. (Existing worker tests use `repo=None` or stubbed specs; spec publish is skipped when `repo` is falsy, and adds `spec_doc_url` only on a real URL, so their payload assertions are unaffected. If any existing test passes a truthy `repo`, confirm it still passes — `publish_doc` against a non-repo path returns `None` and adds no key.)

- [ ] **Step 7: Commit**

```bash
git add src/ai_dev_system/task_graph/single_task_worker.py tests/unit/task_graph/test_single_task_worker_publish.py
git commit -m "feat(worker): --mode spec|plan; publish spec then plan docs to the branch"
```

---

### Task 5: Two-gate routing in the gateway tools

**Files:**
- Modify: `src/ai_dev_system/harness/tools/dev_pipeline.py`
  - `dev_run_status` single-task block (lines ~244-259): spec gate vs plan gate; no auto-plan
  - `dev_answer_gate` single-task block (lines ~349-377): file-based gate routing
  - `dev_task_start` reply text (lines ~627-628): spec-first wording
- Modify: `tests/unit/harness/test_dev_task_tools.py` (update `test_status_shows_plan_when_spec_ready`; add two tests)

**Interfaces:**
- Consumes: `single_task_plan.{load_plan, approve_plan}`; worker `--mode plan` via `_spawn_worker`.
- Produces: the two-gate behavior — spec ready + no `<id>-plan.json` → spec gate; `<id>-plan.json` present → plan gate.

- [ ] **Step 1: Update `dev_run_status` — remove auto-plan, add spec/plan gates**

Replace the block at lines ~244-259 (`# 2. Spec ready? ...` through the `📋 Plan sẵn sàng` return) with:

```python
            # 2. Spec ready? Clarify gate → spec gate → plan gate.
            if spec_path.exists():
                spec = json.loads(spec_path.read_text(encoding="utf-8"))
                clarify = spec.get("clarify") or {}
                if clarify.get("needed") and pending.get("round", 0) < 2:
                    chat_task_store.update(surface, chat_id, phase="awaiting_clarify",
                                           clarify_questions=clarify.get("questions") or [])
                    return {"content": [{"type": "text", "text":
                        format_questions(clarify.get("questions") or [])}]}
                plan = load_plan(sr, spec_id)
                if plan is None:
                    # SPEC gate — plan not generated until the spec is approved.
                    url = spec.get("spec_doc_url")
                    link = f"\n📄 Spec: {url}" if url else ""
                    chat_task_store.update(surface, chat_id, phase="awaiting_spec_approval")
                    return {"content": [{"type": "text", "text":
                        f"📄 Spec sẵn sàng.{link}\nNhắn 'duyệt' để tạo plan."}]}
                # PLAN gate — plan generated + published; awaiting run approval.
                steps = (plan.get("graph") or {}).get("tasks") or []
                n = len(steps) if isinstance(steps, list) else 0
                url = plan.get("doc_url")
                link = f"\n📋 Plan: {url}" if url else ""
                chat_task_store.update(surface, chat_id, phase="awaiting_plan_approval")
                return {"content": [{"type": "text", "text":
                    f"📋 Plan sẵn sàng ({n} bước).{link}\nNhắn 'duyệt' để chạy và tạo PR."}]}

            return {"content": [{"type": "text", "text": "⏳ Đang tạo spec..."}]}
```

Also update the import on line ~213-215 to drop `plan_single_task` (now unused here):

```python
            from ai_dev_system.task_graph.single_task_plan import load_plan
```

- [ ] **Step 2: Update `dev_answer_gate` — file-based two-gate routing**

Replace the pending-task block (lines ~350-377, the `if pending and not run_id:` body) with:

```python
        if pending and not (args.get("run_id") or "").strip():
            text = args.get("text", "")
            approve = bool(_G2_APPROVE_RE.search(text)) and not bool(_G2_REJECT_RE.search(text))
            reject = bool(_G2_REJECT_RE.search(text)) and not bool(_G2_APPROVE_RE.search(text))
            sr = str(config.storage_root)
            spec_id = pending["spec_id"]
            if approve:
                from ai_dev_system.task_graph.single_task_plan import (
                    approve_plan, load_plan,
                )
                plan = load_plan(sr, spec_id)
                if plan is not None:
                    # PLAN gate → approve + execute.
                    approve_plan(sr, spec_id)
                    log_dir = Path(sr) / "ui_logs"; log_dir.mkdir(parents=True, exist_ok=True)
                    argv = [
                        sys.executable, "-m", "ai_dev_system.task_graph.single_task_executor",
                        "--id", spec_id, "--storage-root", sr,
                        "--database-url", str(config.database_url),
                    ]
                    try:
                        with open(log_dir / f"exec_{spec_id[:8]}.log", "a",
                                  encoding="utf-8", errors="replace") as logf:
                            _spawn_exec(argv, stdout=logf, stderr=subprocess.STDOUT, cwd=str(_REPO_ROOT))
                    except Exception as exc:  # pragma: no cover
                        return {"content": [{"type": "text", "text": f"exec spawn error: {exc}"}]}
                    return {"content": [{"type": "text", "text":
                        "▶️ Đang chạy execution. Hỏi trạng thái để nhận link PR khi xong."}]}
                # SPEC gate → require a ready, unblocked spec, then build the plan.
                from pathlib import Path as _P
                spec_path = _P(sr) / "task_specs" / f"{spec_id}.json"
                if not spec_path.exists():
                    return {"content": [{"type": "text", "text":
                        "Spec chưa sẵn sàng — hỏi trạng thái trước."}]}
                spec = json.loads(spec_path.read_text(encoding="utf-8"))
                if (spec.get("clarify") or {}).get("needed"):
                    return {"content": [{"type": "text", "text":
                        "Còn câu hỏi cần trả lời trước khi tạo plan."}]}
                log_dir = Path(sr) / "ui_logs"; log_dir.mkdir(parents=True, exist_ok=True)
                argv = [
                    sys.executable, "-m", "ai_dev_system.task_graph.single_task_worker",
                    "--id", spec_id, "--mode", "plan", "--repo", pending["repo"],
                    "--storage-root", sr, "--database-url", str(config.database_url),
                ]
                try:
                    with open(log_dir / f"plan_{spec_id[:8]}.log", "a",
                              encoding="utf-8", errors="replace") as logf:
                        _spawn_worker(argv, stdout=logf, stderr=subprocess.STDOUT, cwd=str(_REPO_ROOT))
                except Exception as exc:  # pragma: no cover
                    return {"content": [{"type": "text", "text": f"plan spawn error: {exc}"}]}
                chat_task_store.update(surface, chat_id, phase="plan_generating")
                return {"content": [{"type": "text", "text":
                    "✅ Đã duyệt spec. Đang tạo plan… Hỏi trạng thái để xem plan."}]}
            if reject:
                chat_task_store.clear(surface, chat_id)
                return {"content": [{"type": "text", "text": "Đã huỷ task."}]}
            return {"content": [{"type": "text", "text":
                "Nhắn 'duyệt' để tiếp tục, hoặc 'từ chối' để huỷ."}]}
```

- [ ] **Step 3: Update `dev_task_start` reply wording (spec-first)**

At lines ~627-628, change the note text:

```python
        text = json.dumps({"spec_id": spec_id, "status": "spec_generating",
                           "note": "Đang tạo spec. Hỏi trạng thái rồi nhắn 'duyệt' để duyệt spec (sau đó mình tạo plan)."})
```

- [ ] **Step 4: Update + add the gateway tests**

In `tests/unit/harness/test_dev_task_tools.py`, replace `test_status_shows_plan_when_spec_ready` with a spec-gate assertion and add two tests:

```python
def test_status_shows_spec_gate_when_spec_ready_no_plan(tmp_path):
    cfg = _Cfg(tmp_path, [_Bot("tg", repo_path="/repos/app", base_branch="main")])
    store = ChatTaskStore(str(tmp_path))
    store.set_pending("tg", "1", spec_id="s1", repo="/repos/app", base_branch="main")
    _seed_spec(tmp_path, "s1")
    tools = make_dev_pipeline_tools(
        surface="tg", chat_id="1", conn_factory=lambda: None, config=cfg,
        link_store=None, chat_task_store=store,
    )
    status = _find(tools, "dev_run_status")
    out = asyncio.run(status.handler({"run_id": ""}))
    txt = out["content"][0]["text"].lower()
    assert "spec" in txt and "duyệt" in txt
    # plan is NOT materialized at the spec gate
    assert not (tmp_path / "task_specs" / "s1-plan.json").exists()
    assert store.get_pending("tg", "1")["phase"] == "awaiting_spec_approval"


def test_status_shows_plan_gate_when_plan_ready(tmp_path):
    cfg = _Cfg(tmp_path, [_Bot("tg", repo_path="/repos/app", base_branch="main")])
    store = ChatTaskStore(str(tmp_path))
    store.set_pending("tg", "1", spec_id="s1b", repo="/repos/app", base_branch="main")
    _seed_spec(tmp_path, "s1b")
    from ai_dev_system.task_graph.single_task_plan import plan_single_task, plan_path
    plan_single_task({"task": {"title": "t"}, "facets": {}}, "s1b", storage_root=str(tmp_path))
    # simulate a published plan doc
    pp = plan_path(str(tmp_path), "s1b")
    import json as _j
    pl = _j.loads(pp.read_text(encoding="utf-8")); pl["doc_url"] = "https://github.com/o/r/blob/b/p.md"
    pp.write_text(_j.dumps(pl), encoding="utf-8")
    tools = make_dev_pipeline_tools(
        surface="tg", chat_id="1", conn_factory=lambda: None, config=cfg,
        link_store=None, chat_task_store=store,
    )
    status = _find(tools, "dev_run_status")
    out = asyncio.run(status.handler({"run_id": ""}))
    txt = out["content"][0]["text"].lower()
    assert "plan" in txt and "bước" in txt and "github.com" in txt


def test_approve_spec_spawns_plan_worker(tmp_path):
    cfg = _Cfg(tmp_path, [_Bot("tg", repo_path="/repos/app", base_branch="main")])
    store = ChatTaskStore(str(tmp_path))
    store.set_pending("tg", "1", spec_id="s7", repo="/repos/app", base_branch="main")
    _seed_spec(tmp_path, "s7")  # spec ready, no plan yet
    spawned = []
    tools = make_dev_pipeline_tools(
        surface="tg", chat_id="1", conn_factory=lambda: None, config=cfg,
        link_store=None, chat_task_store=store,
        spawn_task_worker=lambda argv, **kw: spawned.append(argv),
    )
    gate = _find(tools, "dev_answer_gate")
    out = asyncio.run(gate.handler({"run_id": "", "text": "duyệt"}))
    assert spawned and "--mode" in spawned[0] and "plan" in spawned[0]
    assert "s7" in spawned[0]
    assert store.get_pending("tg", "1")["phase"] == "plan_generating"
    assert "plan" in out["content"][0]["text"].lower()
```

(`test_approve_spawns_executor` is unchanged and must stay green: it pre-builds the plan, so `load_plan` is non-None → plan gate → executor.)

- [ ] **Step 5: Run the gateway tests**

Run: `python -m pytest tests/unit/harness/test_dev_task_tools.py tests/unit/harness/test_dev_answer_gate.py -q`
Expected: PASS (all, including the unchanged `test_approve_spawns_executor`).

- [ ] **Step 6: Commit**

```bash
git add src/ai_dev_system/harness/tools/dev_pipeline.py tests/unit/harness/test_dev_task_tools.py
git commit -m "feat(gateway): two-gate flow — approve spec, then generate+publish plan, then execute"
```

---

### Task 6: Full suite + manual smoke note

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `python -m pytest -q`
Expected: PASS with the same baseline as before plus the new tests (no regressions). Investigate any failure before proceeding — pay attention to `tests/unit/test_single_task_executor.py`, `tests/integration/test_tdd_first_executor.py`, and `tests/integration/test_executor_e2e.py` (they exercise the executor whose helpers moved).

- [ ] **Step 2: Commit any test fixups**

```bash
git add -A
git commit -m "test: fixups after git_ops extraction + two-gate flow"
```

- [ ] **Step 3: Manual smoke (record, do not automate)**

On a repo-bound bot (Telegram), document the expected sequence for the user to verify live:
1. Send a task → bot: "Đang tạo spec…".
2. Ask status → bot: "📄 Spec sẵn sàng: <github link> — nhắn 'duyệt' để tạo plan." Open the link: `…-spec.md` exists on `ai-dev/task-…`.
3. "duyệt" → bot: "Đã duyệt spec. Đang tạo plan…".
4. Ask status → bot: "📋 Plan sẵn sàng (N bước): <github link> — nhắn 'duyệt' để chạy." Open the link: `…-plan.md` on the same branch.
5. "duyệt" → execution runs; ask status → PR link. The PR contains both docs + the code.

---

## Self-Review

**Spec coverage:**
- Two task-named files → Task 3 (`spec_doc_relpath`/`plan_doc_relpath`, `-spec.md`/`-plan.md`) ✓
- Merge-safe unique paths → Task 3 (`task-{id8}-{slug}`) + test ✓
- Same feature branch → Tasks 3/4 (`branch_name_for`, `ensure_branch_from_base`) ✓
- Commit + push + link → Task 3 (`publish_doc`, `blob_url`) ✓
- Update = new commit → Task 3 integration test (second publish adds a commit) ✓
- Two-gate (approve spec → generate plan → approve plan → execute) → Tasks 4/5 ✓
- Plan not generated until spec approved → Task 5 `dev_run_status` (no auto-plan) + `dev_answer_gate` spec gate ✓
- Git IO off the gateway thread → Tasks 4/5 (worker subprocess does publishing) ✓
- No-repo / push-failure guards → Task 3 (`publish_doc` returns None) + Task 4 (link omitted) ✓
- Vietnamese-safe → Task 3 `slugify` test + `encoding="utf-8"` everywhere ✓

**Placeholder scan:** none — every code step has complete content.

**Type consistency:** `publish_doc(repo_path, branch, relpath, content, commit_msg) -> str|None` used identically in Tasks 3/4. `run_plan_worker(spec_id, *, storage_root, database_url=None) -> dict` matches its caller in `main`. `load_plan`/`approve_plan` signatures match `single_task_plan.py`. Gate routing keys (`doc_url`, `spec_doc_url`, `phase`) are written and read consistently across Tasks 4/5.
