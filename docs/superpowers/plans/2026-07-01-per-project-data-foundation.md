# Per-project data foundation (SP-1) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add pure per-project data-location logic (`resolve_project` / `ProjectPaths`) plus an in-process `ProjectRegistry` cache, so later sub-projects can route each bound repo to its own `<repo>/.ai-dev/state/{control.db, storage/}`.

**Architecture:** `ProjectPaths` + `resolve_project(repo_path, ensure=True)` live in `config.py` (low-dep, reusable by CLI/webui/worker/TUI). `resolve_project` deterministically derives the four paths and, when `ensure=True`, creates dirs, manages `<repo>/.ai-dev/.gitignore` (adds a `state/` line), and applies the DB schema idempotently. `ProjectRegistry` (in `gateway/`, where live connections belong) lazily builds + caches per-repo `(paths, conn, conn_factory)`. SP-1 wires nothing into production — only tests consume it.

**Tech Stack:** Python 3.12, stdlib `os`/`pathlib`/`sqlite3`/`dataclasses`, pytest. Reuses `db.connection.get_connection` and `db.migrator.apply_schema`.

## Global Constraints

- SP-1 changes ONLY `config.py` (additions) + a new `gateway/project_registry.py` + tests. Do NOT touch `Config`, `build_gateway`, `AssistantFactory`, watchers, dev_pipeline, worker/executor, webui, or CLI.
- Data layout is `<repo>/.ai-dev/state/{control.db, storage/}`; `database_url = sqlite:///<repo>/.ai-dev/state/control.db` (built the same way as the existing `DEFAULT_DATABASE_URL`).
- `resolve_project(..., ensure=False)` performs NO disk IO. `ensure=True` is idempotent.
- `.gitignore` management must PRESERVE existing lines and add `state/` at most once.
- Blank `repo_path` → `ValueError`. No global-fallback logic in SP-1.
- All file writes use `encoding="utf-8"`.
- Repo paths normalized via `os.path.abspath` so cache keys and paths are stable.

---

### Task 1: `ProjectPaths` + `resolve_project` in `config.py`

**Files:**
- Modify: `src/ai_dev_system/config.py` (add dataclass + function; keep existing content)
- Test: `tests/unit/test_project_paths.py`

**Interfaces:**
- Consumes: `db.connection.get_connection`, `db.migrator.apply_schema` (existing).
- Produces (SP-2 + Task 2 depend on these):
  - `ProjectPaths` (frozen dataclass): `repo_path: str`, `root: str`, `storage_root: str`, `database_url: str`
  - `resolve_project(repo_path: str, *, ensure: bool = True) -> ProjectPaths`

- [ ] **Step 1: Write the failing tests (pure computation)**

```python
# tests/unit/test_project_paths.py
import os
import sqlite3
from pathlib import Path

import pytest

from ai_dev_system.config import ProjectPaths, resolve_project


def test_resolve_project_pure_paths_no_io(tmp_path):
    repo = tmp_path / "myrepo"  # does not exist on disk
    p = resolve_project(str(repo), ensure=False)
    assert isinstance(p, ProjectPaths)
    assert p.repo_path == os.path.abspath(str(repo))
    assert p.root == os.path.join(p.repo_path, ".ai-dev", "state")
    assert p.storage_root == os.path.join(p.root, "storage")
    assert p.database_url == f"sqlite:///{os.path.join(p.root, 'control.db')}"
    # ensure=False must not create anything
    assert not (repo / ".ai-dev").exists()


def test_resolve_project_two_repos_distinct_db(tmp_path):
    a = resolve_project(str(tmp_path / "a"), ensure=False)
    b = resolve_project(str(tmp_path / "b"), ensure=False)
    assert a.database_url != b.database_url


def test_resolve_project_blank_raises():
    with pytest.raises(ValueError):
        resolve_project("", ensure=False)
    with pytest.raises(ValueError):
        resolve_project("   ", ensure=False)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_project_paths.py -q`
Expected: FAIL (ImportError: cannot import `ProjectPaths` / `resolve_project`).

