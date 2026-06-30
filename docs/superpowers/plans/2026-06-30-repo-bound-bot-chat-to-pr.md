# Repo-bound Bot → Chat → PR (vertical slice) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Message a repo-bound Telegram bot with a task → the single-task executor runs on that repo → approve the plan over chat → the bot replies with the PR link.

**Architecture:** Bind each bot to a `repo_path` (container path) in config; the wizard captures the host repo and writes a per-bot mount into `docker-compose.override.yml`. The image gains `git`+`gh`; auth is mounted. A new `dev_task_start` chat tool spawns the existing single-task worker on the bound repo, a file-backed `ChatTaskStore` remembers the pending spec per chat, `dev_run_status`/`dev_answer_gate` are extended to show the plan, approve it, run the executor, and open a PR via an extracted `create_pr` helper. Everything reuses the existing single-task worker/plan/executor; no reimplementation.

**Tech Stack:** Python 3.11+ (typer CLI, `@tool` from `claude_agent_sdk`), stdlib `subprocess`/`json`, Docker, `git`+`gh` CLIs, SQLite (unchanged).

## Global Constraints

- Scope: **existing git repo** only. New-project flow unchanged. SVN out of scope.
- `repo_path` stored is the **container** path `"/repos/<label>"`; the override file maps host→container. Empty `repo_path` → bot is new-project-only (back-compat, unchanged).
- Bot JSON keys: `label`, `token`, `chat_ids`, and new optional `repo_path`, `base_branch`. `AI_DEV_TELEGRAM_BOTS` stays **single-line JSON**.
- Delivery: **git branch + `git push` + `gh pr create`** (GitHub only). One gate (approve plan) → auto-PR.
- Reuse, do not reimplement: `single_task_worker` (argv `--id --idea --repo --storage-root --database-url`), `plan_single_task(spec, spec_id, *, storage_root)`, `approve_plan(storage_root, spec_id)`, `single_task_executor` (argv `--id --storage-root --database-url`), and the extracted `create_pr`.
- Chat tools return `{"content": [{"type": "text", "text": <str>}]}`. Subprocess spawns are **injectable** for tests (never fork real processes in unit tests).
- Force UTF-8 where Vietnamese is printed. Repo convention: any task adding tests MUST bump the README count (enforced by `tests/unit/test_docs_reconciliation.py`); current baseline **1853**.
- DRY, YAGNI, TDD, frequent commits.

---

## File Structure

- Modify `src/ai_dev_system/config.py` — `TelegramBotConfig` gains `repo_path`, `base_branch`; parser reads them.
- Modify `src/ai_dev_system/cli/telegram_setup.py` — `container_repo_path`, `add_bot_mount` (pure), extend `upsert_bot_in_env` + `run_telegram_setup`.
- Modify `Dockerfile` — install `git` + `gh`. Modify `docker-compose.yml` + `.env.example` — gh auth mount + git identity. Modify `src/ai_dev_system/cli/commands/gateway.py` — `_ensure_git_ready()` at startup.
- Create `src/ai_dev_system/vcs/__init__.py` + `src/ai_dev_system/vcs/github_pr.py` — `create_pr(...)`; modify `webui.py` to delegate.
- Create `src/ai_dev_system/harness/tools/chat_task_store.py` — file-backed pending-task state.
- Modify `src/ai_dev_system/harness/tools/dev_pipeline.py` — resolve repo_path; add `dev_task_start`; extend `dev_run_status`/`dev_answer_gate`.
- Tests under `tests/unit/...` per task.

---

### Task 1: Config — `repo_path` + `base_branch` per bot

**Files:**
- Modify: `src/ai_dev_system/config.py` (`TelegramBotConfig` ~line 24-28; parser ~line 67-81)
- Test: `tests/unit/test_config_telegram.py`

**Interfaces:**
- Produces: `TelegramBotConfig(label, token, allowed_chat_ids=(), repo_path="", base_branch="")`; parser reads `repo_path`/`base_branch` from each bot JSON object.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_config_telegram.py`:

```python
def test_bot_parses_repo_path_and_base_branch(monkeypatch):
    from ai_dev_system.config import Config
    monkeypatch.setenv(
        "AI_DEV_TELEGRAM_BOTS",
        '[{"label":"my-app","token":"T","chat_ids":[1],'
        '"repo_path":"/repos/my-app","base_branch":"main"}]',
    )
    cfg = Config.from_env()
    bot = cfg.telegram_bots[0]
    assert bot.repo_path == "/repos/my-app"
    assert bot.base_branch == "main"


def test_bot_without_repo_fields_defaults_empty(monkeypatch):
    from ai_dev_system.config import Config
    monkeypatch.setenv(
        "AI_DEV_TELEGRAM_BOTS", '[{"label":"x","token":"T","chat_ids":[1]}]'
    )
    cfg = Config.from_env()
    assert cfg.telegram_bots[0].repo_path == ""
    assert cfg.telegram_bots[0].base_branch == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_config_telegram.py -k repo -v`
Expected: FAIL (`TypeError: __init__() got unexpected keyword` or `AttributeError: repo_path`).

- [ ] **Step 3: Add fields + parse**

In `config.py`, extend the dataclass:

```python
@dataclass(frozen=True)
class TelegramBotConfig:
    label: str
    token: str
    allowed_chat_ids: tuple[int, ...] = ()
    repo_path: str = ""
    base_branch: str = ""
