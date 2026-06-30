# Repo-bound Bot → Chat → PR (vertical slice) — Design Spec
_Date: 2026-06-30_

## Context

Today a Telegram bot's `label` is only a routing key — nothing maps a bot/run to a repo on disk. The chat assistant has 3 tools (`dev_newproject_start`, `dev_run_status`, `dev_answer_gate`) and they ALL drive the new-project debate pipeline. The only existing-repo→branch→PR capability is the **single-task executor**, which is **WebUI-only**, and the gateway Docker image has **no `git`/`gh`**.

This spec designs the smallest closable loop that makes a bot work on a real repo:

> Message a repo-bound bot with a task → single-task executor runs on that repo → approve the plan over chat → bot replies with the PR link.

**Locked decisions (from brainstorming):**
- Scope: **existing git repo** only (new-project flow unchanged; SVN repos out of scope — deferred).
- Mount model: **per-bot mount**, written by the wizard into a `docker-compose.override.yml`.
- Path model: `repo_path` stored as the **container** path (`/repos/<label>`); the override maps host→container.
- Delivery: **git branch + `git push` + `gh pr create`** (GitHub only).
- Gates: **one gate (approve plan over chat) → auto-PR** after a successful, internally-reviewed run. A diff-approval gate is a future add.
- Auth: gh auth via **mounted `~/.config/gh`** (same pattern as `~/.claude`); git identity via env.
- Test repo: a real **git** repo chosen at smoke time (CoShareAsyncAPI is SVN → deferred).

---

## Verified codebase facts (anchors)

- `TelegramBotConfig(label, token, allowed_chat_ids)` — `config.py:25`; parsed from `AI_DEV_TELEGRAM_BOTS` JSON at `config.py:67-81` (keys `label`/`token`/`chat_ids`).
- Chat tools live in `src/ai_dev_system/harness/tools/dev_pipeline.py:84-481`; registered per-chat in `assistant/factory.py:_build_chat_runtime` (lines ~76-119).
- Single-task path (reused, not reimplemented):
  - `python -m ai_dev_system.task_graph.single_task_worker --id <spec_id> --idea <task> --repo <repo> --storage-root .. --database-url ..` → writes `task_specs/<spec_id>.json` (contains `repo`, `task`, `facets`).
  - `plan_single_task(spec_id)` → builds plan → `task_specs/<spec_id>-plan.json`.
  - `python -m ai_dev_system.task_graph.single_task_executor --id <spec_id> --storage-root .. --database-url ..` → branch, execute via `PhaseRoutingAgent(repo_path, branch, base_branch)`, `_push_branch_compare` (push).
  - PR creation: `_accept_branch_create_pr(branch, base, repo, title, body)` in `webui.py:973` runs `git push -u origin` + `gh pr create` (+ `gh pr view` for URL).
- `RepoBranchAgent`/`PhaseRoutingAgent` run `claude -p ... --permission-mode bypassPermissions` with `cwd=repo` and `git diff base..HEAD` — `repo_branch_agent.py:233,456`. They need `claude` + `git` on PATH.
- Dockerfile installs `nodejs npm @anthropic-ai/claude-code` only — **no `git`, no `gh`**.

---

## Components

### 1. Config — `repo_path` + `base_branch` per bot
**File:** `src/ai_dev_system/config.py`

Extend `TelegramBotConfig`:
```python
@dataclass(frozen=True)
class TelegramBotConfig:
    label: str
    token: str
    allowed_chat_ids: tuple[int, ...] = ()
    repo_path: str = ""          # container-side path, e.g. /repos/my-app; "" = new-project-only bot
    base_branch: str = ""        # optional override; "" = executor auto-detects main/master
```
Parser reads `repo_path` and `base_branch` from each bot JSON object (default `""`). Back-compat: bots without these fields behave exactly as today.

### 2. Wizard — capture repo + write override mount
**File:** `src/ai_dev_system/cli/telegram_setup.py`

