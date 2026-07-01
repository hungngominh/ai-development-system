# webui + CLI-direct per-project (SP-3) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a human target one project by passing `--repo`/`AIDEV_REPO` to the CLI or webui, so the whole process (and its spawned subprocesses) uses `<repo>/.ai-dev/state/{control.db, storage/}`.

**Architecture:** A shared `config.apply_project_env(repo)` resolves the project (SP-1 `resolve_project`, ensure=True) and overlays `STORAGE_ROOT`/`DATABASE_URL` on `os.environ`. The CLI root callback calls it when `--repo` is given (before any subcommand); webui calls it at `main()` startup. Everything downstream already reads those env vars via `Config.from_env()`, so no per-command changes.

**Tech Stack:** Python 3.12, typer, argparse, pytest (`typer.testing.CliRunner`).

## Global Constraints

- Opt-in only via `--repo` / `AIDEV_REPO`; no cwd auto-detection. No repo → global default (back-compat, existing tests green).
- `--repo` flag takes precedence over `AIDEV_REPO` env.
- Blank/whitespace repo → `ValueError` (from `resolve_project`).
- Only `config.py`, `cli/main.py`, `webui.py` + tests touched. No per-command refactor, no migration.

---

### Task 1: `apply_project_env` in `config.py`

**Files:**
- Modify: `src/ai_dev_system/config.py` (add function near `resolve_project`)
- Test: `tests/unit/test_apply_project_env.py`

**Interfaces:**
- Consumes: `resolve_project(repo_path, *, ensure=True) -> ProjectPaths` (SP-1, same module).
- Produces: `apply_project_env(repo_path: str) -> ProjectPaths`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_apply_project_env.py
import os
from pathlib import Path

import pytest

from ai_dev_system.config import apply_project_env


def test_overlays_env_and_returns_paths(tmp_path, monkeypatch):
    # Track these keys so monkeypatch restores them on teardown even though
    # apply_project_env mutates os.environ directly.
    monkeypatch.setenv("STORAGE_ROOT", "sentinel")
    monkeypatch.setenv("DATABASE_URL", "sentinel")
    repo = tmp_path / "repo"
    paths = apply_project_env(str(repo))
    assert os.environ["STORAGE_ROOT"] == paths.storage_root
    assert os.environ["DATABASE_URL"] == paths.database_url
    assert paths.storage_root == os.path.join(paths.repo_path, ".ai-dev", "state", "storage")
    assert Path(paths.root).is_dir()          # ensure=True created it


def test_blank_repo_raises(monkeypatch):
    monkeypatch.setenv("STORAGE_ROOT", "sentinel")
    with pytest.raises(ValueError):
        apply_project_env("   ")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_apply_project_env.py -q`
Expected: FAIL (ImportError: `apply_project_env`).

- [ ] **Step 3: Implement**

Add to `src/ai_dev_system/config.py` right after `resolve_project` (`os` is already imported at the top):

```python
def apply_project_env(repo_path: str) -> "ProjectPaths":
    """Resolve a project (creating its data dir) and overlay STORAGE_ROOT +
    DATABASE_URL onto os.environ so every Config.from_env() in this process —
    and every subprocess that inherits the env — targets that project."""
    paths = resolve_project(repo_path, ensure=True)
    os.environ["STORAGE_ROOT"] = paths.storage_root
    os.environ["DATABASE_URL"] = paths.database_url
    return paths
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_apply_project_env.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/config.py tests/unit/test_apply_project_env.py
git commit -m "feat(config): apply_project_env — overlay STORAGE_ROOT/DATABASE_URL for a repo"
```

---

### Task 2: CLI root `--repo` option

**Files:**
- Modify: `src/ai_dev_system/cli/main.py` (`_root_callback`)
- Test: `tests/unit/cli/test_root_repo_option.py`

**Interfaces:**
- Consumes: `config.apply_project_env`.
- Produces: `ai-dev --repo <path> <cmd>` (and `AIDEV_REPO` env) overlays the project env before the subcommand runs.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/cli/test_root_repo_option.py
from typer.testing import CliRunner

import ai_dev_system.cli.main as cli_main

runner = CliRunner()


def test_repo_flag_invokes_apply_project_env(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cli_main, "apply_project_env", lambda r: calls.append(r))
    # any registered subcommand; callback runs before it. Exit code irrelevant.
    runner.invoke(cli_main.app, ["--repo", str(tmp_path / "repo"), "info"])
    assert calls == [str(tmp_path / "repo")]


def test_aidev_repo_env_invokes_apply_project_env(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cli_main, "apply_project_env", lambda r: calls.append(r))
    monkeypatch.setenv("AIDEV_REPO", str(tmp_path / "envrepo"))
    runner.invoke(cli_main.app, ["info"])
    assert calls == [str(tmp_path / "envrepo")]


def test_no_repo_does_not_invoke(monkeypatch):
    calls = []
    monkeypatch.setattr(cli_main, "apply_project_env", lambda r: calls.append(r))
    monkeypatch.delenv("AIDEV_REPO", raising=False)
    runner.invoke(cli_main.app, ["info"])
    assert calls == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/cli/test_root_repo_option.py -q`
Expected: FAIL (no `--repo` option; `apply_project_env` not imported in `cli.main`).

- [ ] **Step 3: Implement**

In `src/ai_dev_system/cli/main.py`, add the import near the top:

```python
from ai_dev_system.config import apply_project_env
```

