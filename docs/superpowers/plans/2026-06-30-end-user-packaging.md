# End-User Packaging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Đóng gói AI Dev System để một dev mới clone repo, tạo Telegram bot bằng một lệnh, và chạy gateway liên tục trong Docker container.

**Architecture:** Một image Linux (Python 3.12 + Node.js + `claude` CLI) chạy `ai-dev gateway` như một long-poll daemon. Auth Claude Max được mount read-only từ Windows host (`~/.claude`). Config (Telegram bots, feature flags) nằm trong project-root `.env`, vừa được docker-compose dùng để thay biến, vừa được app đọc. Một lệnh CLI mới `ai-dev telegram setup` tự động lấy `chat_id` và ghi bot vào `.env`.

**Tech Stack:** Docker + docker-compose, Python 3.11+ (typer CLI), stdlib `urllib` (Telegram API qua `gateway/telegram_client.py`), `claude-agent-sdk` + `claude` CLI (provider claude_code).

## Global Constraints

- **Python:** `requires-python = ">=3.11"`. Docker base image: `python:3.12-slim`.
- **LLM provider mục tiêu:** `claude_code` (Claude Max, không API key). crewai KHÔNG được nằm trong core dependencies.
- **`AI_DEV_TELEGRAM_BOTS` luôn ghi single-line JSON** — python-dotenv không parse được multi-line value không quote.
- **Đường dẫn Windows trong `.env` dùng forward slash** (`C:/Users/...`) để Docker Desktop mount đúng.
- **stdout phải ép UTF-8** ở mọi command in tiếng Việt (`sys.stdout.reconfigure(encoding="utf-8", errors="replace")`) — cp1252 crash ký tự có dấu.
- **CLI command theo pattern `@command`** trong `src/ai_dev_system/cli/core/registry.py` (`noun`/`verb`), đăng ký bằng cách import module trong `cli/commands/__init__.py`.
- **Telegram client là module-level functions** `get_updates(token, offset, timeout, *, transport)` / `send_message(...)` trong `ai_dev_system.gateway.telegram_client` — KHÔNG phải method của class. `transport(url, data, timeout) -> bytes` injectable để test không chạm network.
- **Bot config dataclass:** `TelegramBotConfig(label, token, allowed_chat_ids)`; JSON trong env dùng key `chat_ids`.
- DRY, YAGNI, TDD, commit thường xuyên.

---

## File Structure

**Tạo mới:**
- `Dockerfile` — image build (Python + Node + claude CLI + package).
- `.dockerignore` — loại trừ context không cần.
- `docker-compose.yml` — service gateway + volumes.
- `.env.example` — template config có comment.
- `src/ai_dev_system/cli/telegram_setup.py` — logic thuần (env-merge, chat_id, wizard flow).
- `src/ai_dev_system/cli/commands/telegram.py` — thin `@command` wrapper.
- `tests/unit/cli/test_telegram_setup.py` — test cho logic thuần + wizard.
- `tests/unit/cli/test_gateway_schema.py` — test gateway self-heal schema.
- `GETTING-STARTED.md` — hướng dẫn end-user (tiếng Việt).
- `docs/telegram-setup.md` — hướng dẫn tạo bot thủ công.

**Sửa:**
- `pyproject.toml` — chuyển `crewai` sang optional extra `execution`.
- `src/ai_dev_system/cli/commands/gateway.py` — apply schema khi khởi động.
- `src/ai_dev_system/cli/commands/__init__.py` — import module `telegram`.

---

### Task 1: crewai → optional dependency

**Lý do:** crewai chỉ được import lazily trong `cli/run_phase_b.py:103` (hàm `_make_execution_agent`), chỉ chạy khi `LLM_PROVIDER` ∈ {anthropic, openai, azure}. Trên path `claude_code` nó không bao giờ được dùng. Môi trường dev hiện tại không cài crewai mà test suite vẫn xanh. Để image Docker gọn (crewai kéo theo litellm/chromadb...), chuyển nó sang extra.

**Files:**
- Modify: `pyproject.toml:9-25`

**Interfaces:**
- Produces: extra `execution` cài crewai; core deps không còn crewai.

- [ ] **Step 1: Sửa pyproject.toml — bỏ crewai khỏi core deps, thêm extra**

Trong `pyproject.toml`, đổi block `dependencies` và `optional-dependencies`:

```toml
dependencies = [
    # SQLite is stdlib — no DB driver dependency
    "anthropic>=0.25",
    "openai>=1.30",
    "python-dotenv>=1.0",
    "typer>=0.12",
    "rich>=13",
    "pyyaml>=6.0",
    "claude-agent-sdk>=0.1",
]

[project.scripts]
ai-dev = "ai_dev_system.cli.main:main"

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-mock>=3.12"]
execution = ["crewai>=0.51"]
```