- [ ] **Step 3: Implement `ProjectPaths` + the pure part of `resolve_project`**

Add to `src/ai_dev_system/config.py` (after the existing imports / near `TelegramBotConfig`). The imports `os` and `Path` already exist at the top of the file; add `from dataclasses import dataclass` is already imported (`dataclass, field`). Add:

```python
@dataclass(frozen=True)
class ProjectPaths:
    """Per-project data locations under <repo>/.ai-dev/state/."""
    repo_path: str
    root: str
    storage_root: str
    database_url: str


def resolve_project(repo_path: str, *, ensure: bool = True) -> ProjectPaths:
    """Derive (and optionally initialize) a project's data location.

    Layout: <repo>/.ai-dev/state/{control.db, storage/}. With ensure=True this
    creates the dirs, adds a `state/` line to <repo>/.ai-dev/.gitignore, and
    applies the DB schema (idempotent). With ensure=False it is pure — no IO.
    """
    if not repo_path or not str(repo_path).strip():
        raise ValueError("resolve_project requires a non-empty repo_path")
    repo = os.path.abspath(str(repo_path).strip())
    root = os.path.join(repo, ".ai-dev", "state")
    storage_root = os.path.join(root, "storage")
    db_path = os.path.join(root, "control.db")
    paths = ProjectPaths(
        repo_path=repo,
        root=root,
        storage_root=storage_root,
        database_url=f"sqlite:///{db_path}",
    )
    if ensure:
        _ensure_project(paths)
    return paths
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_project_paths.py -q`
Expected: PASS (3 tests). (`_ensure_project` is only referenced on the `ensure=True` path, not exercised yet.)

- [ ] **Step 5: Add the failing tests for `ensure=True`**

Append to `tests/unit/test_project_paths.py`:

```python
def _table_names(db_url: str) -> set[str]:
    from ai_dev_system.db.connection import get_connection
    conn = get_connection(db_url)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        return {r["name"] for r in rows}
    finally:
        conn.close()


def test_ensure_creates_dirs_gitignore_and_schema(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    p = resolve_project(str(repo), ensure=True)
    assert Path(p.root).is_dir()
    assert Path(p.storage_root).is_dir()
    gi = repo / ".ai-dev" / ".gitignore"
    assert gi.exists()
    assert "state/" in gi.read_text(encoding="utf-8").splitlines()
    # schema applied: a known control-layer table exists
    assert "runs" in _table_names(p.database_url)


def test_ensure_is_idempotent(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    resolve_project(str(repo), ensure=True)
    resolve_project(str(repo), ensure=True)  # must not raise
    gi = repo / ".ai-dev" / ".gitignore"
    # exactly one state/ line
    assert gi.read_text(encoding="utf-8").splitlines().count("state/") == 1


def test_ensure_preserves_existing_gitignore(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".ai-dev").mkdir(parents=True)
    gi = repo / ".ai-dev" / ".gitignore"
    gi.write_text("# custom\nnotes.txt\n", encoding="utf-8")
    resolve_project(str(repo), ensure=True)
    lines = gi.read_text(encoding="utf-8").splitlines()
    assert "notes.txt" in lines and "# custom" in lines
    assert lines.count("state/") == 1
```

- [ ] **Step 6: Run the new tests to verify they fail**

Run: `python -m pytest tests/unit/test_project_paths.py -q`
Expected: FAIL (`_ensure_project` is not defined → `NameError` when `ensure=True`).

- [ ] **Step 7: Implement `_ensure_project`**

Add to `src/ai_dev_system/config.py` (below `resolve_project`):

