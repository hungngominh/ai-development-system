# Gateway per-project routing (SP-2)

**Date:** 2026-07-01
**Status:** Approved (design)
**Parent effort:** Per-project data isolation. SP-1 (foundation: `resolve_project` + `ProjectRegistry`) is merged. This is **SP-2 of 5**.

## Goal

Route the gateway daemon so each repo-bound Telegram bot reads/writes its **own** `<repo>/.ai-dev/state/{control.db, storage/}` (via SP-1's `ProjectRegistry`), while bots without a bound repo keep using the global DB/storage. After SP-2, a bot→task→PR flow stores all its runtime data inside the bound repo. webui and direct-CLI stay on the global path (SP-3).

## Routing hub

`surface` (Telegram bot label) → `repo_path` (from `config.telegram_bots`) → `ProjectRegistry.get(repo_path)` → per-project resources. A repo-bound chat, single-task run, and spawned subprocess all resolve to the same project data. Bots with empty `repo_path` → global fallback (unchanged behavior).

New shared helper in `config.py`: `repo_path_for_label(telegram_bots, label) -> str` (returns `""` if none) — centralizes the lookup currently duplicated in `build_clarify_prompt_suffix` and `make_dev_pipeline_tools`.

## Components

### `gateway/project_registry.py` — extend `ProjectResources` into the per-project hub
`ProjectResources` gains three fields, built eagerly in `ProjectRegistry.get` on the project's `conn_factory`:
- `link_store: RunLinkStore`
- `session_store: SessionStore`
- `budget: BudgetTracker`

Existing fields (`paths`, `conn`, `conn_factory`) unchanged. `close_all()` unchanged (closes conns). This makes `ProjectRegistry.get(repo)` the single source of every per-project object the gateway needs.

### `assistant/factory.py` — project-aware `for_chat`
- `build_assistant_factory(...)` gains an optional `project_registry` param. The factory keeps its existing **global** `conn_factory`/`link_store`/`session_store`/`budget`/`config` as the fallback for non-repo bots.
- `AssistantFactory.for_chat(surface, chat_id)`:
  1. `repo = repo_path_for_label(config.telegram_bots, surface)`.
  2. If `repo` and `project_registry` present → `res = project_registry.get(repo)`; use `res.conn_factory`, `res.link_store`, `res.session_store`, `res.budget`, and `res.paths.storage_root` / `res.paths.database_url`.
  3. Else → global fallback (today's behavior).
  4. Build the per-chat runtime with the resolved pieces; `session_id` comes from the resolved `session_store`.
- `_build_chat_runtime` passes the resolved `conn_factory`, `link_store`, per-project `storage_root`, and `database_url` into `make_dev_pipeline_tools`.

### `harness/tools/dev_pipeline.py` — per-project storage/DB + subprocess env
- `make_dev_pipeline_tools(...)` gains `storage_root: str | None = None` and `database_url: str | None = None` (default to `config.storage_root` / `config.database_url` when not given → back-compat for existing callers/tests). Every current use of `config.storage_root` / `config.database_url` inside the tools uses the resolved values instead.
- Subprocess spawns:
  - `single_task_worker` / `single_task_executor` already take `--storage-root` / `--database-url` → pass the per-project values.
  - `ai-dev start` (dev_newproject_start) and `phase-b …` read `Config.from_env()` → inject `env` into the spawn: a copy of `os.environ` overlaid with `STORAGE_ROOT` + `DATABASE_URL` set to the per-project values. `_real_spawn` already forwards `**kwargs` to `Popen`, so pass `env=…`.
- The bound `repo_path` resolution (for `--repo`) continues via `repo_path_for_label`.

### `cli/commands/gateway.py` — `build_gateway` per-project wiring
- Create one `ProjectRegistry` for the daemon.
- Keep the existing **global** conn/link_store/session_store/budget for non-repo bots.
- Build the assistant factory with `project_registry=registry` (plus the global fallbacks it already receives).
- **Watchers:** for each distinct bound `repo_path` among `config.telegram_bots`, `registry.get(repo)` and build a `RunStatusWatcher` + `ClarifyWatcher` bound to that project's `conn_factory`/`link_store`/`storage_root`/`session_store`. Add one global pair iff any non-repo bot exists. `_post_poll` runs `check_once()` on every watcher in the list.
- **Resume/shutdown bookkeeping:** the clean-shutdown file check (`consume_clean_shutdown(home)`) stays single (file-based). When it indicates an unclean restart, call `mark_recent_resume_pending()` on **every** project session store and the global one. `mark_clean_shutdown(home)` on graceful exit is unchanged.
- On shutdown, `registry.close_all()`.

## Non-goals (SP-2)
- webui and direct-CLI (`ai-dev start/phase-b/gate` invoked by a human, not spawned by the gateway) — SP-3.
- Migrating existing global-DB data into per-project DBs — repo-bound bots start fresh in `<repo>/.ai-dev/state/`.
- Changing single-task/worker/executor internals (they already accept `--storage-root`/`--database-url`).

## Edge cases & risks
- **Non-repo bot** → global DB/storage exactly as today (fallback path fully preserved).
- **Two bots, same repo_path** → share one `ProjectResources` (registry cache keyed by abspath) — one conn, one watcher pair. Correct (same project).
- **Config with no telegram_bots** (REPL/tests) → factory `project_registry` unused; global path. No behavior change.
- **Subprocess env**: inject only `STORAGE_ROOT`/`DATABASE_URL` overlay; keep the rest of `os.environ` (tokens, `IS_SANDBOX`, git identity) intact.
- **Back-compat**: `make_dev_pipeline_tools` new params default to config values, so `build_assistant_factory` without a registry and all existing tests behave unchanged.
- **SQLite on Windows bind-mount** (Docker path) may aggravate the known "database is locked" issue since each project DB now lives under a mounted repo; WAL + `busy_timeout=5000` already set; native path (SP-5 TUI) unaffected. Documented risk, not fixed here.

## Testing (TDD)
- `repo_path_for_label`: returns the bot's repo for a matching label; `""` for no match / no repo.
- `ProjectRegistry.get`: returned `ProjectResources` now carries usable `link_store`/`session_store`/`budget` (e.g. `link_store.link(...)` then read back on the project DB); two repos → independent stores on independent DB files.
- `AssistantFactory.for_chat`: with a registry + a repo-bound surface, the dev tools' spawns carry the **project** `--storage-root`/`--database-url` (assert argv), and subprocess `env` overlay for `start`/`phase-b` carries project `STORAGE_ROOT`/`DATABASE_URL`; a non-repo surface still uses the global values. Use injected `spawn_*` recorders (existing pattern) — no real forks.
- `make_dev_pipeline_tools`: given explicit `storage_root`/`database_url`, single-task spawns and status reads target those paths; omitted → falls back to config values (existing tests stay green).
- `build_gateway`: with two repo-bound bots, builds two project watcher pairs (+ global iff a non-repo bot exists); `_post_poll` invokes `check_once` on all; `registry.close_all()` called on shutdown. Use a stub registry/watchers to assert wiring without real DBs where possible.
- Regression: existing `tests/unit/harness/test_dev_task_tools.py`, `test_dev_answer_gate.py`, and gateway/factory tests pass unchanged (global fallback path).