```

In the parse loop (where `label`/`token`/`ids` are read), add and pass through:

```python
                    repo_path = str(b.get("repo_path") or "").strip()
                    base_branch = str(b.get("base_branch") or "").strip()
                    if label and token:
                        _bots.append(TelegramBotConfig(
                            label=label, token=token, allowed_chat_ids=ids,
                            repo_path=repo_path, base_branch=base_branch,
                        ))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_config_telegram.py -v`
Expected: PASS (existing tests still pass too).

- [ ] **Step 5: Bump README + commit**

Bump the test count in `README.md` to match `python -m pytest --collect-only -q 2>&1 | tail -1` (was 1853; +2 → 1855).

```bash
git add src/ai_dev_system/config.py tests/unit/test_config_telegram.py README.md
git commit -m "feat(config): repo_path + base_branch per Telegram bot"
```

---

### Task 2: Wizard — capture repo + write override mount

**Files:**
- Modify: `src/ai_dev_system/cli/telegram_setup.py`
- Test: `tests/unit/cli/test_telegram_setup.py`

**Interfaces:**
- Consumes: `upsert_bot_in_env` (Task 3 of prior plan — already exists).
- Produces:
  - `container_repo_path(label: str) -> str` → `f"/repos/{label}"`.
  - `add_bot_mount(override_text: str, label: str, host_repo: str) -> str` — returns YAML for `docker-compose.override.yml` with the gateway volume `"<host_repo>:/repos/<label>:rw"` added; idempotent on the container target.
  - `upsert_bot_in_env(env_text, label, token, chat_ids, repo_path="", base_branch="")` — extended to write `repo_path`/`base_branch` keys when non-empty.

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/cli/test_telegram_setup.py`:

```python
def test_container_repo_path():
    assert ts.container_repo_path("my-app") == "/repos/my-app"


def test_upsert_writes_repo_fields():
    env = "AI_DEV_TELEGRAM_BOTS=[]\n"
    out = ts.upsert_bot_in_env(env, "my-app", "T", [1],
                               repo_path="/repos/my-app", base_branch="main")
    import json
    line = next(l for l in out.splitlines() if l.startswith("AI_DEV_TELEGRAM_BOTS="))
    bot = json.loads(line.split("=", 1)[1])[0]
    assert bot["repo_path"] == "/repos/my-app"
    assert bot["base_branch"] == "main"


def test_upsert_omits_repo_fields_when_empty():
    env = "AI_DEV_TELEGRAM_BOTS=[]\n"
    out = ts.upsert_bot_in_env(env, "x", "T", [1])
    import json
    bot = json.loads(
        next(l for l in out.splitlines() if l.startswith("AI_DEV_TELEGRAM_BOTS="))
        .split("=", 1)[1]
    )[0]
    assert "repo_path" not in bot and "base_branch" not in bot


def test_add_bot_mount_fresh():
    out = ts.add_bot_mount("", "my-app", "E:/Work/my-app")
    assert "services:" in out and "gateway:" in out and "volumes:" in out
    assert '"E:/Work/my-app:/repos/my-app:rw"' in out


def test_add_bot_mount_idempotent():
    once = ts.add_bot_mount("", "my-app", "E:/Work/my-app")
    twice = ts.add_bot_mount(once, "my-app", "E:/Work/my-app")
    assert twice.count("/repos/my-app:rw") == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/cli/test_telegram_setup.py -k "container_repo or repo_fields or bot_mount" -v`
Expected: FAIL (`AttributeError: container_repo_path` / `add_bot_mount`; `upsert_bot_in_env` got unexpected kwargs).

- [ ] **Step 3: Implement the helpers**

In `telegram_setup.py`, add:

```python
def container_repo_path(label: str) -> str:
    """Container-side mount point for a bot's repo."""
    return f"/repos/{label}"


def add_bot_mount(override_text: str, label: str, host_repo: str) -> str:
    """Add a gateway volume mapping host_repo -> /repos/<label> to a
    docker-compose.override.yml body. Idempotent on the container target.
    Kept line-based (not a YAML lib) to avoid a new dependency; the file is
    wizard-owned so the shape is fixed."""
    target = container_repo_path(label)
    mount = f'      - "{host_repo}:{target}:rw"'
    lines = override_text.splitlines() if override_text.strip() else []
    if any(f":{target}:rw" in ln for ln in lines):
        return override_text if override_text.endswith("\n") else override_text + "\n"
    if not lines:
        lines = ["services:", "  gateway:", "    volumes:"]
    else:
        # Ensure the services/gateway/volumes scaffold exists.
        if "    volumes:" not in lines:
            # Append scaffold if a different shape; simplest: rebuild minimal block.
            if "  gateway:" not in lines:
                lines += ["  gateway:"]
            lines += ["    volumes:"]
    lines.append(mount)
    return "\n".join(lines) + "\n"
```

Extend `upsert_bot_in_env` signature and the appended dict:

```python
def upsert_bot_in_env(env_text, label, token, chat_ids, repo_path="", base_branch=""):
    ...
    entry = {"label": label, "token": token, "chat_ids": list(chat_ids)}
    if repo_path:
        entry["repo_path"] = repo_path
    if base_branch:
        entry["base_branch"] = base_branch
    bots.append(entry)
    ...
```