Add a `repo` option to `_root_callback` (after the `dry_run` option, keeping the signature style):

```python
    repo: Optional[str] = typer.Option(
        None,
        "--repo",
        envvar="AIDEV_REPO",
        help="Target repo — use its <repo>/.ai-dev/state for DB + storage (default: global).",
    ),
```

At the very start of the callback body (before building `CLIContext`), add:

```python
    if repo:
        apply_project_env(repo)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/cli/test_root_repo_option.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/cli/main.py tests/unit/cli/test_root_repo_option.py
git commit -m "feat(cli): --repo/AIDEV_REPO root option routes to per-project state"
```

---

### Task 3: webui `--repo`/`AIDEV_REPO` at startup

**Files:**
- Modify: `src/ai_dev_system/webui.py` (`main` + new `_maybe_apply_project`)
- Test: `tests/unit/test_webui_repo.py`

**Interfaces:**
- Consumes: `config.apply_project_env`.
- Produces: `_maybe_apply_project(argv=None) -> None`; `main()` calls it before serving.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_webui_repo.py
import ai_dev_system.webui as webui


def test_repo_argv_wins_over_env(monkeypatch):
    calls = []
    monkeypatch.setattr(webui, "apply_project_env", lambda r: calls.append(r))
    monkeypatch.setenv("AIDEV_REPO", "/from/env")
    webui._maybe_apply_project(["--repo", "/from/argv"])
    assert calls == ["/from/argv"]


def test_env_used_when_no_argv(monkeypatch):
    calls = []
    monkeypatch.setattr(webui, "apply_project_env", lambda r: calls.append(r))
    monkeypatch.setenv("AIDEV_REPO", "/from/env")
    webui._maybe_apply_project([])
    assert calls == ["/from/env"]


def test_noop_when_neither(monkeypatch):
    calls = []
    monkeypatch.setattr(webui, "apply_project_env", lambda r: calls.append(r))
    monkeypatch.delenv("AIDEV_REPO", raising=False)
    webui._maybe_apply_project([])
    assert calls == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_webui_repo.py -q`
Expected: FAIL (`_maybe_apply_project` / `apply_project_env` not in webui).

- [ ] **Step 3: Implement**

In `src/ai_dev_system/webui.py`, add near the top imports:

```python
import argparse
from ai_dev_system.config import apply_project_env
```

(If `argparse`/`os` are already imported, don't duplicate — check the existing imports first.)

Add the helper above `main`:

```python
def _maybe_apply_project(argv=None) -> None:
    """If --repo (argv) or AIDEV_REPO (env) is set, overlay that project's
    STORAGE_ROOT/DATABASE_URL for this process. --repo wins over the env var."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--repo", default=None)
    known, _ = parser.parse_known_args(argv)
    repo = known.repo or os.environ.get("AIDEV_REPO")
    if repo:
        apply_project_env(repo)
```

Call it at the very start of `main()`:

```python
def main() -> None:
    _maybe_apply_project()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"AI Dev System dashboard -> http://localhost:{PORT}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
```

Note: `PORT` is read at import time today. `_maybe_apply_project` only sets `STORAGE_ROOT`/`DATABASE_URL`, which are read later per-request via `_config()`/`Config.from_env()`, so ordering is fine.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_webui_repo.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/webui.py tests/unit/test_webui_repo.py
git commit -m "feat(webui): --repo/AIDEV_REPO at startup routes to per-project state"
```

---

### Task 4: Full suite + README test-count bump

**Files:**
- Modify: `README.md` (test count in `## Trạng thái`, only if changed)

- [ ] **Step 1: Run the full suite**

Run: `python -m pytest -q -p no:cacheprovider`
Expected: all pass EXCEPT possibly `test_docs_reconciliation.py::test_readme_test_count_matches_collected_count` (stale count). No other failures — investigate any (esp. `tests/unit/cli/*`, webui tests).

- [ ] **Step 2: Get the live collected count**

Run: `python -m pytest --collect-only -q -p no:cacheprovider` and read the final `N tests collected` line.

- [ ] **Step 3: Update the README count**

In `README.md`, `## Trạng thái`, set the `- **<N> tests** — …` line to the collected count (currently `1940`).

- [ ] **Step 4: Verify reconciliation passes**

Run: `python -m pytest tests/unit/test_docs_reconciliation.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs(readme): bump test count for webui/CLI per-project (SP-3)"
```

---

## Self-Review

**Spec coverage:**
- `apply_project_env(repo)` overlays env + returns paths, blank→ValueError → Task 1 ✓
- CLI root `--repo` (envvar `AIDEV_REPO`) → apply_project_env before subcommand → Task 2 ✓
- webui `_maybe_apply_project` (`--repo` argv wins over `AIDEV_REPO`), called in `main()` → Task 3 ✓
- Opt-in only, no repo → global (back-compat) → Tasks 2/3 no-op tests ✓
- Flag precedence over env → Tasks 2 (typer envvar) & 3 (`known.repo or env`) ✓
- README chore → Task 4 ✓
- Non-goals (no cwd autodetect, no multi-project UI, no per-command refactor, no migration) → honored ✓

**Placeholder scan:** none — every code step is complete.

**Type consistency:** `apply_project_env(repo_path: str) -> ProjectPaths` defined in Task 1, patched/called by name in Tasks 2 (`cli_main.apply_project_env`) and 3 (`webui.apply_project_env`). `_maybe_apply_project(argv=None)` defined and tested consistently in Task 3.