(Chỉ xoá dòng `"crewai>=0.51",` khỏi `dependencies` và thêm dòng `execution = [...]` vào `optional-dependencies`. Giữ nguyên các block khác.)

- [ ] **Step 2: Verify crewai không còn trong core deps**

Run: `python -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); core=' '.join(d['project']['dependencies']); assert 'crewai' not in core, core; assert 'crewai' in ' '.join(d['project']['optional-dependencies']['execution']); print('OK: crewai moved to execution extra')"`
Expected: `OK: crewai moved to execution extra`

- [ ] **Step 3: Verify test suite vẫn xanh không cần crewai**

Run: `python -m pytest tests/ -q`
Expected: tất cả pass (môi trường không có crewai → chứng minh claude_code path không cần nó).

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "build: move crewai to optional [execution] extra (lean Docker image for claude_code path)"
```

---

### Task 2: Gateway self-heals DB schema on startup

**Lý do:** `get_connection` KHÔNG tự apply schema. Một container với volume `/data` trống chưa có `control.db` → gateway crash ở query đầu tiên. `apply_schema` idempotent (CREATE TABLE IF NOT EXISTS) nên gọi mỗi lần khởi động là an toàn, và còn giúp user chạy `ai-dev gateway` mà chưa `setup`.

**Files:**
- Modify: `src/ai_dev_system/cli/commands/gateway.py:64-70`
- Test: `tests/unit/cli/test_gateway_schema.py`

**Interfaces:**
- Produces: `_ensure_schema(database_url: str) -> None` trong `gateway.py`; được gọi trong `gateway_cmd` trước `build_gateway`.

- [ ] **Step 1: Viết failing test**

Tạo `tests/unit/cli/test_gateway_schema.py`:

```python
import sqlite3


def test_ensure_schema_creates_tables(tmp_path):
    from ai_dev_system.cli.commands.gateway import _ensure_schema

    db = tmp_path / "control.db"
    url = f"sqlite:///{db}"

    _ensure_schema(url)

    n = sqlite3.connect(db).execute(
        "SELECT count(*) FROM sqlite_master WHERE type='table'"
    ).fetchone()[0]
    assert n > 0
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `python -m pytest tests/unit/cli/test_gateway_schema.py -v`
Expected: FAIL — `ImportError: cannot import name '_ensure_schema'`

- [ ] **Step 3: Thêm `_ensure_schema` và gọi trong gateway_cmd**

Trong `src/ai_dev_system/cli/commands/gateway.py`, thêm hàm helper (đặt ngay sau `build_gateway`, trước `@command`):

```python
def _ensure_schema(database_url: str) -> None:
    """Apply the control-layer schema (idempotent) so a fresh DB doesn't crash the daemon."""
    from ai_dev_system.db.connection import get_connection
    from ai_dev_system.db.migrator import apply_schema

    apply_schema(get_connection(database_url))
```

Rồi sửa thân `gateway_cmd` — thay block hiện tại:

```python
    from ai_dev_system.config import Config

    daemon = build_gateway(Config.from_env(), poll_timeout=poll_timeout)
```

thành:

```python
    from ai_dev_system.config import Config

    cfg = Config.from_env()
    _ensure_schema(cfg.database_url)
    daemon = build_gateway(cfg, poll_timeout=poll_timeout)
```

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `python -m pytest tests/unit/cli/test_gateway_schema.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/cli/commands/gateway.py tests/unit/cli/test_gateway_schema.py
git commit -m "fix(gateway): apply DB schema on startup so a fresh container DB self-heals"
```

---

### Task 3: env-merge + chat_id pure helpers

**Files:**
- Create: `src/ai_dev_system/cli/telegram_setup.py`
- Test: `tests/unit/cli/test_telegram_setup.py`

**Interfaces:**
- Produces:
  - `extract_chat_id(updates: list) -> tuple[int, str] | None` — trả `(chat_id, username)` từ message đầu tiên, hoặc `None`.
  - `upsert_bot_in_env(env_text: str, label: str, token: str, chat_ids) -> str` — trả nội dung `.env` mới với bot được thêm vào `AI_DEV_TELEGRAM_BOTS` (single-line JSON). Raise `ValueError` nếu label trùng.
  - Hằng `BOTS_KEY = "AI_DEV_TELEGRAM_BOTS"`.

- [ ] **Step 1: Viết failing test**

Tạo `tests/unit/cli/test_telegram_setup.py`:

```python
import pytest

from ai_dev_system.cli import telegram_setup as ts


def test_extract_chat_id_from_message():
    updates = [
        {"update_id": 1, "message": {"chat": {"id": 5913726934},
                                     "from": {"username": "ngomi"}, "text": "hi"}}
    ]
    assert ts.extract_chat_id(updates) == (5913726934, "ngomi")


def test_extract_chat_id_returns_none_when_no_message():
    assert ts.extract_chat_id([]) is None
    assert ts.extract_chat_id([{"update_id": 1}]) is None


def test_upsert_into_empty_bots_line():
    env = "LLM_PROVIDER=claude_code\nAI_DEV_TELEGRAM_BOTS=[]\n"
    out = ts.upsert_bot_in_env(env, "my-app", "TOK", [42])
    assert 'AI_DEV_TELEGRAM_BOTS=[{"label": "my-app", "token": "TOK", "chat_ids": [42]}]' in out
    assert "LLM_PROVIDER=claude_code" in out  # other lines preserved


def test_upsert_appends_to_existing_bot():
    env = 'AI_DEV_TELEGRAM_BOTS=[{"label": "a", "token": "T1", "chat_ids": [1]}]\n'
    out = ts.upsert_bot_in_env(env, "b", "T2", [2])
    assert '"label": "a"' in out and '"label": "b"' in out
    # still one line for the key
    assert sum(1 for ln in out.splitlines() if ln.startswith("AI_DEV_TELEGRAM_BOTS=")) == 1


def test_upsert_adds_key_when_missing():
    env = "LLM_PROVIDER=claude_code\n"
    out = ts.upsert_bot_in_env(env, "a", "T1", [1])
    assert "AI_DEV_TELEGRAM_BOTS=" in out
    assert '"label": "a"' in out


def test_upsert_duplicate_label_raises():
    env = 'AI_DEV_TELEGRAM_BOTS=[{"label": "a", "token": "T1", "chat_ids": [1]}]\n'
    with pytest.raises(ValueError, match="a"):
        ts.upsert_bot_in_env(env, "a", "T2", [2])
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `python -m pytest tests/unit/cli/test_telegram_setup.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ai_dev_system.cli.telegram_setup'`

- [ ] **Step 3: Viết các helper thuần**

Tạo `src/ai_dev_system/cli/telegram_setup.py`:

```python
"""Logic for `ai-dev telegram setup`: detect chat_id via getUpdates and merge a
bot entry into AI_DEV_TELEGRAM_BOTS in a .env file. Pure helpers are network- and
IO-free so they can be unit-tested directly."""
from __future__ import annotations

import json

BOTS_KEY = "AI_DEV_TELEGRAM_BOTS"


def extract_chat_id(updates: list) -> tuple[int, str] | None:
    """Return (chat_id, username) from the first message update, or None."""
    for upd in updates:
        msg = upd.get("message") or {}
        chat = msg.get("chat") or {}
        cid = chat.get("id")
        if cid is not None:
            uname = (msg.get("from") or {}).get("username", "") or ""
            return int(cid), uname
    return None


def upsert_bot_in_env(env_text: str, label: str, token: str, chat_ids) -> str:
    """Add a bot to the AI_DEV_TELEGRAM_BOTS line (single-line JSON), preserving
    all other lines. Raise ValueError on duplicate label."""
    lines = env_text.splitlines()
    line_idx = None
    bots: list = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(f"{BOTS_KEY}=") and not stripped.startswith("#"):
            line_idx = i
            raw = stripped[len(BOTS_KEY) + 1:].strip()
            if raw:
                try:
                    bots = json.loads(raw)
                except ValueError:
                    bots = []
            break

    if any(isinstance(b, dict) and b.get("label") == label for b in bots):
        raise ValueError(f"Bot với label '{label}' đã tồn tại. Dùng tên khác.")

    bots.append({"label": label, "token": token, "chat_ids": list(chat_ids)})
    new_line = f"{BOTS_KEY}={json.dumps(bots, ensure_ascii=False)}"

    if line_idx is not None:
        lines[line_idx] = new_line
    else:
        lines.append(new_line)
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Chạy test để xác nhận PASS**

Run: `python -m pytest tests/unit/cli/test_telegram_setup.py -v`
Expected: tất cả PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/cli/telegram_setup.py tests/unit/cli/test_telegram_setup.py
git commit -m "feat(cli): telegram setup helpers — extract_chat_id + upsert_bot_in_env"
```

---

### Task 4: telegram setup wizard flow + command

**Files:**
- Modify: `src/ai_dev_system/cli/telegram_setup.py` (thêm `run_telegram_setup`)
- Create: `src/ai_dev_system/cli/commands/telegram.py`
- Modify: `src/ai_dev_system/cli/commands/__init__.py`
- Test: `tests/unit/cli/test_telegram_setup.py` (thêm test cho flow)

**Interfaces:**
- Consumes: `extract_chat_id`, `upsert_bot_in_env` (Task 3); `gateway.telegram_client.get_updates` + `TelegramError`.
- Produces: `run_telegram_setup(env_path, *, transport=None, input_fn=input, sleep_fn=..., clock=..., max_wait_s=60.0) -> int` (0=ok, 1=lỗi); command `ai-dev telegram setup --env-file .env`.

- [ ] **Step 1: Viết failing test cho flow**

Thêm vào cuối `tests/unit/cli/test_telegram_setup.py`:

