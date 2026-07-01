# Per-project data foundation — `resolve_project` + `ProjectRegistry` (SP-1)

**Date:** 2026-07-01
**Status:** Approved (design)
**Parent effort:** Per-project data isolation (each bound repo gets its own DB + storage under `<repo>/.ai-dev/state/`, one gateway serving many projects). This is **SP-1 of 5** — see decomposition below.

## Context & decomposition

Today the whole system shares ONE global `storage_root` + `database_url` (Config). The goal is per-project isolation so (a) DB/storage live on the host inside each project dir, and (b) a future native TUI run inside a repo shares the same data. Approved decomposition:

1. **SP-1 (this spec)** — foundation: compute + initialize a project's data location; cache per-repo resources. No entrypoint rewired yet.
2. SP-2 — gateway per-project routing (factory/daemon/watchers project-aware; subprocess env injection).
3. SP-3 — CLI + webui per-project (one instance per repo).
4. SP-4 — compose/deploy + docs.
5. SP-5 (future) — TUI.

SP-1 ships standalone and reviewable: it is pure path logic + initialization + an in-process cache. Nothing in production calls it yet (only tests) — SP-2 wires it in.

## Data layout (target, established here)

```
<repo>/.ai-dev/
  tasks/                      # committed (spec/plan md — shipped feature, untouched)
  .gitignore                 # auto-managed: contains a line `state/`
  state/                     # gitignored — per-project runtime
    control.db               # per-project SQLite DB, full schema, self-contained
    storage/                 # per-project storage_root (subdirs created by consumers)
```

## Components

### `ProjectPaths` (in `config.py`)
Frozen dataclass — pure value object, no IO:
- `repo_path: str` — absolute, normalized repo root
- `root: str` — `<repo>/.ai-dev/state`
- `storage_root: str` — `<repo>/.ai-dev/state/storage`
- `database_url: str` — `sqlite:///<repo>/.ai-dev/state/control.db`

### `resolve_project(repo_path, *, ensure=True) -> ProjectPaths` (in `config.py`)
- Normalizes `repo_path` to an absolute path and computes the four fields.
- `database_url` is built as `f"sqlite:///{db_path}"` (same construction as the existing `DEFAULT_DATABASE_URL`, which already works on Windows).
- **`ensure=False`**: pure computation, **no disk IO** (for read-only callers / tests).
- **`ensure=True`** (default): idempotent initialization —
  1. `mkdir -p` for `state/` and `storage/`.
  2. Ensure `<repo>/.ai-dev/.gitignore` contains a `state/` line: create the file with `state/\n` if absent; if present, append `state/` only when that exact line is missing; never rewrite/clobber existing lines.
  3. Apply the control-layer schema to `control.db` (idempotent, via `db.connection.get_connection` + `db.migrator.apply_schema`); close that init connection.
- **Empty/blank `repo_path`** → raise `ValueError`. Fallback-to-global is the caller's responsibility (SP-2 decides per-bot); SP-1 does not embed global fallback.

### `ProjectRegistry` (new module `src/ai_dev_system/gateway/project_registry.py`)
In-process, single-threaded cache (mirrors the daemon's existing one-shared-conn model):
- `get(repo_path) -> ProjectResources` — lazily `resolve_project(repo_path, ensure=True)`, open ONE shared `sqlite3.Connection` to that project DB, cache by normalized `repo_path`, and return a `ProjectResources`.
- `ProjectResources` (frozen dataclass): `paths: ProjectPaths`, `conn: sqlite3.Connection`, `conn_factory: Callable[[], sqlite3.Connection]` (returns the cached conn — same shape the gateway/assistant code already expects).
- `close_all() -> None` — close every cached connection (daemon shutdown); safe to call repeatedly.
- Same `repo_path` (after normalization) returns the **same** `ProjectResources`/conn; different repos get independent conns + DB files.

Registry lives in `gateway/` because it holds live connections for the daemon; the pure `resolve_project`/`ProjectPaths` live in `config.py` so CLI/webui/worker/TUI can reuse them without importing gateway.

## Non-goals (SP-1)
- No changes to `Config`, `build_gateway`, `AssistantFactory`, watchers, dev_pipeline, worker/executor, webui, or CLI. (All SP-2/SP-3.)
- No global fallback logic, no env-var injection, no subprocess wiring.
- No migration of existing global-DB data.

## Edge cases
- Existing `.ai-dev/.gitignore` with unrelated entries → `state/` appended once; other lines preserved.
- `resolve_project` called twice → second call is a no-op init (dirs exist, gitignore line present, schema already applied).
- `repo_path` with trailing slash / mixed separators → normalized so the cache key and paths are stable.
- Registry `get` on an unwritable/invalid repo path → the underlying `mkdir`/`get_connection` error propagates (caller in SP-2 will guard); SP-1 does not swallow it.

## Testing (TDD)

Pure (`ensure=False`, no disk):
- `resolve_project` computes `root`/`storage_root`/`database_url` correctly from a repo path; `database_url` is `sqlite:///…/.ai-dev/state/control.db`; two different repos → two different db URLs.
- Blank `repo_path` raises `ValueError`.

Init (`ensure=True`, tmp dir):
- Creates `state/` and `state/storage/`.
- Writes `.ai-dev/.gitignore` containing `state/`; a pre-existing `.gitignore` with other content keeps that content and gains exactly one `state/` line; a second `resolve_project` does not duplicate it.
- Applies schema: opening `control.db` shows a known table (e.g. `runs`) exists.
- Idempotent: calling twice raises nothing and leaves one `state/` line.

Registry (tmp dirs):
- `get(repo)` returns `ProjectResources` whose `conn` can query a schema table; `conn_factory()` returns the same connection object; a second `get(repo)` returns the same cached conn.
- Two different repos → different `database_url`, different db files on disk, different conn objects.
- `close_all()` closes conns (subsequent use raises `ProgrammingError`) and can be called twice safely.