```python
def _ensure_project(paths: "ProjectPaths") -> None:
    """Idempotent init for a project's data dir: mkdir, .gitignore, schema."""
    from ai_dev_system.db.connection import get_connection
    from ai_dev_system.db.migrator import apply_schema

    os.makedirs(paths.storage_root, exist_ok=True)  # also creates root/.ai-dev

    # .gitignore: add a `state/` line once, preserving any existing content.
    gi = Path(paths.repo_path) / ".ai-dev" / ".gitignore"
    gi.parent.mkdir(parents=True, exist_ok=True)
    if gi.exists():
        content = gi.read_text(encoding="utf-8")
        if "state/" not in [ln.strip() for ln in content.splitlines()]:
            sep = "" if content == "" or content.endswith("\n") else "\n"
            gi.write_text(content + sep + "state/\n", encoding="utf-8")
    else:
        gi.write_text("state/\n", encoding="utf-8")

    # Apply schema to the project DB (idempotent); fail fast on a real error.
    conn = get_connection(paths.database_url)
    try:
        results = apply_schema(conn)
        failed = [
            r for r in results
            if r.error or (not r.applied and r.skipped_reason == "file not found")
        ]
        if failed:
            details = "; ".join(f"{r.name}: {r.error or r.skipped_reason}" for r in failed)
            raise RuntimeError(f"project schema apply failed: {details}")
    finally:
        conn.close()
```

- [ ] **Step 8: Run the full test file to verify all pass**

Run: `python -m pytest tests/unit/test_project_paths.py -q`
Expected: PASS (6 tests).

- [ ] **Step 9: Commit**

```bash
git add src/ai_dev_system/config.py tests/unit/test_project_paths.py
git commit -m "feat(config): resolve_project + ProjectPaths (per-project data location)"
```

---

### Task 2: `ProjectRegistry` in `gateway/project_registry.py`

**Files:**
- Create: `src/ai_dev_system/gateway/project_registry.py`
- Test: `tests/unit/gateway/test_project_registry.py`

**Interfaces:**
- Consumes: `config.resolve_project`, `config.ProjectPaths`, `db.connection.get_connection`.
- Produces (SP-2 depends on these):
  - `ProjectResources` (frozen dataclass): `paths: ProjectPaths`, `conn: sqlite3.Connection`, `conn_factory: Callable[[], sqlite3.Connection]`
  - `ProjectRegistry` with `get(repo_path: str) -> ProjectResources` and `close_all() -> None`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/gateway/test_project_registry.py
import sqlite3
from pathlib import Path

import pytest

from ai_dev_system.gateway.project_registry import ProjectRegistry, ProjectResources


def test_get_returns_usable_resources(tmp_path):
    reg = ProjectRegistry()
    try:
        res = reg.get(str(tmp_path / "repo"))
        assert isinstance(res, ProjectResources)
        # schema applied → can query a control-layer table
        rows = res.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='runs'"
        ).fetchall()
        assert len(rows) == 1
        # conn_factory returns the SAME cached connection
        assert res.conn_factory() is res.conn
    finally:
        reg.close_all()


def test_get_caches_per_repo(tmp_path):
    reg = ProjectRegistry()
    try:
        r1 = reg.get(str(tmp_path / "repo"))
        r2 = reg.get(str(tmp_path / "repo"))
        assert r1 is r2  # same cached ProjectResources
    finally:
        reg.close_all()


def test_two_repos_independent_dbs(tmp_path):
    reg = ProjectRegistry()
    try:
        a = reg.get(str(tmp_path / "a"))
        b = reg.get(str(tmp_path / "b"))
        assert a.conn is not b.conn
        assert a.paths.database_url != b.paths.database_url
        assert Path(a.paths.root, "control.db").exists()
        assert Path(b.paths.root, "control.db").exists()
    finally:
        reg.close_all()


def test_close_all_closes_conns_and_is_safe_twice(tmp_path):
    reg = ProjectRegistry()
    res = reg.get(str(tmp_path / "repo"))
    reg.close_all()
    with pytest.raises(sqlite3.ProgrammingError):
        res.conn.execute("SELECT 1")
    reg.close_all()  # second call must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/gateway/test_project_registry.py -q`
Expected: FAIL (ModuleNotFoundError: `project_registry`).

- [ ] **Step 3: Implement `project_registry.py`**