```python
import json as _json
from pathlib import Path


def _transport_with_message(chat_id=42, username="ngomi"):
    payload = {
        "ok": True,
        "result": [
            {"update_id": 1, "message": {"chat": {"id": chat_id},
                                         "from": {"username": username}, "text": "hi"}}
        ],
    }

    def _t(url, data, timeout):
        return _json.dumps(payload).encode("utf-8")

    return _t


def test_run_telegram_setup_writes_bot(tmp_path):
    env = tmp_path / ".env"
    env.write_text("LLM_PROVIDER=claude_code\nAI_DEV_TELEGRAM_BOTS=[]\n")

    inputs = iter(["123:ABC", "my-app"])  # token, then project label

    rc = ts.run_telegram_setup(
        env,
        transport=_transport_with_message(chat_id=999),
        input_fn=lambda *_a, **_k: next(inputs),
        sleep_fn=lambda *_a, **_k: None,
    )

    assert rc == 0
    text = env.read_text()
    bots = _json.loads(
        next(ln for ln in text.splitlines() if ln.startswith("AI_DEV_TELEGRAM_BOTS="))
        .split("=", 1)[1]
    )
    assert bots == [{"label": "my-app", "token": "123:ABC", "chat_ids": [999]}]


def test_run_telegram_setup_bad_token(tmp_path):
    env = tmp_path / ".env"
    env.write_text("AI_DEV_TELEGRAM_BOTS=[]\n")

    def _t(url, data, timeout):
        return _json.dumps({"ok": False, "description": "Unauthorized"}).encode("utf-8")

    rc = ts.run_telegram_setup(
        env, transport=_t,
        input_fn=lambda *_a, **_k: "BADTOKEN",
        sleep_fn=lambda *_a, **_k: None,
    )
    assert rc == 1
    assert ts.BOTS_KEY in env.read_text()  # file untouched-ish, no bot added
    assert '"label"' not in env.read_text()


def test_run_telegram_setup_timeout_no_message(tmp_path):
    env = tmp_path / ".env"
    env.write_text("AI_DEV_TELEGRAM_BOTS=[]\n")

    def _t(url, data, timeout):
        return _json.dumps({"ok": True, "result": []}).encode("utf-8")  # no messages ever

    # clock advances past deadline on the 2nd reading so the loop exits fast
    ticks = iter([0.0, 0.0, 999.0, 999.0, 999.0])
    rc = ts.run_telegram_setup(
        env, transport=_t,
        input_fn=lambda *_a, **_k: "123:ABC",
        sleep_fn=lambda *_a, **_k: None,
        clock=lambda: next(ticks),
        max_wait_s=60.0,
    )
    assert rc == 1
    assert '"label"' not in env.read_text()
```

- [ ] **Step 2: Chạy test để xác nhận FAIL**

Run: `python -m pytest tests/unit/cli/test_telegram_setup.py -k run_telegram_setup -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'run_telegram_setup'`

- [ ] **Step 3: Thêm `run_telegram_setup` vào telegram_setup.py**

Thêm import ở đầu file (sau `import json`):

```python
import time
from pathlib import Path
```

Thêm hàm vào cuối `src/ai_dev_system/cli/telegram_setup.py`:

```python
def run_telegram_setup(
    env_path,
    *,
    transport=None,
    input_fn=input,
    sleep_fn=time.sleep,
    clock=time.monotonic,
    max_wait_s: float = 60.0,
) -> int:
    """Interactive wizard: validate a bot token, wait for the user's first message
    to capture chat_id, then write the bot into env_path. Returns process exit code."""
    from ai_dev_system.gateway.telegram_client import get_updates, TelegramError

    env_path = Path(env_path)

    print("\n=== Telegram bot setup ===\n")
    print("Bước 1 — Tạo bot: mở @BotFather trên Telegram, gửi /newbot, làm theo hướng dẫn.")
    token = input_fn("Nhập bot token: ").strip()
    if not token:
        print("Chưa nhập token. Huỷ.")
        return 1

    # Validate token: a bad token makes Telegram return ok:false → TelegramError.
    try:
        get_updates(token, timeout=0, transport=transport)
    except TelegramError:
        print("Token không hợp lệ. Kiểm tra lại token từ @BotFather.")
        return 1

    print("\nBước 2 — Tìm bot vừa tạo trên Telegram và nhắn cho nó BẤT KỲ tin nhắn nào.")
    print(f"⏳ Đang đợi tin nhắn (tối đa {int(max_wait_s)} giây)...")

    found = None
    offset = None
    deadline = clock() + max_wait_s
    while clock() < deadline:
        try:
            updates = get_updates(token, offset=offset, timeout=0, transport=transport)
        except TelegramError:
            updates = []
        found = extract_chat_id(updates)
        if found:
            break
        if updates:
            offset = updates[-1].get("update_id", 0) + 1
        sleep_fn(2)

    if not found:
        print("Không nhận được tin nhắn. Bot đã được tạo và bạn đã nhắn cho nó chưa? Thử lại.")
        return 1

    chat_id, uname = found
    who = f" (từ @{uname})" if uname else ""
    print(f"✅ Phát hiện: chat_id = {chat_id}{who}")

    label = input_fn("\nBước 3 — Đặt tên cho project này (VD: my-app): ").strip()
    if not label:
        print("Chưa đặt tên project. Huỷ.")
        return 1

    env_text = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    try:
        new_text = upsert_bot_in_env(env_text, label, token, [chat_id])
    except ValueError as exc:
        print(str(exc))
        return 1
    env_path.write_text(new_text, encoding="utf-8")

    print(f"\n✅ Đã thêm bot '{label}' vào {env_path}.")
    print("Chạy `docker-compose restart` (hoặc `docker-compose up -d`) để áp dụng.")
    return 0
```