`run_telegram_setup` gains one prompt after the project name: *"Đường dẫn repo trên host (bỏ trống nếu bot chỉ tạo project mới):"*.
- If provided (`host_repo`): validate it exists and is a git repo (`<host_repo>/.git` present). If not a git repo, warn and store no repo binding (don't abort the bot creation).
- Compute container path `/repos/<label>`; write `repo_path` + `base_branch` (default `"main"`) into the bot entry via an extended `upsert_bot_in_env`.
- Append the mount to `docker-compose.override.yml` via a new pure helper `add_bot_mount(override_text, label, host_repo) -> str`:
  ```yaml
  services:
    gateway:
      volumes:
        - "E:/Work/my-app:/repos/my-app:rw"
  ```
  Idempotent: don't duplicate a mount whose container target is `/repos/<label>`.

New pure helpers (unit-testable, no IO/network):
- `container_repo_path(label) -> str` → `f"/repos/{label}"`
- `add_bot_mount(override_text: str, label: str, host_repo: str) -> str`
- `upsert_bot_in_env(..., repo_path="", base_branch="")` — extended to write the two new keys when non-empty.

### 3. Image — `git` + `gh` + auth
**Files:** `Dockerfile`, `docker-compose.yml`

Dockerfile: install `git` and the GitHub CLI (`gh`). git is in Debian; gh needs its apt source or a pinned `.deb` download — install via the official apt repo. Keep the existing `RUN claude --version` fast-fail; add `RUN git --version && gh --version` as a build-time check.

docker-compose.yml additions:
- Mount gh auth read-only: `- "${GH_CONFIG_DIR}:/root/.config/gh:ro"`.
- git identity via env: `GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL`, `GIT_COMMITTER_NAME`, `GIT_COMMITTER_EMAIL` (from `.env`).

**Gateway startup hook (Python, same place as the schema self-heal in `gateway.py`):** a `_ensure_git_ready()` helper runs, best-effort, once at startup:
- `gh auth setup-git` so `git push` over HTTPS uses gh's credential helper,
- `git config --global --add safe.directory '*'` so a host-mounted repo (different uid) isn't rejected.
Doing this in Python (not a `docker-entrypoint.sh`) avoids Windows CRLF breakage on a shell script and mirrors the existing `_ensure_schema` pattern. Failures are logged, not fatal (a new-project-only deployment without gh still boots).

`.env.example` gains: `GH_CONFIG_DIR=C:/Users/yourname/.config/gh`, `GIT_AUTHOR_NAME`, `GIT_AUTHOR_EMAIL` (with comments).

### 4. Shared PR helper (targeted extraction)
**Files:** new `src/ai_dev_system/vcs/github_pr.py`; `webui.py` refactor to call it.

`_accept_branch_create_pr` currently lives inside `webui.py`. Extract the push+PR logic into:
```python
def create_pr(repo: str, branch: str, base: str, title: str, body: str = "") -> str:
    """git push -u origin <branch> then gh pr create; return PR URL. Idempotent
    (if PR exists, return its URL via gh pr view)."""
```
`webui.py` keeps its endpoint but delegates to `create_pr`. The chat tool calls the same helper. This avoids duplicating git/gh subprocess logic in two places.

### 5. Chat tool — `dev_task_start` + plan gate
**Files:** `src/ai_dev_system/harness/tools/dev_pipeline.py`, `assistant/factory.py`

`_build_chat_runtime(surface, chat_id)` resolves the bot's `repo_path`/`base_branch` from config (match `surface == label`) and passes them to a new tool factory, alongside the existing tools.

New tool **`dev_task_start(task_description: str)`**:
1. Guard: if the bound `repo_path` is empty → return an error telling the user this bot isn't repo-bound (run `ai-dev telegram setup` with a repo).
2. Spawn `single_task_worker(--id <new spec_id> --idea <task_description> --repo <repo_path> ...)` (detached, same pattern as `dev_newproject_start`).
3. Record the pending spec for this chat: store `spec_id` in the chat **session state** (via the assistant's session store) keyed by (surface, chat_id), kind `task_plan`.
4. Return `{"spec_id": ..., "status": "spec_generating"}` → assistant tells the user the plan is being prepared.

Extend **`dev_run_status`**: when the chat has a pending `task_plan` spec, report whether the spec+plan are ready and include a short plan summary (read `task_specs/<spec_id>.json` + `-plan.json`). When ready, status is presented as "plan ready — reply 'duyệt' to run".

Extend **`dev_answer_gate`**: if the chat has a pending `task_plan` spec and the user's text approves (reuse the existing approve/reject regex at `dev_pipeline.py:246-258`):
1. Ensure the plan exists (call `plan_single_task(spec_id)` if not already built).
2. Spawn `single_task_executor(--id <spec_id> ...)` (detached).
3. On terminal success (poll run status / exec-status JSON), call `create_pr(repo, branch, base, title, body)` and reply the PR URL.
4. On failure, reply the failure summary (from the exec log/status).

(Branch name + base come from the executor's run metadata: `runs.metadata.branch`; base via executor auto-detect or the bot's `base_branch`.)

---

## Data flow

```
TG "thêm validation cho field email"  (chat bound to bot label=my-app, repo_path=/repos/my-app)
  → dev_task_start: spawn single_task_worker(idea, repo=/repos/my-app) → task_specs/<id>.json
  → session pending {spec_id, kind:task_plan}; reply "📋 Đang tạo plan..."
TG "xong chưa"  → dev_run_status → plan ready → reply plan summary + "nhắn 'duyệt' để chạy"
TG "duyệt"
  → dev_answer_gate: plan_single_task(if needed) → spawn single_task_executor(spec_id)
  → executor: branch ai-dev/<id>, claude execution, internal ReviewAgent gate, push
  → create_pr(repo, branch, base, title) → reply "✅ PR: https://github.com/.../pull/N"
```

---

## Error handling

- **Bot not repo-bound** (`repo_path==""`): `dev_task_start` returns a clear message; no spawn.
- **repo missing / not a git repo in container**: executor's first `git` call fails → surface the stderr summary to chat, no PR.
- **gh not authed / push denied**: `create_pr` catches non-zero `gh`/`git` exit → reply "push/PR thất bại: <stderr tail>"; the branch still exists locally for manual recovery.
- **Spec/plan/exec subprocess crash**: poll detects terminal non-success → reply failure summary from the `.log`/exec-status file.
- **Concurrent task on same chat**: if a pending `task_plan` already exists for the chat, `dev_task_start` refuses with "đang có task chờ duyệt" (one in-flight task per chat for the slice).

---

## Testing

**Unit (pytest, deterministic):**
- `config.py`: bot JSON with `repo_path`/`base_branch` parses into `TelegramBotConfig`; absent → defaults `""`; back-compat bots unchanged.
- `telegram_setup.py`: `container_repo_path`, `add_bot_mount` (idempotent, preserves other services/volumes), `upsert_bot_in_env` writes the new keys; all pure on in-memory strings.
- `dev_pipeline.py`: `dev_task_start` with empty `repo_path` returns the guard error (no spawn); with a repo_path it invokes the (injected) spawner with the right argv; `dev_answer_gate` approval path calls the (injected) executor spawner + `create_pr` with branch/base from a fake run row. Use dependency injection for subprocess spawn + `create_pr` so no real processes/network run.
- `vcs/github_pr.create_pr`: with an injected runner, asserts the `git push` + `gh pr create` argv and URL parsing; idempotent path (`gh pr view`) on "already exists".

**Manual smoke (like packaging Task 8 — needs a real git repo + gh auth + Docker):**
1. Pick a throwaway git repo with a GitHub origin; `ai-dev telegram setup` binds it (writes override mount).
2. `docker-compose up -d --build`; confirm `git`/`gh` present and `gh auth status` OK in-container.
3. Message the bot a small task → approve plan → confirm a real PR appears on GitHub.

---

## Out of scope (this slice)

- New-project debate flow becoming repo-bound (unchanged; still artifact-only).
- Multi-task graph execution over chat.
- The diff-approval gate (only the plan gate here).
- Non-GitHub remotes; **SVN repos** (CoShareAsyncAPI deferred).
- More than one in-flight task per chat.

---

## Risks & verify during implementation

- **`gh` install in `python:3.12-slim`**: the apt repo route adds a key + source list; verify the build succeeds and image size stays acceptable. Fallback: pinned `.deb`.
- **gh auth in container**: mounting `~/.config/gh` read-only + `gh auth setup-git` must make `git push` work over HTTPS without interactive prompts. Verify in smoke.
- **Windows bind-mount of a git repo into Linux container**: line-ending/permission quirks; `safe.directory '*'` is set by `_ensure_git_ready()` at startup — verify in smoke that `git`/push actually work on the mounted repo.
- **claude running as root in container** (carried over from packaging): may need `IS_SANDBOX=1`.
- **Pending-spec state across assistant restart**: session state must persist the pending `spec_id` (it's in the session store/DB, not just memory) so "duyệt" works even if the daemon restarted.
- **PR helper extraction**: ensure `webui.py` behavior is unchanged after delegating to `create_pr` (existing webui tests must stay green).