```python
# src/ai_dev_system/gateway/project_registry.py
"""In-process cache of per-project data resources (paths + live DB connection).

One shared connection per project, mirroring the daemon's single-threaded
one-connection model. Built lazily on first get(); closed via close_all() at
daemon shutdown.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable
import sqlite3

from ai_dev_system.config import ProjectPaths, resolve_project
from ai_dev_system.db.connection import get_connection


@dataclass(frozen=True)
class ProjectResources:
    paths: ProjectPaths
    conn: sqlite3.Connection
    conn_factory: Callable[[], sqlite3.Connection]


class ProjectRegistry:
    """Lazily resolve + cache per-repo data resources."""

    def __init__(self) -> None:
        self._cache: dict[str, ProjectResources] = {}

    def get(self, repo_path: str) -> ProjectResources:
        key = os.path.abspath(str(repo_path).strip())
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        paths = resolve_project(key, ensure=True)
        conn = get_connection(paths.database_url)
        res = ProjectResources(paths=paths, conn=conn, conn_factory=lambda: conn)
        self._cache[key] = res
        return res

    def close_all(self) -> None:
        for res in self._cache.values():
            try:
                res.conn.close()
            except Exception:  # noqa: BLE001 — shutdown best-effort
                pass
        self._cache.clear()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/gateway/test_project_registry.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/gateway/project_registry.py tests/unit/gateway/test_project_registry.py
git commit -m "feat(gateway): ProjectRegistry — per-project paths + cached DB connection"
```

---

### Task 3: Full suite + README test-count bump

**Files:**
- Modify: `README.md` (test count in the `## Trạng thái` section — only if the number changed)

**Interfaces:** none (verification only).

- [ ] **Step 1: Run the full test suite**

Run: `python -m pytest -q -p no:cacheprovider`
Expected: all pass EXCEPT possibly `tests/unit/test_docs_reconciliation.py::test_readme_test_count_matches_collected_count`, which fails because Tasks 1–2 added tests and the README count is now stale. No other failures are acceptable — investigate any.

- [ ] **Step 2: Get the live collected count**

Run: `python -m pytest --collect-only -q -p no:cacheprovider` and read the final `N tests collected` line.

- [ ] **Step 3: Update the README count**

In `README.md`, `## Trạng thái` section, update the `- **<N> tests** — …` line to the collected count from Step 2 (currently `1921`; it will become `1921 + number of new tests`).

- [ ] **Step 4: Verify the reconciliation test passes**

Run: `python -m pytest tests/unit/test_docs_reconciliation.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs(readme): bump test count for per-project data foundation (SP-1)"
```

---

## Self-Review

**Spec coverage:**
- `ProjectPaths` (repo_path/root/storage_root/database_url) → Task 1 ✓
- `resolve_project(ensure=False)` pure, no IO → Task 1 (test asserts nothing created) ✓
- `resolve_project(ensure=True)`: mkdir + gitignore + schema, idempotent → Task 1 (`_ensure_project`) ✓
- `.gitignore` preserves existing lines, adds `state/` once → Task 1 test ✓
- Blank repo_path → ValueError → Task 1 test ✓
- database_url form `sqlite:///…/control.db` → Task 1 test ✓
- `ProjectRegistry.get` (cached per repo, usable conn, conn_factory identity) → Task 2 ✓
- Two repos → independent conns + db files → Task 2 test ✓
- `close_all` closes + safe twice → Task 2 test ✓
- Registry in `gateway/`, resolver in `config.py` → Tasks 2 & 1 file placement ✓
- Non-goals (no rewiring) honored → only config.py additions + new module + tests ✓
- README-count chore → Task 3 ✓

**Placeholder scan:** none — every code step is complete.

**Type consistency:** `resolve_project(repo_path, *, ensure=True) -> ProjectPaths` used identically in Task 2's `ProjectRegistry.get`. `ProjectPaths` field names (`repo_path`, `root`, `storage_root`, `database_url`) match across Tasks 1–2 and tests. `ProjectResources` fields (`paths`, `conn`, `conn_factory`) match the Task 2 test usage. `apply_schema` result attributes (`error`, `applied`, `skipped_reason`, `name`) match the real `MigrationResult` shape used by the existing `gateway._ensure_schema`.