- [ ] **Step 4: Tạo command wrapper**

Tạo `src/ai_dev_system/cli/commands/telegram.py`:

```python
"""ai-dev telegram setup — tạo bot Telegram cho project và ghi vào .env."""
from __future__ import annotations

import typer

from ai_dev_system.cli.core.registry import command


@command(noun="telegram", verb="setup",
         help="Tạo Telegram bot cho project (tự lấy chat_id) và ghi vào .env.")
def telegram_setup_cmd(
    env_file: str = typer.Option(".env", "--env-file", help="Đường dẫn .env để ghi bot."),
) -> None:
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    from pathlib import Path
    from ai_dev_system.cli.telegram_setup import run_telegram_setup

    rc = run_telegram_setup(Path(env_file))
    raise typer.Exit(rc)
```

- [ ] **Step 5: Đăng ký module trong commands/__init__.py**

Trong `src/ai_dev_system/cli/commands/__init__.py`, thêm dòng (sau dòng import `gateway`):

```python
from ai_dev_system.cli.commands import telegram  # noqa: F401
```

- [ ] **Step 6: Chạy test + verify command đăng ký**

Run: `python -m pytest tests/unit/cli/test_telegram_setup.py -v`
Expected: tất cả PASS

Run: `python -c "from ai_dev_system.cli.main import app; import typer; from typer.testing import CliRunner; r=CliRunner().invoke(app, ['telegram','setup','--help']); print(r.output)"`
Expected: in ra help chứa `--env-file` (xác nhận `ai-dev telegram setup` đã đăng ký).

- [ ] **Step 7: Commit**

```bash
git add src/ai_dev_system/cli/telegram_setup.py src/ai_dev_system/cli/commands/telegram.py src/ai_dev_system/cli/commands/__init__.py tests/unit/cli/test_telegram_setup.py
git commit -m "feat(cli): ai-dev telegram setup — auto-detect chat_id and write bot to .env"
```

---

### Task 5: Dockerfile + .dockerignore

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`

**Interfaces:**
- Produces: image chạy `ai-dev gateway`, có `claude` CLI trên PATH, có `skills/` + `docs/schema/` để package-data + runtime resolve.

- [ ] **Step 1: Tạo .dockerignore**

Tạo `.dockerignore` (KHÔNG loại trừ `docs/` hay `skills/` — Dockerfile cần `docs/schema` và `skills`):

```
.git
.gitignore
.pytest_cache
.eval_runs
.superpowers
.worktrees
.venv
venv
__pycache__
**/__pycache__
*.pyc
*.pyo
.env
*.db
*.db-journal
tests/
research/
references/
examples/
```

- [ ] **Step 2: Tạo Dockerfile**

Tạo `Dockerfile`:

```dockerfile
FROM python:3.12-slim

