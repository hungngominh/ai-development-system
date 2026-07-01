# webui + CLI-direct per-project (SP-3)

**Date:** 2026-07-01
**Status:** Approved (design)
**Parent effort:** Per-project data isolation. SP-1 (foundation) + SP-2 (gateway routing) merged. This is **SP-3 of 5**.

## Goal

Let a human run the webui or a direct CLI command against a specific project so it reads/writes that project's `<repo>/.ai-dev/state/{control.db, storage/}` — one instance per repo. Opt-in via `--repo` / `AIDEV_REPO`; without it, behavior is the global default (unchanged).

## Mechanism (uniform with SP-2)

Both webui and CLI call `Config.from_env()`, which reads `STORAGE_ROOT` / `DATABASE_URL`. So a single entry-point step — resolve `--repo` and overlay those two env vars for the whole process — makes every downstream `Config.from_env()` and every spawned subprocess (which inherits the env) target the project. No per-command refactor.

### `config.apply_project_env(repo_path) -> ProjectPaths` (new)
```
paths = resolve_project(repo_path, ensure=True)   # SP-1: mkdir + .gitignore + schema
os.environ["STORAGE_ROOT"] = paths.storage_root
os.environ["DATABASE_URL"] = paths.database_url
return paths
```
- Blank `repo_path` → `ValueError` (propagated from `resolve_project`).
- Idempotent (safe to call once per process at startup).

## Components

### CLI root callback (`cli/main.py`)
- Add a root option: `repo: Optional[str] = typer.Option(None, "--repo", envvar="AIDEV_REPO", help="Target repo — use its <repo>/.ai-dev/state for DB + storage (default: global).")`.
- In the callback body, **before any subcommand runs**, `if repo: apply_project_env(repo)`.
- Result: `start`, `phase-b`, `gate`, `intake`, `info`, and every subprocess they spawn resolve to the project. No change to individual commands or `CLIContext`.

### webui (`webui.py`)
- Add `_maybe_apply_project(argv=None) -> None`: compute `repo = <parsed --repo> or os.environ.get("AIDEV_REPO")`; if set, `apply_project_env(repo)`. (`--repo` argv takes precedence over the env var.)
- `main()` calls `_maybe_apply_project()` **before** starting the HTTP server. `_config()` stays `Config.from_env()` — it now reads the overlaid env, so every request + spawned worker/executor/`start` targets the project. No other webui logic changes.

## Non-goals (SP-3)
- No cwd auto-detection — resolution is explicit via `--repo`/`AIDEV_REPO` only (auto-detect deferred to SP-5 TUI).
- No multi-project browser UI — one webui instance serves one project (run more instances on other ports for more projects).
- No per-command refactor to thread a Config object (the env overlay covers them).
- No migration of existing global-DB data.

## Edge cases
- **No `--repo`/`AIDEV_REPO`** → env untouched → global default (back-compat; existing tests green).
- **`--repo` flag vs `AIDEV_REPO` env** → the explicit flag wins (both CLI via typer `envvar` precedence, and webui via `argv or env`).
- **Blank/whitespace `--repo`** → `ValueError` from `resolve_project` (fail fast; the operator gave a bad path).
- **os.environ mutation** — acceptable: CLI is one-shot; webui is one-project-per-instance. Tests set/restore env via monkeypatch and only trigger resolution when `--repo` is passed.
- **`--repo` also flows to spawned subprocesses** — they inherit the overlaid `os.environ`, so `ai-dev start` (spawned by webui with no explicit flags) lands in the project too, consistent with the workers that already pass explicit `--storage-root`/`--database-url` (now the project values via `_config()`).

## Testing (TDD)
- `apply_project_env(repo)`: sets `os.environ["STORAGE_ROOT"]`/`["DATABASE_URL"]` to the project's paths, returns `ProjectPaths`, creates `<repo>/.ai-dev/state` (ensure); blank → `ValueError`. Restore env with monkeypatch.
- CLI root `--repo`: invoking the app (typer `CliRunner`) with `--repo <tmp>` and a trivial/`info` command overlays the env to the project paths; without `--repo`, env is untouched. `AIDEV_REPO` env alone also triggers it.
- webui `_maybe_apply_project`: `--repo` argv wins over `AIDEV_REPO`; env-only path works; neither set → no-op (env untouched). Monkeypatch `apply_project_env` to assert it is (not) called with the right repo.
- Regression: existing CLI/webui/start tests pass unchanged when no repo is given.