(Keep the rest of `upsert_bot_in_env` — dedup check, single-line `json.dumps`, in-place replace — unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/cli/test_telegram_setup.py -v`
Expected: PASS (all, including the prior telegram tests).

- [ ] **Step 5: Wire the prompt into `run_telegram_setup`**

After the project-label prompt in `run_telegram_setup`, before writing `.env`, add (uses the same `input_fn`):

```python
    host_repo = input_fn(
        "Đường dẫn repo trên host (Enter để bỏ qua — bot chỉ tạo project mới): "
    ).strip()
    repo_path = ""
    base_branch = ""
    if host_repo:
        from pathlib import Path as _P
        if not (_P(host_repo) / ".git").is_dir():
            print(f"⚠ {host_repo} không phải git repo — bỏ qua binding repo.")
        else:
            repo_path = container_repo_path(label)
            base_branch = "main"
            override = _P("docker-compose.override.yml")
            override_text = override.read_text(encoding="utf-8") if override.exists() else ""
            override.write_text(add_bot_mount(override_text, label, host_repo), encoding="utf-8")
            print(f"✅ Mount: {host_repo} → {repo_path} (docker-compose.override.yml)")
```

Then pass `repo_path=repo_path, base_branch=base_branch` into the existing `upsert_bot_in_env(...)` call.

- [ ] **Step 6: Verify the wizard flow test still passes + add an override-write test**

Add a flow test that injects `input_fn` returning a token, label, and a host repo path pointing at a tmp git dir, and asserts the bot entry has `repo_path` and the override file was written. Use `tmp_path` and `monkeypatch.chdir(tmp_path)` so the override file lands in tmp:

```python
def test_run_setup_binds_repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    repo = tmp_path / "myrepo"
    (repo / ".git").mkdir(parents=True)
    env = tmp_path / ".env"
    env.write_text("AI_DEV_TELEGRAM_BOTS=[]\n")
    inputs = iter(["123:ABC", "my-app", str(repo)])  # token, label, host repo
    import json
    rc = ts.run_telegram_setup(
        env, transport=_transport_with_message(chat_id=7),
        input_fn=lambda *_a, **_k: next(inputs),
        sleep_fn=lambda *_a, **_k: None,
    )
    assert rc == 0
    bot = json.loads(
        next(l for l in env.read_text().splitlines() if l.startswith("AI_DEV_TELEGRAM_BOTS="))
        .split("=", 1)[1]
    )[0]
    assert bot["repo_path"] == "/repos/my-app"
    assert (tmp_path / "docker-compose.override.yml").exists()
```

Run: `python -m pytest tests/unit/cli/test_telegram_setup.py -v` → PASS.

- [ ] **Step 7: Bump README + commit**

Bump README count to the new collected count.

```bash
git add src/ai_dev_system/cli/telegram_setup.py tests/unit/cli/test_telegram_setup.py README.md
git commit -m "feat(cli): telegram setup captures repo + writes per-bot compose override mount"
```

---

### Task 3: Image gets `git` + `gh` + git-ready startup

**Files:**
- Modify: `Dockerfile`, `docker-compose.yml`, `.env.example`
- Modify: `src/ai_dev_system/cli/commands/gateway.py` (add `_ensure_git_ready`)
- Test: `tests/unit/cli/test_gateway_schema.py` (add a git-ready wiring test)

**Interfaces:**
- Produces: `_ensure_git_ready() -> None` in `gateway.py`, called in `gateway_cmd` after `_ensure_schema`.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/cli/test_gateway_schema.py`:

```python
def test_ensure_git_ready_runs_setup_commands(monkeypatch):
    from ai_dev_system.cli.commands import gateway

    calls = []
    def fake_run(argv, **kw):
        calls.append(argv)
        class R: returncode = 0
        return R()
    monkeypatch.setattr(gateway.subprocess, "run", fake_run)

    gateway._ensure_git_ready()

    joined = [" ".join(a) for a in calls]
    assert any("gh auth setup-git" in j for j in joined)
    assert any("safe.directory" in j for j in joined)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/cli/test_gateway_schema.py -k git_ready -v`
Expected: FAIL (`AttributeError: _ensure_git_ready`, and `gateway.subprocess` may not exist yet).

- [ ] **Step 3: Add `_ensure_git_ready` + wire it + import subprocess**

In `gateway.py`, add `import subprocess` at the top (with the other imports), add the helper after `_ensure_schema`:

```python
def _ensure_git_ready() -> None:
    """Best-effort: make git/gh usable in the container for repo-bound bots.
    Failures are non-fatal (a new-project-only deployment without gh still boots)."""
    for argv in (
        ["gh", "auth", "setup-git"],
        ["git", "config", "--global", "--add", "safe.directory", "*"],
    ):
        try:
            subprocess.run(argv, capture_output=True, text=True)
        except Exception:  # noqa: BLE001
            pass
```

In `gateway_cmd`, after `_ensure_schema(cfg.database_url)`:

```python
    _ensure_git_ready()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/cli/test_gateway_schema.py -v`
Expected: PASS.

- [ ] **Step 5: Dockerfile — add git + gh**

In `Dockerfile`, replace the node/claude install RUN with one that also installs `git` and `gh` (GitHub CLI via its official apt repo):

```dockerfile
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm git ca-certificates curl gnupg \
    && npm install -g @anthropic-ai/claude-code \
    && mkdir -p -m 755 /etc/apt/keyrings \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
         | tee /etc/apt/keyrings/githubcli-archive-keyring.gpg > /dev/null \
    && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
         > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# Fail fast if any required CLI is missing.
RUN claude --version && git --version && gh --version
```

- [ ] **Step 6: docker-compose.yml + .env.example — gh auth + git identity**

In `docker-compose.yml`, add to the gateway `volumes:`:

```yaml
      # GitHub CLI auth (read-only) so the container can push + open PRs.
      - "${GH_CONFIG_DIR}:/root/.config/gh:ro"
```

and to `environment:`:

```yaml
      GIT_AUTHOR_NAME: "${GIT_AUTHOR_NAME}"
      GIT_AUTHOR_EMAIL: "${GIT_AUTHOR_EMAIL}"
      GIT_COMMITTER_NAME: "${GIT_AUTHOR_NAME}"
      GIT_COMMITTER_EMAIL: "${GIT_AUTHOR_EMAIL}"
```

In `.env.example`, add a section:

```bash
# ── Repo-bound bots (git PR flow) ─────────────────────────────────────────────
# Thư mục cấu hình GitHub CLI trên host (chứa auth của `gh auth login`).
GH_CONFIG_DIR=C:/Users/yourname/.config/gh
GIT_AUTHOR_NAME=Your Name
GIT_AUTHOR_EMAIL=you@example.com
```

- [ ] **Step 7: Build verification**

Run: `docker build -t ai-dev-system .`
Expected: build succeeds; the final `RUN` prints claude, git, and gh versions. (Requires Docker Desktop. If `gh` apt install fails, report the exact error — do not silently drop `gh`.)

- [ ] **Step 8: Bump README + commit**

```bash
git add Dockerfile docker-compose.yml .env.example src/ai_dev_system/cli/commands/gateway.py tests/unit/cli/test_gateway_schema.py README.md
git commit -m "build: add git+gh to image; gh/git auth mounts; _ensure_git_ready at gateway startup"
```

---

### Task 4: Extract `create_pr` into `vcs/github_pr.py`

**Files:**
- Create: `src/ai_dev_system/vcs/__init__.py` (empty), `src/ai_dev_system/vcs/github_pr.py`
- Modify: `src/ai_dev_system/webui.py` (`_accept_branch_create_pr` delegates)
- Test: `tests/unit/vcs/test_github_pr.py` (create dir + `__init__.py`)

**Interfaces:**
- Produces: `create_pr(repo: str, branch: str, base: str, title: str, body: str = "", *, runner=None) -> dict` returning `{"ok","pr_url","pushed","error"}`. `runner(argv, cwd) -> subprocess.CompletedProcess`-like (has `.returncode`, `.stdout`, `.stderr`) is injectable for tests; defaults to a real `subprocess.run`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/vcs/__init__.py` (empty) and `tests/unit/vcs/test_github_pr.py`:

```python
from ai_dev_system.vcs.github_pr import create_pr


class _R:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def test_create_pr_push_then_pr():
    calls = []
    def runner(argv, cwd):
        calls.append(argv)
        if argv[:2] == ["git", "push"]:
            return _R(0)
        if argv[:3] == ["gh", "pr", "create"]:
            return _R(0, out="https://github.com/o/r/pull/7\n")
        return _R(1)
    res = create_pr("/repo", "ai-dev/x", "main", "title", runner=runner)
    assert res["ok"] and res["pr_url"] == "https://github.com/o/r/pull/7"
    assert calls[0][:2] == ["git", "push"]
    assert calls[1][:3] == ["gh", "pr", "create"]


def test_create_pr_existing_pr_recovers_url():
    def runner(argv, cwd):
        if argv[:2] == ["git", "push"]:
            return _R(0)
        if argv[:3] == ["gh", "pr", "create"]:
            return _R(1, err="a pull request already exists")
        if argv[:3] == ["gh", "pr", "view"]:
            return _R(0, out="https://github.com/o/r/pull/3")
        return _R(1)
    res = create_pr("/repo", "b", "main", "t", runner=runner)
    assert res["ok"] and res["pr_url"].endswith("/pull/3")


def test_create_pr_missing_branch_or_repo():
    res = create_pr("", "", "main", "t", runner=lambda *a, **k: _R(0))
    assert not res["ok"] and "branch" in res["error"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/vcs/test_github_pr.py -v`
Expected: FAIL (`ModuleNotFoundError: ai_dev_system.vcs.github_pr`).

- [ ] **Step 3: Implement `create_pr` (move + parametrize the webui logic)**

Create `src/ai_dev_system/vcs/__init__.py` (empty) and `src/ai_dev_system/vcs/github_pr.py`:

```python
"""Push a branch and open (or recover) a GitHub PR via git + gh. Never raises —
git/gh failures are returned in the result dict so callers can fall back."""
from __future__ import annotations

import subprocess


def _default_runner(argv, cwd):
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def create_pr(repo: str, branch: str, base: str, title: str, body: str = "",
              *, runner=None) -> dict:
    runner = runner or _default_runner
    result: dict = {"ok": False, "pr_url": None, "pushed": False, "error": None}
    if not (branch and repo):
        result["error"] = "thiếu branch hoặc repo path"
        return result

    try:
        push = runner(["git", "push", "-u", "origin", branch], repo)
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"git push lỗi: {exc}"
        return result
    if push.returncode != 0:
        detail = (push.stderr or push.stdout or "").strip()
        result["error"] = f"git push thất bại: {detail[:500]}"
        return result
    result["pushed"] = True

    pr_title = (title or branch)[:120]
    pr_body = body or (
        "Tạo tự động bởi ai-dev single-task executor.\n\n"
        f"Branch: {branch}\nReview diff trước khi merge."
    )
    try:
        pr = runner(["gh", "pr", "create", "--base", base, "--head", branch,
                     "--title", pr_title, "--body", pr_body], repo)
    except FileNotFoundError:
        result["error"] = "gh CLI không tìm thấy (branch đã được push lên origin)"
        return result
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"gh pr create lỗi: {exc} (branch đã được push)"
        return result

    if pr.returncode != 0:
        err = (pr.stderr or pr.stdout or "").strip()
        try:
            existing = runner(["gh", "pr", "view", branch, "--json", "url", "-q", ".url"], repo)
            url = (existing.stdout or "").strip()
            if existing.returncode == 0 and url.startswith("http"):
                result["ok"] = True
                result["pr_url"] = url
                return result
        except Exception:  # noqa: BLE001
            pass
        result["error"] = f"gh pr create thất bại: {err[:500]} (branch đã được push)"
        return result

    out = (pr.stdout or "").strip()
    url = next((ln.strip() for ln in out.splitlines() if ln.strip().startswith("http")), "")
    result["ok"] = True
    result["pr_url"] = url or out
    return result
```

- [ ] **Step 4: Delegate from webui**

Replace the body of `_accept_branch_create_pr` in `webui.py` with a delegation (keep the function + its signature so existing callers/tests are unchanged):

```python
def _accept_branch_create_pr(branch, base, repo, title, body_text=""):
    from ai_dev_system.vcs.github_pr import create_pr
    return create_pr(repo, branch, base, title, body_text)
```

- [ ] **Step 5: Run tests to verify pass + no webui regression**

Run: `python -m pytest tests/unit/vcs/test_github_pr.py -v`
Expected: PASS.
Run: `python -m pytest tests/ -q -k "webui or pr"`
Expected: existing webui/PR tests still pass.

- [ ] **Step 6: Bump README + commit**

```bash
git add src/ai_dev_system/vcs/__init__.py src/ai_dev_system/vcs/github_pr.py src/ai_dev_system/webui.py tests/unit/vcs/ README.md
git commit -m "refactor: extract create_pr into vcs/github_pr; webui delegates"
```

---

### Task 5: `ChatTaskStore` — file-backed pending-task state

**Files:**
- Create: `src/ai_dev_system/harness/tools/chat_task_store.py`
- Test: `tests/unit/harness/test_chat_task_store.py` (create dir + `__init__.py` if absent)

**Interfaces:**
- Produces: `ChatTaskStore(storage_root: str)` with:
  - `set_pending(surface, chat_id, *, spec_id, repo, base_branch) -> None`
  - `get_pending(surface, chat_id) -> dict | None` (keys: `spec_id, repo, base_branch, pr_url`)
  - `set_pr_url(surface, chat_id, pr_url) -> None`
  - `clear(surface, chat_id) -> None`
  - Persisted at `<storage_root>/chat_tasks/<surface>__<chat_id>.json`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/harness/__init__.py` (if absent) and `tests/unit/harness/test_chat_task_store.py`:

```python
from ai_dev_system.harness.tools.chat_task_store import ChatTaskStore


def test_set_get_clear(tmp_path):
    s = ChatTaskStore(str(tmp_path))
    assert s.get_pending("tg", "1") is None
    s.set_pending("tg", "1", spec_id="abc", repo="/repos/x", base_branch="main")
    p = s.get_pending("tg", "1")
    assert p["spec_id"] == "abc" and p["repo"] == "/repos/x" and p["base_branch"] == "main"
    assert p["pr_url"] in (None, "")
    s.set_pr_url("tg", "1", "https://github.com/o/r/pull/1")
    assert s.get_pending("tg", "1")["pr_url"].endswith("/pull/1")
    s.clear("tg", "1")
    assert s.get_pending("tg", "1") is None


def test_key_isolation(tmp_path):
    s = ChatTaskStore(str(tmp_path))
    s.set_pending("tg", "1", spec_id="a", repo="/r", base_branch="main")
    assert s.get_pending("tg", "2") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/harness/test_chat_task_store.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement the store**

Create `src/ai_dev_system/harness/tools/chat_task_store.py`:

```python
"""File-backed pending single-task state per (surface, chat_id). Lives under
storage_root (the mounted /data volume in Docker), so it survives daemon
restarts. One pending task per chat (the vertical slice)."""
from __future__ import annotations

import json
import re
from pathlib import Path


def _safe(part: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(part))


class ChatTaskStore:
    def __init__(self, storage_root: str) -> None:
        self._dir = Path(storage_root) / "chat_tasks"

    def _path(self, surface: str, chat_id: str) -> Path:
        return self._dir / f"{_safe(surface)}__{_safe(chat_id)}.json"

    def set_pending(self, surface, chat_id, *, spec_id, repo, base_branch) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path(surface, chat_id).write_text(
            json.dumps({"spec_id": spec_id, "repo": repo,
                        "base_branch": base_branch, "pr_url": None}),
            encoding="utf-8",
        )

    def get_pending(self, surface, chat_id) -> dict | None:
        p = self._path(surface, chat_id)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None

    def set_pr_url(self, surface, chat_id, pr_url) -> None:
        cur = self.get_pending(surface, chat_id) or {}
        cur["pr_url"] = pr_url
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path(surface, chat_id).write_text(json.dumps(cur), encoding="utf-8")

    def clear(self, surface, chat_id) -> None:
        p = self._path(surface, chat_id)
        if p.exists():
            p.unlink()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/harness/test_chat_task_store.py -v`
Expected: PASS.

- [ ] **Step 5: Bump README + commit**

```bash
git add src/ai_dev_system/harness/tools/chat_task_store.py tests/unit/harness/ README.md
git commit -m "feat(harness): file-backed ChatTaskStore for pending single-task state"
```

---

### Task 6: `dev_task_start` tool + repo_path resolution

**Files:**
- Modify: `src/ai_dev_system/harness/tools/dev_pipeline.py`
- Test: `tests/unit/harness/test_dev_task_tools.py`

**Interfaces:**
- Consumes: `ChatTaskStore` (Task 5); `config.telegram_bots` (Task 1).
- Produces: inside `make_dev_pipeline_tools`, new params `spawn_task_worker=None`, `spawn_executor=None`, `create_pr=None`, `make_spec_id=None`, `chat_task_store=None` (all default to real impls); a resolved `_repo_path`/`_base_branch` for this `surface`; and a `dev_task_start(task_description: str)` tool appended to the returned list.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/harness/test_dev_task_tools.py`:

```python
import asyncio
import json
from ai_dev_system.harness.tools.dev_pipeline import make_dev_pipeline_tools
from ai_dev_system.harness.tools.chat_task_store import ChatTaskStore


class _Cfg:
    def __init__(self, tmp, bots):
        self.storage_root = str(tmp)
        self.telegram_bots = bots
        self.database_url = "sqlite:///:memory:"


class _Bot:
    def __init__(self, label, repo_path="", base_branch=""):
        self.label, self.repo_path, self.base_branch = label, repo_path, base_branch


def _find(tools, name):
    for t in tools:
        if (getattr(t, "name", None) or getattr(t, "__name__", "")) == name:
            return t
    raise AssertionError(name)


def test_task_start_guard_when_no_repo(tmp_path):
    cfg = _Cfg(tmp_path, [_Bot("tg")])  # no repo_path
    tools = make_dev_pipeline_tools(
        surface="tg", chat_id="1", conn_factory=lambda: None, config=cfg,
        link_store=None, chat_task_store=ChatTaskStore(str(tmp_path)),
    )
    start = _find(tools, "dev_task_start")
    out = asyncio.run(start({"task_description": "add logout"}))
    assert "chưa gắn repo" in out["content"][0]["text"].lower() or "repo" in out["content"][0]["text"].lower()


def test_task_start_spawns_worker_and_records_pending(tmp_path):
    cfg = _Cfg(tmp_path, [_Bot("tg", repo_path="/repos/app", base_branch="main")])
    spawned = []
    store = ChatTaskStore(str(tmp_path))
    tools = make_dev_pipeline_tools(
        surface="tg", chat_id="1", conn_factory=lambda: None, config=cfg,
        link_store=None, chat_task_store=store,
        spawn_task_worker=lambda argv, **kw: spawned.append(argv),
        make_spec_id=lambda: "spec123",
    )
    start = _find(tools, "dev_task_start")
    out = asyncio.run(start({"task_description": "add logout button"}))
    # worker argv carries the bound repo + the generated spec id + the idea
    assert any(a == "--repo" for a in spawned[0])
    assert "/repos/app" in spawned[0] and "spec123" in spawned[0]
    assert "add logout button" in spawned[0]
    pending = store.get_pending("tg", "1")
    assert pending["spec_id"] == "spec123" and pending["repo"] == "/repos/app"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/harness/test_dev_task_tools.py -v`
Expected: FAIL (`make_dev_pipeline_tools() got an unexpected keyword 'chat_task_store'` / no `dev_task_start`).

- [ ] **Step 3: Extend the factory + add the tool**

In `dev_pipeline.py`, extend `make_dev_pipeline_tools` signature with the new optional params and resolve repo binding + defaults at the top of the function body:

```python
def make_dev_pipeline_tools(
    *,
    surface: str,
    chat_id: str,
    conn_factory,
    config,
    link_store,
    spawn_start=None,
    spawn_phase_b=None,
    spawn_task_worker=None,
    spawn_executor=None,
    create_pr=None,
    make_spec_id=None,
    chat_task_store=None,
) -> list:
    _spawn = spawn_start if spawn_start is not None else _real_spawn
    _spawn_pb = spawn_phase_b if spawn_phase_b is not None else _real_spawn
    _spawn_worker = spawn_task_worker if spawn_task_worker is not None else _real_spawn
    _spawn_exec = spawn_executor if spawn_executor is not None else _real_spawn

    if create_pr is None:
        from ai_dev_system.vcs.github_pr import create_pr as create_pr  # noqa: PLW0127
    if make_spec_id is None:
        import uuid
        def make_spec_id():  # noqa: E306
            return uuid.uuid4().hex
    if chat_task_store is None:
        from ai_dev_system.harness.tools.chat_task_store import ChatTaskStore
        chat_task_store = ChatTaskStore(config.storage_root)

    # Resolve this chat's bound repo (match surface == bot.label)
    _repo_path = ""
    _base_branch = ""
    for _b in getattr(config, "telegram_bots", ()):
        if getattr(_b, "label", None) == surface:
            _repo_path = getattr(_b, "repo_path", "") or ""
            _base_branch = getattr(_b, "base_branch", "") or ""
            break
```

Then, just before `return [dev_newproject_start, dev_run_status, dev_answer_gate]`, define and append the new tool:

```python
    @tool(
        "dev_task_start",
        "Start a coding task on THIS bot's bound repo (existing repo). Generates a "
        "task spec + plan; reply 'duyệt' to run it and get a PR. Only works if the bot "
        "is repo-bound.",
        {"task_description": str},
    )
    async def dev_task_start(args: dict[str, Any]) -> dict[str, Any]:
        if not _repo_path:
            return {"content": [{"type": "text", "text":
                "Bot này chưa gắn repo. Chạy `ai-dev telegram setup` và nhập đường dẫn repo."}]}
        task_description: str = args["task_description"]
        spec_id = make_spec_id()
        log_dir = Path(config.storage_root) / "ui_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        argv = [
            sys.executable, "-m", "ai_dev_system.task_graph.single_task_worker",
            "--id", spec_id, "--idea", task_description, "--repo", _repo_path,
            "--storage-root", str(config.storage_root),
            "--database-url", str(config.database_url),
        ]
        try:
            with open(log_dir / f"task_{spec_id[:8]}.log", "a", encoding="utf-8", errors="replace") as logf:
                _spawn_worker(argv, stdout=logf, stderr=subprocess.STDOUT, cwd=str(_REPO_ROOT))
        except Exception as exc:  # pragma: no cover
            return {"content": [{"type": "text", "text": f"spawn error: {exc}"}]}
        chat_task_store.set_pending(surface, chat_id, spec_id=spec_id,
                                    repo=_repo_path, base_branch=_base_branch)
        text = json.dumps({"spec_id": spec_id, "status": "spec_generating",
                           "note": "Đang tạo spec + plan. Hỏi trạng thái rồi nhắn 'duyệt' để chạy."})
        return {"content": [{"type": "text", "text": text}]}

    return [dev_newproject_start, dev_run_status, dev_answer_gate, dev_task_start]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/harness/test_dev_task_tools.py -v`
Expected: PASS.

- [ ] **Step 5: Run the broader harness/assistant tests for no regression**

Run: `python -m pytest tests/ -q -k "dev_pipeline or assistant or factory or gateway"`
Expected: PASS (the extra tool doesn't break existing tool wiring).

- [ ] **Step 6: Bump README + commit**

```bash
git add src/ai_dev_system/harness/tools/dev_pipeline.py tests/unit/harness/test_dev_task_tools.py README.md
git commit -m "feat(harness): dev_task_start tool runs single-task worker on the bot's bound repo"
```

---

### Task 7: Plan summary, approval, execution, PR over chat

**Files:**
- Modify: `src/ai_dev_system/harness/tools/dev_pipeline.py` (extend `dev_run_status` + `dev_answer_gate`)
- Test: `tests/unit/harness/test_dev_task_tools.py`

**Interfaces:**
- Consumes: `chat_task_store`, `create_pr`, `_spawn_exec`, `_repo_path`/`_base_branch` (Task 6); `plan_single_task`, `load_plan`, `approve_plan` from `ai_dev_system.task_graph.single_task_plan`; `task_specs/<spec_id>.json` (spec), `task_specs/<spec_id>-exec.json` (`{branch, base_branch, exec_status, error}`).
- Produces: extended behavior — no new public names.

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/harness/test_dev_task_tools.py`:

```python
import os


def _seed_spec(tmp_path, spec_id, repo="/repos/app"):
    d = tmp_path / "task_specs"; d.mkdir(parents=True, exist_ok=True)
    (d / f"{spec_id}.json").write_text(json.dumps({
        "status": "done", "idea": "add logout", "repo": repo,
        "task": {"title": "Add logout"}, "facets": {},
    }), encoding="utf-8")


def test_status_shows_plan_when_spec_ready(tmp_path, monkeypatch):
    cfg = _Cfg(tmp_path, [_Bot("tg", repo_path="/repos/app", base_branch="main")])
    store = ChatTaskStore(str(tmp_path))
    store.set_pending("tg", "1", spec_id="s1", repo="/repos/app", base_branch="main")
    _seed_spec(tmp_path, "s1")
    tools = make_dev_pipeline_tools(
        surface="tg", chat_id="1", conn_factory=lambda: None, config=cfg,
        link_store=None, chat_task_store=store,
    )
    status = _find(tools, "dev_run_status")
    out = asyncio.run(status({"run_id": ""}))
    txt = out["content"][0]["text"]
    assert "plan" in txt.lower() and "duyệt" in txt.lower()
    assert (tmp_path / "task_specs" / "s1-plan.json").exists()  # plan materialized


def test_approve_spawns_executor(tmp_path):
    cfg = _Cfg(tmp_path, [_Bot("tg", repo_path="/repos/app", base_branch="main")])
    store = ChatTaskStore(str(tmp_path))
    store.set_pending("tg", "1", spec_id="s2", repo="/repos/app", base_branch="main")
    _seed_spec(tmp_path, "s2")
    # pre-build the plan so approve_plan finds it
    from ai_dev_system.task_graph.single_task_plan import plan_single_task
    plan_single_task({"task": {"title": "t"}, "facets": {}}, "s2", storage_root=str(tmp_path))
    spawned = []
    tools = make_dev_pipeline_tools(
        surface="tg", chat_id="1", conn_factory=lambda: None, config=cfg,
        link_store=None, chat_task_store=store,
        spawn_executor=lambda argv, **kw: spawned.append(argv),
    )
    gate = _find(tools, "dev_answer_gate")
    out = asyncio.run(gate({"run_id": "", "text": "duyệt"}))
    assert "s2" in spawned[0] and "single_task_executor" in " ".join(spawned[0])
    assert "đang chạy" in out["content"][0]["text"].lower() or "execution" in out["content"][0]["text"].lower()


def test_status_creates_pr_when_exec_completed(tmp_path):
    cfg = _Cfg(tmp_path, [_Bot("tg", repo_path="/repos/app", base_branch="main")])
    store = ChatTaskStore(str(tmp_path))
    store.set_pending("tg", "1", spec_id="s3", repo="/repos/app", base_branch="main")
    _seed_spec(tmp_path, "s3")
    d = tmp_path / "task_specs"
    (d / "s3-exec.json").write_text(json.dumps({
        "branch": "ai-dev/s3", "base_branch": "main", "exec_status": "COMPLETED",
    }), encoding="utf-8")
    pr_calls = []
    def fake_create_pr(repo, branch, base, title, body="", **kw):
        pr_calls.append((repo, branch, base))
        return {"ok": True, "pr_url": "https://github.com/o/r/pull/9", "pushed": True, "error": None}
    tools = make_dev_pipeline_tools(
        surface="tg", chat_id="1", conn_factory=lambda: None, config=cfg,
        link_store=None, chat_task_store=store, create_pr=fake_create_pr,
    )
    status = _find(tools, "dev_run_status")
    out = asyncio.run(status({"run_id": ""}))
    assert pr_calls and pr_calls[0][1] == "ai-dev/s3"
    assert "pull/9" in out["content"][0]["text"]
    assert store.get_pending("tg", "1")["pr_url"].endswith("/pull/9")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/harness/test_dev_task_tools.py -k "plan or approve or completed" -v`
Expected: FAIL (current `dev_run_status`/`dev_answer_gate` ignore pending chat tasks).

- [ ] **Step 3: Extend `dev_run_status`**

At the **start** of `dev_run_status` (before the existing run_id resolution), handle a pending chat task. Insert after the function's first line:

```python
        # Repo-bound single-task flow takes priority when a task is pending for this chat.
        pending = chat_task_store.get_pending(surface, chat_id)
        if pending and not (args.get("run_id") or "").strip():
            from pathlib import Path as _P
            from ai_dev_system.task_graph.single_task_plan import (
                plan_single_task, load_plan,
            )
            sr = str(config.storage_root)
            spec_id = pending["spec_id"]
            specs = _P(sr) / "task_specs"
            exec_path = specs / f"{spec_id}-exec.json"
            spec_path = specs / f"{spec_id}.json"

            # 1. Execution finished? create the PR (once) and report it.
            if exec_path.exists():
                ex = json.loads(exec_path.read_text(encoding="utf-8"))
                if ex.get("exec_status") == "COMPLETED":
                    if not pending.get("pr_url"):
                        res = create_pr(
                            pending["repo"], ex.get("branch", ""),
                            ex.get("base_branch") or pending.get("base_branch") or "main",
                            f"ai-dev: {spec_id[:8]}",
                        )
                        if res.get("ok") and res.get("pr_url"):
                            chat_task_store.set_pr_url(surface, chat_id, res["pr_url"])
                            return {"content": [{"type": "text", "text":
                                f"✅ PR: {res['pr_url']}"}]}
                        return {"content": [{"type": "text", "text":
                            f"Execution xong nhưng tạo PR lỗi: {res.get('error')}"}]}
                    return {"content": [{"type": "text", "text":
                        f"✅ PR: {pending['pr_url']}"}]}
                if ex.get("exec_status") in ("FAILED", "ABORTED"):
                    return {"content": [{"type": "text", "text":
                        f"❌ Execution {ex.get('exec_status')}: {ex.get('error','')[:300]}"}]}
                return {"content": [{"type": "text", "text": "⏳ Đang chạy execution..."}]}

            # 2. Spec ready? materialize + summarize the plan, await approval.
            if spec_path.exists():
                spec = json.loads(spec_path.read_text(encoding="utf-8"))
                plan = load_plan(sr, spec_id) or plan_single_task(spec, spec_id, storage_root=sr)
                steps = (plan.get("graph") or {}).get("tasks") or plan.get("graph") or []
                n = len(steps) if isinstance(steps, list) else 0
                return {"content": [{"type": "text", "text":
                    f"📋 Plan sẵn sàng ({n} bước). Nhắn 'duyệt' để chạy."}]}

            return {"content": [{"type": "text", "text": "⏳ Đang tạo spec + plan..."}]}
```

(The existing run-based logic stays below, unchanged, for new-project runs.)

- [ ] **Step 4: Extend `dev_answer_gate`**

At the **start** of `dev_answer_gate` (before run_id resolution), handle approval of a pending chat task:

```python
        pending = chat_task_store.get_pending(surface, chat_id)
        if pending and not (args.get("run_id") or "").strip():
            text = args.get("text", "")
            if _G2_APPROVE_RE.search(text) and not _G2_REJECT_RE.search(text):
                from ai_dev_system.task_graph.single_task_plan import approve_plan
                sr = str(config.storage_root)
                spec_id = pending["spec_id"]
                if not approve_plan(sr, spec_id):
                    return {"content": [{"type": "text", "text":
                        "Chưa có plan để duyệt — hỏi trạng thái trước."}]}
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
            if _G2_REJECT_RE.search(text) and not _G2_APPROVE_RE.search(text):
                chat_task_store.clear(surface, chat_id)
                return {"content": [{"type": "text", "text": "Đã huỷ task."}]}
            return {"content": [{"type": "text", "text":
                "Nhắn 'duyệt' để chạy task, hoặc 'từ chối' để huỷ."}]}
```

Note: `_G2_APPROVE_RE`/`_G2_REJECT_RE` are defined inside the dev_answer_gate scope today — move their definitions to just inside `make_dev_pipeline_tools` (module-level within the factory) so both the new pending-task block and the existing Gate-2 block can use them. (Cut the two `re.compile(...)` assignments from inside `dev_answer_gate` and paste them above the `@tool` definitions; behavior identical.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/unit/harness/test_dev_task_tools.py -v`
Expected: PASS (all task-flow tests).

- [ ] **Step 6: Full suite — no regression**

Run: `python -m pytest tests/ -q`
Expected: 0 failed.

- [ ] **Step 7: Bump README + commit**

```bash
git add src/ai_dev_system/harness/tools/dev_pipeline.py tests/unit/harness/test_dev_task_tools.py README.md
git commit -m "feat(harness): plan summary, chat approval, executor run, and PR reply for repo-bound tasks"
```

---

### Task 8: Manual smoke (real git repo + gh + Docker)

**Goal:** end-to-end on a real GitHub repo. Manual — not pytest.

- [ ] **Step 1: Prep**

```bash
gh auth login        # on host; produces ~/.config/gh
cp .env.example .env  # set CLAUDE_AUTH_DIR, GH_CONFIG_DIR, GIT_AUTHOR_*
python -m ai_dev_system.cli.main telegram setup   # bind a throwaway GitHub git repo
```
Verify `.env` bot has `repo_path: /repos/<label>` and `docker-compose.override.yml` has the mount.

- [ ] **Step 2: Build + run**

Run: `docker-compose up -d --build`
Run: `docker compose exec gateway sh -lc "git --version && gh --version && gh auth status"`
Expected: all present; gh authed.

- [ ] **Step 3: Drive the loop over Telegram**

Message the bot: `thêm một dòng comment vào README`. Then ask status until "📋 Plan sẵn sàng", reply `duyệt`, ask status until `✅ PR: <url>`.
Expected: a real PR appears on GitHub on branch `ai-dev/<spec_id>`.

- [ ] **Step 4: Teardown**

Run: `docker-compose down`. Close/delete the throwaway PR/branch.

---

## Self-Review

**Spec coverage:**
- Config repo_path/base_branch → Task 1 ✓
- Wizard capture + override mount → Task 2 ✓
- Image git+gh + auth + git-ready startup → Task 3 ✓
- create_pr extraction → Task 4 ✓
- Pending-task state → Task 5 (ChatTaskStore; refines the spec's "session state" wording to a file-backed store under storage_root — persists across restart on the /data volume) ✓
- dev_task_start + repo resolution → Task 6 ✓
- Plan gate + execution + PR over chat → Task 7 ✓
- One gate (plan) + auto-PR → Task 7 (approve → executor; status COMPLETED → create_pr) ✓
- Error handling (not repo-bound, exec FAILED, PR error) → Tasks 6/7 ✓
- Smoke → Task 8 ✓

**Placeholder scan:** none — every code/ test step has concrete content; argv and file paths are exact.

**Type consistency:**
- `create_pr(repo, branch, base, title, body="", *, runner=None) -> dict{ok,pr_url,pushed,error}` — defined Task 4, called Task 7 with positional `(repo, branch, base, title)` ✓.
- `ChatTaskStore.set_pending(surface, chat_id, *, spec_id, repo, base_branch)` / `get_pending → {spec_id,repo,base_branch,pr_url}` / `set_pr_url` / `clear` — defined Task 5, used Tasks 6/7 consistently ✓.
- `make_dev_pipeline_tools(... spawn_task_worker, spawn_executor, create_pr, make_spec_id, chat_task_store)` — added Task 6, reused Task 7 ✓.
- `plan_single_task(spec, spec_id, *, storage_root)`, `load_plan(storage_root, spec_id)`, `approve_plan(storage_root, spec_id)`, `branch_name_for(spec_id)` — real existing signatures ✓.
- worker argv `--id --idea --repo --storage-root --database-url`; executor argv `--id --storage-root --database-url`; exec-status keys `branch,base_branch,exec_status,error` — verified against source ✓.

**Refinement noted (vs spec):** pending state uses a file-backed `ChatTaskStore` under `storage_root` rather than `SessionStore.set_status` (avoids overloading the assistant's session-status field; still restart-durable via the /data volume).