# Node.js + npm để cài `claude` CLI (provider claude_code spawn binary này).
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm \
    && npm install -g @anthropic-ai/claude-code \
    && rm -rf /var/lib/apt/lists/*

# Fail fast nếu binary claude không lên PATH.
RUN claude --version

WORKDIR /app

# Copy metadata + source + thư mục package-data (pyproject trỏ tới ../../skills,
# ../../docs/schema; runtime cũng đọc docs/schema để apply_schema).
COPY pyproject.toml ./
COPY src/ ./src/
COPY skills/ ./skills/
COPY docs/schema/ ./docs/schema/

RUN pip install --no-cache-dir -e .

RUN mkdir -p /data/storage

ENV PYTHONUNBUFFERED=1
ENV PYTHONIOENCODING=utf-8

CMD ["ai-dev", "gateway"]
```

- [ ] **Step 3: Build image để verify**

Run: `docker build -t ai-dev-system .`
Expected: build thành công; thấy dòng version của `claude` in ra ở bước `RUN claude --version`; bước `pip install` xong không lỗi.

(Yêu cầu Docker Desktop đang chạy. Nếu `claude --version` lỗi → sai tên npm package; nếu `pip install` lỗi thiếu file → kiểm tra COPY skills/docs/schema.)

- [ ] **Step 4: Verify ai-dev chạy trong image**

Run: `docker run --rm ai-dev-system ai-dev --help`
Expected: in ra cây lệnh `ai-dev` (gồm cả `telegram`, `gateway`).

- [ ] **Step 5: Commit**

```bash
git add Dockerfile .dockerignore
git commit -m "build: Dockerfile (python+node+claude CLI) and .dockerignore for gateway image"
```

---

### Task 6: docker-compose.yml + .env.example

**Files:**
- Create: `docker-compose.yml`
- Create: `.env.example`

**Interfaces:**
- Consumes: image từ Task 5; `CLAUDE_AUTH_DIR`, `AI_DEV_TELEGRAM_BOTS` từ `./.env`.
- Produces: service `gateway` với volumes auth/data/.env và env override `DATABASE_URL`/`STORAGE_ROOT` về đường dẫn container.

- [ ] **Step 1: Tạo .env.example**

Tạo `.env.example` (AI_DEV_TELEGRAM_BOTS single-line; đường dẫn forward slash):

```bash
# ── Claude Auth ───────────────────────────────────────────────────────────────
# Đường dẫn tới thư mục .claude trên máy bạn (chứa auth token Claude Max).
# Dùng forward slash cho Docker. Thường là: C:/Users/<tên của bạn>/.claude
CLAUDE_AUTH_DIR=C:/Users/yourname/.claude

# ── LLM ──────────────────────────────────────────────────────────────────────
LLM_PROVIDER=claude_code
LLM_MODEL=sonnet

# ── Telegram Bots ─────────────────────────────────────────────────────────────
# Mỗi project một bot. Chạy `ai-dev telegram setup` để thêm bot tự động,
# hoặc điền thủ công (single-line JSON):
#   [{"label": "my-app", "token": "123:ABC", "chat_ids": [5913726934]}]
AI_DEV_TELEGRAM_BOTS=[]

# ── Feature Flags ─────────────────────────────────────────────────────────────
FF_USE_INTAKE_WIZARD=true
FF_USE_DEBATE_V2=true
FF_USE_GATE1_V2=true
FF_USE_SPEC_GEN_V2=true
FF_EVAL_HARNESS_ENABLED=true
```

- [ ] **Step 2: Tạo docker-compose.yml**

Tạo `docker-compose.yml`:

```yaml
services:
  gateway:
    build: .
    container_name: ai-dev-gateway
    restart: unless-stopped
    volumes:
      # Claude Max auth — mount read-only từ Windows host.
      # ${CLAUDE_AUTH_DIR} được docker-compose đọc từ ./.env.
      - "${CLAUDE_AUTH_DIR}:/root/.claude:ro"
      # SQLite DB + storage, persist qua restart/rebuild.
      - ai-dev-data:/data
      # Config cho app đọc (Telegram bots, feature flags).
      - ./.env:/app/.env:ro
    environment:
      # Override về đường dẫn container — ghi đè Windows paths trong .env
      # (app dùng load_dotenv(override=False) nên env này thắng).
      DATABASE_URL: sqlite:////data/control.db
      STORAGE_ROOT: /data/storage
      LLM_PROVIDER: claude_code
      PYTHONIOENCODING: utf-8

volumes:
  ai-dev-data:
```

- [ ] **Step 3: Verify compose hợp lệ**

Run: `CLAUDE_AUTH_DIR=C:/tmp/.claude docker compose config`
Expected: in ra config đã render, không lỗi; thấy mount `/root/.claude:ro`, `ai-dev-data`, `/app/.env`, và `DATABASE_URL: sqlite:////data/control.db`.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml .env.example
git commit -m "build: docker-compose gateway service + .env.example template"
```

---

### Task 7: GETTING-STARTED.md + docs/telegram-setup.md

**Files:**
- Create: `GETTING-STARTED.md`
- Create: `docs/telegram-setup.md`

**Interfaces:**
- Consumes: mọi artifact ở Task 1–6 (lệnh, file).

- [ ] **Step 1: Tạo GETTING-STARTED.md**

Tạo `GETTING-STARTED.md`:

````markdown
# Bắt đầu với AI Dev System

Hướng dẫn này giúp bạn chạy AI Dev System như một bot Telegram chạy liên tục trong Docker.

## Yêu cầu

- **Docker Desktop** (Windows, bật WSL2 backend) đang chạy.
- **Claude Code CLI** đã cài và đăng nhập Claude Max trên máy.
  Kiểm tra: mở terminal, gõ `claude --version`.
- **Python 3.11+** (chỉ cần cho lệnh `ai-dev telegram setup`).
- Tài khoản **Telegram**.

## Bước 1 — Clone repo

```bash
git clone <repo-url>
cd ai-development-system
cp .env.example .env
```

## Bước 2 — Điền CLAUDE_AUTH_DIR

Mở `.env`, sửa dòng `CLAUDE_AUTH_DIR` thành đường dẫn `.claude` của bạn
(dùng forward slash):

```
CLAUDE_AUTH_DIR=C:/Users/ngomi/.claude
```

## Bước 3 — Tạo bot Telegram cho project đầu tiên

Chạy lệnh sau **trên máy host** (không phải trong container) và làm theo hướng dẫn —
lệnh sẽ tự bắt `chat_id` của bạn và ghi vào `.env`:

```bash
pip install -e .          # cài một lần để có lệnh ai-dev
ai-dev telegram setup
```

Wizard sẽ: hỏi token (từ @BotFather) → bảo bạn nhắn cho bot một tin → tự phát hiện
`chat_id` → hỏi tên project → ghi bot vào `.env`.

> Không muốn dùng wizard? Xem [docs/telegram-setup.md](docs/telegram-setup.md) để làm thủ công.

## Bước 4 — Chạy gateway

```bash
docker-compose up -d --build
docker-compose logs -f        # theo dõi log; Ctrl+C để thoát (container vẫn chạy)
```

Thấy log gateway bắt đầu polling và không có lỗi auth là OK.

## Thử ngay — project demo đầu tiên

Mở Telegram, tìm bot vừa tạo, nhắn:

```
Tôi muốn xây một web app quản lý công việc nhóm
```

Hệ thống sẽ:

1. Hỏi vài câu để hiểu rõ project (intake wizard).
2. Gửi thông báo: `🔔 Run ... tới Gate 1 — trả lời để duyệt`.
3. Bạn xem rồi nhắn `ok` / `duyệt` để tiếp tục.
4. Hệ thống sinh spec, dựng task graph, tới Gate 2, rồi thực thi và verify.

## Thêm project mới (bot mới)

```bash
ai-dev telegram setup     # chạy lại, thêm bot thứ 2 vào .env
docker-compose restart
```

## Quản lý container

```bash
docker-compose ps         # trạng thái
docker-compose logs -f    # xem log
docker-compose restart    # nạp lại .env sau khi sửa
docker-compose down       # dừng (dữ liệu /data vẫn còn trong volume)
```

## Troubleshooting

- **Bot không trả lời** → `docker-compose logs gateway` để xem lỗi.
- **`claude: command not found` trong container** → rebuild: `docker-compose build --no-cache`.
- **Lỗi auth / "not logged in"** → chạy `claude login` trên Windows host, rồi `docker-compose restart`. Kiểm tra `CLAUDE_AUTH_DIR` trỏ đúng `.claude` và dùng forward slash.
- **Bot không phản hồi đúng người** → kiểm tra `chat_ids` trong `.env` đúng ID của bạn (chạy lại `ai-dev telegram setup`).
- **Sửa `.env` nhưng không có tác dụng** → phải `docker-compose restart` (hoặc `up -d`) để nạp lại.

## Image lớn (~800MB)?

Bình thường — image gồm Python + Node.js + `claude` CLI. Đây là đánh đổi để dùng
Claude Max miễn phí trong container.
````

- [ ] **Step 2: Tạo docs/telegram-setup.md**

Tạo `docs/telegram-setup.md`:

````markdown
# Tạo Telegram bot (thủ công)

Tài liệu này mô tả cách làm thủ công. Cách nhanh hơn là chạy `ai-dev telegram setup`
(tự bắt `chat_id` và ghi `.env`).

## 1. Tạo bot qua @BotFather

1. Mở Telegram, tìm **@BotFather**.
2. Gửi `/newbot`.
3. Nhập tên hiển thị, ví dụ: `My Project Bot`.
4. Nhập username (phải kết thúc bằng `bot`), ví dụ: `my_project_123_bot`.
5. Copy token, dạng `1234567890:AAH...`.

## 2. Lấy chat_id thủ công

1. Nhắn bất kỳ tin nhắn nào tới bot vừa tạo.
2. Mở trình duyệt, thay `<TOKEN>` bằng token của bạn:
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. Tìm trường `"from": {"id": 5913726934}` — đó là `chat_id` của bạn.

## 3. Ghi vào .env (single-line JSON)

`AI_DEV_TELEGRAM_BOTS` phải nằm trên **một dòng** (python-dotenv không đọc value
nhiều dòng):

```
AI_DEV_TELEGRAM_BOTS=[{"label": "my-project", "token": "1234567890:AAH...", "chat_ids": [5913726934]}]
```

## 4. Nhiều project (nhiều bot)

Tạo bot riêng cho từng project, gộp vào cùng một mảng:

```
AI_DEV_TELEGRAM_BOTS=[{"label": "project-a", "token": "TOKEN_A", "chat_ids": [111111]}, {"label": "project-b", "token": "TOKEN_B", "chat_ids": [222222]}]
```

- `label` — tên nhận dạng project (chữ thường, gạch ngang). Cũng dùng để định tuyến
  thông báo run-status về đúng bot.
- `chat_ids` — danh sách Telegram user ID được phép dùng bot (allowlist).

Sau khi sửa `.env`, chạy `docker-compose restart`.
````

- [ ] **Step 3: Verify file tồn tại và link đúng**

Run: `python -c "import os; assert os.path.exists('GETTING-STARTED.md'); assert os.path.exists('docs/telegram-setup.md'); print('docs OK')"`
Expected: `docs OK`

- [ ] **Step 4: Commit**

```bash
git add GETTING-STARTED.md docs/telegram-setup.md
git commit -m "docs: GETTING-STARTED guide + manual telegram-setup reference"
```

---

### Task 8: Smoke test (integration verification)

**Mục tiêu:** xác nhận end-to-end thật: image build → gateway kết nối Telegram qua auth mount → bot trả lời. Đây là kiểm thử thủ công, không phải pytest.

**Files:** không tạo file mới (có thể tạo `.env` local từ template — `.env` đã gitignore).

- [ ] **Step 1: Chuẩn bị .env thật**

```bash
cp .env.example .env    # nếu chưa có
# sửa CLAUDE_AUTH_DIR cho đúng máy
ai-dev telegram setup   # thêm 1 bot thật
```

Verify: `.env` có `CLAUDE_AUTH_DIR` đúng và `AI_DEV_TELEGRAM_BOTS` chứa ít nhất 1 bot.

- [ ] **Step 2: Build + chạy**

Run: `docker-compose up -d --build`
Run: `docker-compose logs --tail 30 gateway`
Expected: log cho thấy daemon khởi động, schema applied (không crash DB), bắt đầu polling; KHÔNG có lỗi auth/`claude`.

- [ ] **Step 3: Gửi tin nhắn thật qua bot**

Trên Telegram, nhắn bot: `Tôi muốn xây một app ghi chú đơn giản`.
Expected: bot phản hồi (intake wizard hoặc câu hỏi làm rõ) trong vài giây–vài chục giây.

Nếu bot im lặng → `docker-compose logs gateway` tìm lỗi. Lỗi thường gặp:
- claude chạy dưới root bị chặn → xem ghi chú dưới.
- auth mount sai → kiểm tra `CLAUDE_AUTH_DIR`.

- [ ] **Step 4: Dọn dẹp**

Run: `docker-compose down`
Expected: container dừng; volume `ai-dev-data` vẫn còn (dữ liệu run được giữ).

- [ ] **Step 5: (Nếu gặp) claude bị chặn khi chạy dưới root**

Nếu log báo claude từ chối chạy dưới root, thêm vào `environment:` của service trong `docker-compose.yml`:

```yaml
      IS_SANDBOX: "1"
```

rồi `docker-compose up -d`. Ghi lại kết quả vào GETTING-STARTED troubleshooting nếu cần.

---

## Self-Review

**1. Spec coverage:**
- Dockerfile → Task 5 ✓ (đã sửa: COPY skills/ + docs/schema/, claude CLI verify, schema tự apply qua Task 2)
- docker-compose.yml → Task 6 ✓
- .dockerignore → Task 5 ✓
- .env.example → Task 6 ✓ (single-line bots)
- `ai-dev telegram setup` (tự lấy chat_id) → Task 3 + 4 ✓
- GETTING-STARTED.md → Task 7 ✓
- docs/telegram-setup.md → Task 7 ✓
- Risks (claude-agent-sdk, crewai, Windows path) → Task 1 (crewai), Task 5 (claude CLI verify), Task 6 (forward slash) ✓

**2. Placeholder scan:** không có TBD/TODO; mọi step có code/command cụ thể và expected output.

**3. Type consistency:**
- `extract_chat_id(updates) -> tuple[int, str] | None` — dùng nhất quán ở Task 3 (định nghĩa) và Task 4 (gọi, unpack `chat_id, uname`).
- `upsert_bot_in_env(env_text, label, token, chat_ids) -> str` — nhất quán Task 3 ↔ Task 4.
- `run_telegram_setup(env_path, *, transport, input_fn, sleep_fn, clock, max_wait_s) -> int` — test (Task 4 Step 1) khớp signature (Task 4 Step 3).
- `_ensure_schema(database_url: str) -> None` — Task 2 định nghĩa và gọi nhất quán.
- `get_updates(token, offset, timeout, *, transport)` — dùng đúng chữ ký module-level thật.
- `BOTS_KEY` / key `chat_ids` — khớp parser trong `config.py`.

**Phát hiện bổ sung từ verification (đã đưa vào plan, lệch so với spec gốc):**
1. Dockerfile COPY thêm `skills/` + `docs/schema/` (package-data ngoài `src/`).
2. crewai → optional extra (Task 1) thay vì để trong core deps.
3. Gateway tự apply schema khi khởi động (Task 2) — fresh DB không crash.
4. `AI_DEV_TELEGRAM_BOTS` single-line (dotenv không đọc multi-line).
