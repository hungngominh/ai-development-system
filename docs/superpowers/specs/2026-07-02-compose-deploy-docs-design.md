# Compose/deploy + docs for per-project data (SP-4)

**Date:** 2026-07-02
**Status:** Approved (design)
**Parent effort:** Per-project data isolation. SP-1/SP-2/SP-3 merged. This is **SP-4 of 5** (SP-5 = TUI, future).

## Goal

Make the Docker deploy config and the prose docs match the shipped per-project data model: each repo-bound bot/CLI/webui uses `<repo>/.ai-dev/state/{control.db, storage/}`; the global `/data` location is only the fallback for bots with no bound repo. Put global data on the host too (bind-mount), and document the two-tier model so a new operator understands where data lives and how `--repo`/`AIDEV_REPO` work.

No application logic changes — the runtime behavior shipped in SP-1..SP-3.

## Changes

### 1. `docker-compose.yml`
- Replace the named volume `ai-dev-data:/data` with a host bind-mount `./data:/data`, and delete the top-level `volumes: ai-dev-data:` declaration.
- Keep `DATABASE_URL: sqlite:////data/control.db` and `STORAGE_ROOT: /data/storage` — now explicitly the **global fallback** for non-repo bots. Add a short comment saying per-project data lives in `<repo>/.ai-dev/state/` and this `/data` is only the fallback.
- Nothing else changes (auth mounts, IS_SANDBOX, git identity, override file all stay).

### 2. `.gitignore` (ai-dev repo root)
- Add `/data/` so the bind-mounted global fallback data never enters git. (Create `.gitignore` addition; preserve existing entries.)

### 3. `.env.example`
- Add commented lines documenting the two vars as the global fallback:
  ```
  # Global fallback DB + storage (used only by bots with NO bound repo, and by
  # CLI/webui runs without --repo). Repo-bound work auto-uses <repo>/.ai-dev/state/.
  # DATABASE_URL=sqlite:///~/.ai-dev-system/control.db
  # STORAGE_ROOT=~/.ai-dev-system/storage
  ```
  (Commented — the compose file sets the container values; this documents them for non-Docker runs.)

### 4. Docs — two-tier data model
Add a concise, consistent description to each, WITHOUT breaking existing doc-reconciliation invariants (SQLite, intake front-door, single-task working, module tree, skills table, no live postgres):

- **README.md** — a short bullet/subsection under status/architecture: per-project `<repo>/.ai-dev/state/{control.db, storage/}` is the default when a bot is repo-bound or `--repo`/`AIDEV_REPO` is set; global `~/.ai-dev-system` (or `/data` in Docker) is the fallback for non-repo bots. Keep the existing "Persistence: SQLite" line.
- **SETUP.md** — a "Per-project data" subsection: data auto-initializes in `<repo>/.ai-dev/state/` (gitignored via an auto-created `<repo>/.ai-dev/.gitignore`); run webui/CLI against a project with `--repo <path>` or `AIDEV_REPO=<path>`; a repo-bound Telegram bot is automatically per-project. Note the global fallback for non-repo bots.
- **docs/architecture.md** — extend the `db/` description to state the two-tier layout (per-project DB inside the repo; global fallback for non-repo bots).
- **docs/workflow-v2.md** — out of scope (pipeline-flow doc, not deploy).

### 5. New tests in `tests/unit/test_docs_reconciliation.py`
Encode the new doc invariants as executable checks (matches the file's existing philosophy; guards against drift):
- README mentions the per-project path token `.ai-dev/state` AND the `--repo` flag or `AIDEV_REPO`.
- SETUP.md mentions `.ai-dev/state` AND (`--repo` or `AIDEV_REPO`) AND a global-fallback notion.
- docs/architecture.md mentions `.ai-dev/state`.

## Non-goals
- No migration of existing named-volume `/data` data (fresh; it's only the fallback).
- No Dockerfile change (`mkdir -p /data/storage` is harmless under the bind overlay).
- No application/logic change; no workflow-v2.md rewrite.

## Edge cases / risks
- `./data` appears in the ai-dev repo dir when running Docker → gitignored; documented.
- Container writes `/data` as root on a Windows bind-mount — same as the already-working repo bind-mount; acceptable.
- Doc-reconciliation: all EXISTING assertions must stay green; the new tests only ADD checks. Adding tests bumps the collected count → README test-count line updated in the final step.

## Testing
- New doc-reconciliation assertions above (run `tests/unit/test_docs_reconciliation.py` → all green, including the pre-existing ones).
- Full suite green; README test count bumped to the live collected count.
- (Compose is not unit-tested; correctness verified by the explorer's analysis that the global env is fallback-only and the per-project override happens at runtime — no conflict.)
