# Hermes + Harness MVP — Plan 3: Telegram + Gateway Daemon — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the operator talk to the Plan-2 `Assistant` over **Telegram**: a long-poll gateway daemon routes each chat message to an `Assistant` keyed by `(surface, chat_id)` and sends the reply back — reachable from a phone, single operator, stdlib-only.

**Architecture:** A shared `AssistantFactory` lifts the hardcoded `("local","cli")` session out of `build_assistant`, so one shared set of harness/memory/session/budget pieces serves many chats (only `session_id` varies per chat — everything per-chat lives in SQLite). A stdlib-urllib Telegram client (`getUpdates` long-poll + `sendMessage`) backs a `TelegramAdapter` (with its own chat-id allowlist). A single-threaded `GatewayDaemon` polls enabled platforms, dispatches inbound → `Assistant.respond` → reply, with per-message error isolation and the Plan-2 clean-shutdown/resume lifecycle. The Plan-1 harness and Plan-2 `Assistant`/stores are reused **unchanged** except one additive `SessionStore` method.

**Tech Stack:** Python ≥3.11, **stdlib only** (`urllib.request`, `urllib.parse`, `json`, `socket`, `threading`, `signal`) — **no new dependency**. `pytest`, fakes (injectable transport + iteration cap), no network in tests.

## Plan sequence

**Plan 3 of 7** (see Plan 1 header). Plans 1 (harness+REPL) and 2 (memory+sessions+budget) are on master. **Deferred to Plan 5: the notifier / `RunStatusWatcher`** — Plan 3 is *reply-only* (inbound → outbound reply); it pushes nothing unsolicited because there is no execution run to watch yet. Plan 4 = single-task spec→plan→exec; Plan 5 = new-project intake+gates + reactive push.

Spec: [`docs/superpowers/specs/2026-06-29-hermes-harness-internal-mvp-design.md`](../specs/2026-06-29-hermes-harness-internal-mvp-design.md).

## Key design decisions (resolved; flag for review)

1. **Multi-session via a stateless `AssistantFactory`** — shared runtime/memory/session/budget; `for_chat(surface, chat_id)` varies only `session_id`. `build_assistant` becomes a thin shim `= build_assistant_factory(model).for_chat("local","cli")` so Plan-2 tests stay green.
2. **Single-threaded daemon** (one platform now). The `Platform.poll(timeout_s)` contract stays thread-ready so a future Discord platform can move to a per-platform-thread shape without an interface change.
3. **Empty allowlist = DENY-ALL** (security default for an exposed bot token). The bot ignores everyone until `AI_DEV_TELEGRAM_ALLOWED_CHAT_IDS` is set. Bootstrap: read your numeric `chat.id` from `getUpdates`/@userinfobot, then set the env.
4. **stdlib only, injectable transport** — no httpx/requests. The Telegram HTTP call goes through a `transport` callable so tests never hit the network.
5. **Offset in-memory** (reset on restart; Telegram replays unconfirmed updates ~24h; handlers idempotent enough for a single operator). **Raw text replies** (no `parse_mode`) to avoid MarkdownV2 escaping. **Resume window 60 min**.

## Global Constraints

- **Python ≥ 3.11**; source under `src/ai_dev_system/`, tests under `tests/unit/`. **No new dependency.**
- **Reuse unchanged:** the Plan-1 harness (`harness/`), and Plan-2 `Assistant`, `MemoryStore`, `BudgetTracker`. The ONLY change to Plan-2 code is an **additive** `SessionStore.mark_recent_resume_pending` (no migration — `resume_pending` is already allowed by the v7 CHECK).
- **DB** via `db.connection.get_connection` + `db.migrator.apply_schema`; SQLite `?` placeholders; `row_factory=Row`. Timestamps via SQLite `datetime('now')` (UTC).
- **Telegram API:** `https://api.telegram.org/bot<TOKEN>/<method>`; every response is `{"ok": bool, ...}`. `getUpdates` ack = next call with `offset = last update_id + 1`. `sendMessage` text limit **4096** chars (hard-split). Reply target is `chat.id` (not `from.id`). chat/from ids can exceed 32 bits — keep as Python `int`.
- **Env keys** (AI_DEV_ prefix): `AI_DEV_TELEGRAM_TOKEN`, `AI_DEV_TELEGRAM_ALLOWED_CHAT_IDS` (comma/space-separated numeric ids). `config.py` autoloads `~/.ai-dev-system/.env` then project `.env`.
- **Windows lifecycle:** register signals only on the main thread; `SIGINT` always, `SIGTERM`/`SIGBREAK` guarded via `getattr(signal, name, None)` + try/except; catch `KeyboardInterrupt` so CTRL+C reaches the `finally` that writes the clean-shutdown marker.
- **UTF-8 stdout** in the CLI command (Vietnamese replies on Windows cp1252) — same `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` as `assistant_cmd`.
- **Keep the suite green (recurring chore):** every task adding tests bumps the README test-count number (checked by `test_docs_reconciliation.py`); a new source package is added to that file's `EXPECTED_PACKAGES`. The new package `gateway.platforms` is added in Task 6. README count at plan start: **1668**.
- Tests get a DB via `conn` (in-memory) or `file_db_url` (file-backed) fixtures; isolate env with `monkeypatch.setenv/delenv`.

---

### Task 1: AssistantFactory + build_assistant_factory

**Files:**
- Create: `src/ai_dev_system/assistant/factory.py`
- Test: `tests/unit/assistant/test_factory.py`

**Interfaces:**
- Consumes: `Assistant` (agent.py), `SessionStore`, `MemoryStore`/`assistant_home`, `BudgetTracker`, `ToolRegistry`/`now_tool`/`make_memory_tool`, `make_permission_callback`, `SdkAgentRuntime`, `Config`, `get_connection`/`apply_schema`.
- Produces (consumed by Tasks 2, 8, 10):
  - `_SYSTEM_PROMPT` (moved here from `cli/commands/assistant.py`).
  - `class AssistantFactory(*, runtime, memory_store, session_store, budget, base_prompt, cap_usd=None, window=10)` with `for_chat(surface: str, chat_id: str) -> Assistant` (calls `session_store.load_or_create(surface, chat_id)`, varies only `session_id`). Stateless (no cache).
  - `build_assistant_factory(model: str | None) -> AssistantFactory` (builds all shared pieces once; runs `apply_schema` once, closing the init connection).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/assistant/test_factory.py`:

```python
def test_for_chat_varies_session_id_and_shares_tools(conn, tmp_path, monkeypatch):
    monkeypatch.setenv("AI_DEV_ASSISTANT_HOME", str(tmp_path / "home"))
    from ai_dev_system.assistant.session import SessionStore
    from ai_dev_system.assistant.memory import MemoryStore
    from ai_dev_system.assistant.budget import BudgetTracker
    from ai_dev_system.assistant.factory import AssistantFactory
    from ai_dev_system.harness.tools.registry import ToolRegistry
    from ai_dev_system.harness.tools.builtin import now_tool
    from ai_dev_system.harness.tools.memory_tool import make_memory_tool
    from ai_dev_system.harness.permissions import make_permission_callback
    from ai_dev_system.harness.runtime import SdkAgentRuntime

    store = MemoryStore(tmp_path / "home")
    reg = ToolRegistry()
    reg.register(now_tool, "now")
    reg.register(make_memory_tool(store), "memory")
    runtime = SdkAgentRuntime(registry=reg, permission_callback=make_permission_callback(), model=None)
    factory = AssistantFactory(
        runtime=runtime, memory_store=store, session_store=SessionStore(lambda: conn),
        budget=BudgetTracker(lambda: conn), base_prompt="BASE",
    )
    a1 = factory.for_chat("telegram", "111")
    a2 = factory.for_chat("telegram", "222")
    a1b = factory.for_chat("telegram", "111")
    assert a1._session_id != a2._session_id          # different chat -> different session
    assert a1._session_id == a1b._session_id          # same chat -> stable session
    assert a1._runtime is runtime                      # runtime shared
    assert reg.allowed_tool_names() == ["mcp__ai_dev__now", "mcp__ai_dev__memory"]


def test_build_assistant_factory_returns_factory(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_DEV_ASSISTANT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'ctl.db'}")
    from ai_dev_system.assistant.factory import build_assistant_factory, AssistantFactory
    f = build_assistant_factory(model=None)
    assert isinstance(f, AssistantFactory)
    asst = f.for_chat("local", "cli")
    assert asst._runtime._registry.allowed_tool_names() == ["mcp__ai_dev__now", "mcp__ai_dev__memory"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/assistant/test_factory.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: No module named 'ai_dev_system.assistant.factory'`.

- [ ] **Step 3: Implement the factory**

Create `src/ai_dev_system/assistant/factory.py`:

```python
"""AssistantFactory — builds the shared harness/memory/session/budget pieces once
and hands out a per-(surface, chat_id) Assistant (varying only session_id). Lets a
long-lived gateway daemon serve many chats from one set of shared objects."""
from __future__ import annotations

import os

_SYSTEM_PROMPT = (
    "You are ai-dev's internal assistant. You own your tool-use loop. "
    "Use the 'now' tool for the current time. Use the 'memory' tool to durably "
    "record facts about yourself (MEMORY) or the operator (USER) when worth remembering."
)


class AssistantFactory:
    def __init__(self, *, runtime, memory_store, session_store, budget,
                 base_prompt: str, cap_usd: float | None = None, window: int = 10) -> None:
        self._runtime = runtime
        self._memory_store = memory_store
        self._session_store = session_store
        self._budget = budget
        self._base_prompt = base_prompt
        self._cap_usd = cap_usd
        self._window = window

    def for_chat(self, surface: str, chat_id: str):
        from ai_dev_system.assistant.agent import Assistant
        session_id = self._session_store.load_or_create(surface, chat_id)
        return Assistant(
            runtime=self._runtime, memory_store=self._memory_store,
            session_store=self._session_store, budget=self._budget,
            base_prompt=self._base_prompt, session_id=session_id,
            window=self._window, cap_usd=self._cap_usd,
        )


def build_assistant_factory(model: str | None) -> AssistantFactory:
    from ai_dev_system.config import Config
    from ai_dev_system.db.connection import get_connection
    from ai_dev_system.db.migrator import apply_schema
    from ai_dev_system.harness.tools.registry import ToolRegistry
    from ai_dev_system.harness.tools.builtin import now_tool
    from ai_dev_system.harness.tools.memory_tool import make_memory_tool
    from ai_dev_system.harness.permissions import make_permission_callback
    from ai_dev_system.harness.runtime import SdkAgentRuntime
    from ai_dev_system.assistant.memory import MemoryStore, assistant_home
    from ai_dev_system.assistant.session import SessionStore
    from ai_dev_system.assistant.budget import BudgetTracker

    cfg = Config.from_env()
    _init = get_connection(cfg.database_url)
    try:
        apply_schema(_init)
    finally:
        _init.close()

    def conn_factory():
        return get_connection(cfg.database_url)

    store = MemoryStore(assistant_home())
    registry = ToolRegistry()
    registry.register(now_tool, "now")
    registry.register(make_memory_tool(store), "memory")
    runtime = SdkAgentRuntime(
        registry=registry, permission_callback=make_permission_callback(), model=model,
    )
    cap = os.environ.get("AI_DEV_ASSISTANT_BUDGET_USD")
    return AssistantFactory(
        runtime=runtime, memory_store=store, session_store=SessionStore(conn_factory),
        budget=BudgetTracker(conn_factory), base_prompt=_SYSTEM_PROMPT,
        cap_usd=float(cap) if cap else None,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/assistant/test_factory.py -q -p no:cacheprovider`
Expected: PASS (2 passed).

- [ ] **Step 5: Bump README + full suite + commit**

```bash
git add src/ai_dev_system/assistant/factory.py tests/unit/assistant/test_factory.py README.md
git commit -m "feat(assistant): AssistantFactory + build_assistant_factory (multi-session)"
```

---

### Task 2: build_assistant shim over the factory

**Files:**
- Modify: `src/ai_dev_system/cli/commands/assistant.py` (`build_assistant` → shim; drop the duplicated `_SYSTEM_PROMPT`/construction)
- Test: `tests/unit/cli/test_assistant_command.py` (existing — must stay green)

**Interfaces:**
- `build_assistant(model: str | None) -> Assistant` becomes `return build_assistant_factory(model).for_chat("local", "cli")`. `assistant_cmd` and the existing CLI test are unchanged.

- [ ] **Step 1: Run the existing CLI test (baseline)**

Run: `python -m pytest tests/unit/cli/test_assistant_command.py -q -p no:cacheprovider`
Expected: PASS (still green before the change).

- [ ] **Step 2: Replace `build_assistant` with the shim**

In `src/ai_dev_system/cli/commands/assistant.py`, replace the `build_assistant` body and remove the now-duplicated `_SYSTEM_PROMPT` constant + per-piece construction (the prompt now lives in `factory.py`). The function becomes:

```python
def build_assistant(model: str | None) -> "Assistant":
    from ai_dev_system.assistant.factory import build_assistant_factory
    return build_assistant_factory(model).for_chat("local", "cli")
```

Keep the `TYPE_CHECKING` import of `Assistant`, the `@command` decorator, and `assistant_cmd` (incl. its UTF-8 reconfigure + clean-shutdown handling) exactly as they are. Remove the module-level `_SYSTEM_PROMPT` here if it is no longer referenced (it moved to `factory.py`).

- [ ] **Step 3: Run the CLI test to verify still green**

Run: `python -m pytest tests/unit/cli/test_assistant_command.py -q -p no:cacheprovider`
Expected: PASS (the test asserts `build_assistant(None)` returns an `Assistant` with now+memory tools — still true via the shim).

- [ ] **Step 4: Full suite + commit** (no new tests → no README bump)

Run: `python -m pytest -q -p no:cacheprovider 2>&1 | tail -3` → 0 failed.

```bash
git add src/ai_dev_system/cli/commands/assistant.py
git commit -m "refactor(assistant): build_assistant becomes a thin shim over AssistantFactory"
```

---

### Task 3: SessionStore.mark_recent_resume_pending

**Files:**
- Modify: `src/ai_dev_system/assistant/session.py` (add one method)
- Test: `tests/unit/assistant/test_session.py` (add cases)

**Interfaces:**
- Produces (consumed by Task 9): `SessionStore.mark_recent_resume_pending(window_minutes: int = 60) -> int` — sets `status='resume_pending'` for rows where `status='active'` AND `updated_at >= datetime('now','-<N> minutes')`; returns the rowcount. Leaves `suspended` and stale rows untouched.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/assistant/test_session.py`:

```python
def test_mark_recent_resume_pending_flags_only_recent_active(conn):
    store = SessionStore(lambda: conn)
    # recent active -> flagged
    conn.execute("INSERT INTO assistant_sessions (session_id, surface, chat_id, status, updated_at) "
                 "VALUES ('a','telegram','1','active', datetime('now'))")
    # stale active -> NOT flagged (2h old)
    conn.execute("INSERT INTO assistant_sessions (session_id, surface, chat_id, status, updated_at) "
                 "VALUES ('b','telegram','2','active', datetime('now','-120 minutes'))")
    # recent suspended -> NOT flagged
    conn.execute("INSERT INTO assistant_sessions (session_id, surface, chat_id, status, updated_at) "
                 "VALUES ('c','telegram','3','suspended', datetime('now'))")
    conn.commit()
    n = store.mark_recent_resume_pending(window_minutes=60)
    assert n == 1
    assert store.get_status("a") == "resume_pending"
    assert store.get_status("b") == "active"
    assert store.get_status("c") == "suspended"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/unit/assistant/test_session.py::test_mark_recent_resume_pending_flags_only_recent_active -q -p no:cacheprovider`
Expected: FAIL — `AttributeError: 'SessionStore' object has no attribute 'mark_recent_resume_pending'`.

- [ ] **Step 3: Implement the method**

Add to `SessionStore` in `src/ai_dev_system/assistant/session.py`:

```python
    def mark_recent_resume_pending(self, window_minutes: int = 60) -> int:
        """Flag recently-active sessions as resume_pending (crash recovery on startup).
        Only status='active' rows updated within the window; leaves suspended/stale alone."""
        conn = self._conn_factory()
        cur = conn.execute(
            "UPDATE assistant_sessions SET status='resume_pending' "
            "WHERE status='active' AND updated_at >= datetime('now', ?)",
            (f"-{int(window_minutes)} minutes",),
        )
        conn.commit()
        return cur.rowcount
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/unit/assistant/test_session.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Bump README + full suite + commit**

```bash
git add src/ai_dev_system/assistant/session.py tests/unit/assistant/test_session.py README.md
git commit -m "feat(assistant): SessionStore.mark_recent_resume_pending (crash-recovery flag)"
```

---

### Task 4: Config — Telegram fields

**Files:**
- Modify: `src/ai_dev_system/config.py` (add two fields + parse in `from_env`)
- Test: `tests/unit/test_config_telegram.py`

**Interfaces:**
- Produces (consumed by Tasks 7, 10): `Config.telegram_token: str | None = None`, `Config.telegram_allowed_chat_ids: tuple[int, ...] = ()`. `from_env` reads `AI_DEV_TELEGRAM_TOKEN` (empty → None) and parses `AI_DEV_TELEGRAM_ALLOWED_CHAT_IDS` (comma/space-separated ints; empty → `()`).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_config_telegram.py`:

```python
import pytest
from ai_dev_system.config import Config


@pytest.fixture(autouse=True)
def _clear(monkeypatch):
    monkeypatch.delenv("AI_DEV_TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("AI_DEV_TELEGRAM_ALLOWED_CHAT_IDS", raising=False)


def test_defaults_when_unset():
    c = Config.from_env()
    assert c.telegram_token is None
    assert c.telegram_allowed_chat_ids == ()


def test_parses_token_and_ids(monkeypatch):
    monkeypatch.setenv("AI_DEV_TELEGRAM_TOKEN", "123:abc")
    monkeypatch.setenv("AI_DEV_TELEGRAM_ALLOWED_CHAT_IDS", "111, 222  333")
    c = Config.from_env()
    assert c.telegram_token == "123:abc"
    assert c.telegram_allowed_chat_ids == (111, 222, 333)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/unit/test_config_telegram.py -q -p no:cacheprovider`
Expected: FAIL — `AttributeError: ... 'telegram_token'` (field missing).

- [ ] **Step 3: Implement the fields + parsing**

In `src/ai_dev_system/config.py`: add to the `Config` dataclass (after the existing fields, all with defaults so existing `Config(...)` constructions stay valid):

```python
    telegram_token: str | None = None
    telegram_allowed_chat_ids: tuple[int, ...] = ()
```

In `from_env`, before the `return cls(...)`, parse the env (add `import re` at the top of the file if not present):

```python
    _tg_token = os.environ.get("AI_DEV_TELEGRAM_TOKEN") or None
    _tg_ids_raw = os.environ.get("AI_DEV_TELEGRAM_ALLOWED_CHAT_IDS", "")
    _tg_ids = tuple(int(x) for x in re.split(r"[,\s]+", _tg_ids_raw.strip()) if x)
```

and pass them into the `cls(...)` call:

```python
        telegram_token=_tg_token,
        telegram_allowed_chat_ids=_tg_ids,
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/unit/test_config_telegram.py -q -p no:cacheprovider`
Expected: PASS (2 passed).

- [ ] **Step 5: Bump README + full suite + commit**

```bash
git add src/ai_dev_system/config.py tests/unit/test_config_telegram.py README.md
git commit -m "feat(config): AI_DEV_TELEGRAM_TOKEN + ALLOWED_CHAT_IDS"
```

---

### Task 5: Telegram client (stdlib)

**Files:**
- Create: `src/ai_dev_system/gateway/telegram_client.py`
- Test: `tests/unit/gateway/test_telegram_client.py`

**Interfaces:**
- Produces (consumed by Task 6):
  - `_call(token, method, params, *, transport=None, poll_timeout=0) -> dict | None` — POSTs urlencoded params to the Telegram method; returns `result` on `ok=true`; returns `None` on `socket.timeout`; raises `TelegramError` on `ok=false`. `transport(url, data, timeout) -> bytes` is injectable (default = real urllib).
  - `get_updates(token, offset=None, timeout=50, *, transport=None) -> list` — passes `offset` (when not None), `timeout`, `allowed_updates=["message"]`; returns the result list (or `[]`).
  - `send_message(token, chat_id, text, *, transport=None) -> None` — hard-splits `text` into ≤4096-char chunks and calls `_call("sendMessage", ...)` per chunk.
  - `class TelegramError(Exception)`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/gateway/test_telegram_client.py`:

```python
import json
from ai_dev_system.gateway import telegram_client as tc


def _transport_returning(payloads):
    calls = []
    it = iter(payloads)

    def _t(url, data, timeout):
        calls.append((url, data, timeout))
        return json.dumps(next(it)).encode("utf-8")

    return _t, calls


def test_get_updates_passes_offset_and_returns_result():
    payload = {"ok": True, "result": [{"update_id": 5, "message": {"text": "hi"}}]}
    transport, calls = _transport_returning([payload])
    out = tc.get_updates("TOK", offset=5, timeout=10, transport=transport)
    assert out == payload["result"]
    url, data, _ = calls[0]
    assert url.endswith("/botTOK/getUpdates")
    assert b"offset=5" in data and b"timeout=10" in data


def test_send_message_splits_at_4096():
    transport, calls = _transport_returning([{"ok": True, "result": {}}, {"ok": True, "result": {}}])
    tc.send_message("TOK", 42, "x" * 5000, transport=transport)
    assert len(calls) == 2  # 5000 chars -> two chunks (4096 + 904)


def test_call_returns_none_on_timeout():
    import socket

    def _t(url, data, timeout):
        raise socket.timeout()

    assert tc._call("TOK", "getUpdates", {}, transport=_t) is None


def test_call_raises_on_not_ok():
    import pytest

    def _t(url, data, timeout):
        return json.dumps({"ok": False, "description": "Unauthorized"}).encode()

    with pytest.raises(tc.TelegramError):
        tc._call("TOK", "getUpdates", {}, transport=_t)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/unit/gateway/test_telegram_client.py -q -p no:cacheprovider`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the client**

Create `src/ai_dev_system/gateway/telegram_client.py`:

```python
"""Minimal stdlib Telegram Bot API client (getUpdates long-poll + sendMessage).
The HTTP call goes through an injectable `transport` so tests never hit the network."""
from __future__ import annotations

import json
import socket
import urllib.parse
import urllib.request
from typing import Any

_API = "https://api.telegram.org"
_MAX_LEN = 4096


class TelegramError(Exception):
    pass


def _default_transport(url: str, data: bytes, timeout: float) -> bytes:
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https host
        return resp.read()


def _call(token, method, params, *, transport=None, poll_timeout: float = 0) -> dict | None:
    transport = transport or _default_transport
    url = f"{_API}/bot{token}/{method}"
    data = urllib.parse.urlencode(params).encode("utf-8")
    # socket timeout must exceed the long-poll timeout so urlopen doesn't tear it down.
    sock_timeout = (poll_timeout + 10) if poll_timeout else 30
    try:
        raw = transport(url, data, sock_timeout)
    except socket.timeout:
        return None
    except urllib.error.HTTPError as exc:  # 4xx/5xx still carry an ok=false JSON body
        raw = exc.read()
    payload = json.loads(raw.decode("utf-8"))
    if not payload.get("ok"):
        raise TelegramError(payload.get("description", "telegram error"))
    return payload.get("result")


def get_updates(token, offset=None, timeout: int = 50, *, transport=None) -> list[Any]:
    params: dict[str, Any] = {"timeout": timeout, "allowed_updates": json.dumps(["message"])}
    if offset is not None:
        params["offset"] = offset
    result = _call(token, "getUpdates", params, transport=transport, poll_timeout=timeout)
    return result or []


def send_message(token, chat_id, text, *, transport=None) -> None:
    text = text or "(empty)"
    for i in range(0, len(text), _MAX_LEN):
        chunk = text[i:i + _MAX_LEN]
        _call(token, "sendMessage", {"chat_id": chat_id, "text": chunk}, transport=transport)
```

> Note: `import urllib.error` is pulled in transitively by `import urllib.request`; if a strict linter complains, add an explicit `import urllib.error`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/unit/gateway/test_telegram_client.py -q -p no:cacheprovider`
Expected: PASS (4 passed).

- [ ] **Step 5: Bump README + full suite + commit**

```bash
git add src/ai_dev_system/gateway/telegram_client.py tests/unit/gateway/test_telegram_client.py README.md
git commit -m "feat(gateway): stdlib Telegram client (getUpdates long-poll + sendMessage)"
```

---

### Task 6: TelegramAdapter + Platform protocol + Inbound

**Files:**
- Create: `src/ai_dev_system/gateway/base.py` (`Inbound`, `Platform` protocol)
- Create: `src/ai_dev_system/gateway/platforms/__init__.py`
- Create: `src/ai_dev_system/gateway/platforms/telegram.py`
- Modify: `tests/unit/test_docs_reconciliation.py` (add `"gateway.platforms"` to `EXPECTED_PACKAGES`)
- Test: `tests/unit/gateway/test_telegram_adapter.py`

**Interfaces:**
- `@dataclass Inbound(surface: str, chat_id: int, text: str)`.
- `class Platform(Protocol)`: `name: str`; `poll(timeout_s: int) -> list[Inbound]`; `reply(chat_id: int, text: str) -> None`.
- `class TelegramAdapter(*, token, allowed_chat_ids, transport=None, sender=None)` (implements `Platform`, `name="telegram"`): `poll` calls `get_updates(token, offset, timeout, transport)`, advances `self._offset = update_id + 1`, yields `Inbound` only for allowed text messages (drops disallowed chat / non-message / non-text); `reply` calls `sender or send_message`. `is_allowed(chat_id) -> bool` (empty allowlist → False, deny-all).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/gateway/test_telegram_adapter.py`:

```python
from ai_dev_system.gateway.base import Inbound
from ai_dev_system.gateway.platforms.telegram import TelegramAdapter


def _transport_seq(batches):
    it = iter(batches)

    def _t(url, data, timeout):
        import json
        try:
            result = next(it)
        except StopIteration:
            result = []
        return json.dumps({"ok": True, "result": result}).encode()

    return _t


def _adapter(transport, allowed=(111,)):
    sent = []
    a = TelegramAdapter(token="TOK", allowed_chat_ids=allowed, transport=transport,
                        sender=lambda token, chat_id, text, transport=None: sent.append((chat_id, text)))
    return a, sent


def test_poll_returns_inbound_for_allowed_text():
    upd = [{"update_id": 7, "message": {"chat": {"id": 111}, "from": {"id": 111}, "text": "hi"}}]
    a, _ = _adapter(_transport_seq([upd]))
    out = a.poll(timeout_s=0)
    assert out == [Inbound(surface="telegram", chat_id=111, text="hi")]


def test_poll_drops_disallowed_chat_and_nontext():
    upd = [
        {"update_id": 1, "message": {"chat": {"id": 999}, "text": "blocked"}},   # not allowed
        {"update_id": 2, "message": {"chat": {"id": 111}}},                       # no text
        {"update_id": 3, "message": {"chat": {"id": 111}, "text": "ok"}},         # allowed
    ]
    a, _ = _adapter(_transport_seq([upd]))
    out = a.poll(timeout_s=0)
    assert out == [Inbound(surface="telegram", chat_id=111, text="ok")]


def test_offset_advances_no_replay():
    upd = [{"update_id": 5, "message": {"chat": {"id": 111}, "text": "one"}}]
    a, _ = _adapter(_transport_seq([upd, []]))
    first = a.poll(timeout_s=0)
    second = a.poll(timeout_s=0)
    assert [m.text for m in first] == ["one"]
    assert second == []                 # offset advanced past update 5
    assert a._offset == 6


def test_empty_allowlist_denies_all():
    upd = [{"update_id": 1, "message": {"chat": {"id": 111}, "text": "hi"}}]
    a, _ = _adapter(_transport_seq([upd]), allowed=())
    assert a.poll(timeout_s=0) == []


def test_reply_sends():
    a, sent = _adapter(_transport_seq([]))
    a.reply(111, "pong")
    assert sent == [(111, "pong")]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/unit/gateway/test_telegram_adapter.py -q -p no:cacheprovider`
Expected: FAIL — modules not found.

- [ ] **Step 3: Implement base + adapter**

Create `src/ai_dev_system/gateway/base.py`:

```python
"""Gateway surface contracts: an Inbound message and the Platform protocol."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Inbound:
    surface: str
    chat_id: int
    text: str


class Platform(Protocol):
    name: str
    def poll(self, timeout_s: int) -> list[Inbound]: ...
    def reply(self, chat_id: int, text: str) -> None: ...
```

Create `src/ai_dev_system/gateway/platforms/__init__.py`:

```python
"""Concrete gateway platform adapters (Telegram now; Discord fast-follow)."""
```

Create `src/ai_dev_system/gateway/platforms/telegram.py`:

```python
"""Telegram surface: long-poll getUpdates -> Inbound, reply -> sendMessage.
Owns its own chat-id allowlist (empty allowlist = deny-all)."""
from __future__ import annotations

from typing import Any

from ai_dev_system.gateway.base import Inbound
from ai_dev_system.gateway import telegram_client


class TelegramAdapter:
    name = "telegram"

    def __init__(self, *, token: str, allowed_chat_ids, transport=None, sender=None) -> None:
        self._token = token
        self._allowed = set(allowed_chat_ids or ())
        self._transport = transport
        self._sender = sender or telegram_client.send_message
        self._offset: int | None = None

    def is_allowed(self, chat_id: int) -> bool:
        return chat_id in self._allowed  # empty set -> deny-all

    def poll(self, timeout_s: int) -> list[Inbound]:
        updates = telegram_client.get_updates(
            self._token, offset=self._offset, timeout=timeout_s, transport=self._transport,
        )
        inbound: list[Inbound] = []
        for upd in updates:
            uid = upd.get("update_id")
            if uid is not None:
                self._offset = uid + 1  # advance to ACK
            msg: dict[str, Any] = upd.get("message") or {}
            chat_id = (msg.get("chat") or {}).get("id")
            text = msg.get("text")
            if chat_id is None or not text:
                continue
            if not self.is_allowed(chat_id):
                continue
            inbound.append(Inbound(surface=self.name, chat_id=chat_id, text=text))
        return inbound

    def reply(self, chat_id: int, text: str) -> None:
        self._sender(self._token, chat_id, text, transport=self._transport)
```

In `tests/unit/test_docs_reconciliation.py`, add `"gateway.platforms"` to `EXPECTED_PACKAGES` (read the file for the exact set/format — it lists dotted package paths).

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/unit/gateway/test_telegram_adapter.py -q -p no:cacheprovider`
Expected: PASS (5 passed).

- [ ] **Step 5: Bump README + full suite + commit**

```bash
git add src/ai_dev_system/gateway/base.py src/ai_dev_system/gateway/platforms/__init__.py src/ai_dev_system/gateway/platforms/telegram.py tests/unit/gateway/test_telegram_adapter.py tests/unit/test_docs_reconciliation.py README.md
git commit -m "feat(gateway): Inbound/Platform + TelegramAdapter (allowlist, offset ack)"
```

---

### Task 7: PlatformRegistry

**Files:**
- Create: `src/ai_dev_system/gateway/registry.py`
- Test: `tests/unit/gateway/test_registry.py`

**Interfaces:**
- Produces (consumed by Task 10): `class PlatformRegistry(adapters: list)` with `enabled() -> bool` and `adapters() -> list`; classmethod `from_config(cfg, *, transport=None, sender=None) -> PlatformRegistry` appends a `TelegramAdapter` iff `cfg.telegram_token` is set (using `cfg.telegram_allowed_chat_ids`).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/gateway/test_registry.py`:

```python
from types import SimpleNamespace
from ai_dev_system.gateway.registry import PlatformRegistry


def _cfg(token=None, ids=()):
    return SimpleNamespace(telegram_token=token, telegram_allowed_chat_ids=ids)


def test_disabled_when_no_token():
    reg = PlatformRegistry.from_config(_cfg(token=None))
    assert reg.enabled() is False
    assert reg.adapters() == []


def test_enabled_with_token():
    reg = PlatformRegistry.from_config(_cfg(token="123:abc", ids=(111,)), transport=lambda *a: b"{}")
    assert reg.enabled() is True
    assert [a.name for a in reg.adapters()] == ["telegram"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/unit/gateway/test_registry.py -q -p no:cacheprovider`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the registry**

Create `src/ai_dev_system/gateway/registry.py`:

```python
"""Builds the set of enabled gateway platforms from Config (a platform is enabled
iff its credential is set)."""
from __future__ import annotations


class PlatformRegistry:
    def __init__(self, adapters) -> None:
        self._adapters = list(adapters)

    def enabled(self) -> bool:
        return bool(self._adapters)

    def adapters(self) -> list:
        return list(self._adapters)

    @classmethod
    def from_config(cls, cfg, *, transport=None, sender=None) -> "PlatformRegistry":
        from ai_dev_system.gateway.platforms.telegram import TelegramAdapter
        adapters = []
        if getattr(cfg, "telegram_token", None):
            adapters.append(TelegramAdapter(
                token=cfg.telegram_token,
                allowed_chat_ids=getattr(cfg, "telegram_allowed_chat_ids", ()),
                transport=transport, sender=sender,
            ))
        return cls(adapters)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/unit/gateway/test_registry.py -q -p no:cacheprovider`
Expected: PASS (2 passed).

- [ ] **Step 5: Bump README + full suite + commit**

```bash
git add src/ai_dev_system/gateway/registry.py tests/unit/gateway/test_registry.py README.md
git commit -m "feat(gateway): PlatformRegistry.from_config (token-gated enable)"
```

---

### Task 8: GatewayDaemon — routing + per-message isolation

**Files:**
- Create: `src/ai_dev_system/gateway/daemon.py`
- Test: `tests/unit/gateway/test_daemon.py`

**Interfaces:**
- Produces (consumed by Tasks 9, 10): `class GatewayDaemon(*, factory, platforms, home, sleep_fn=None, stop_event=None)` with `run(max_iterations: int | None = None) -> None`. Each iteration: for each platform, for each `Inbound` from `platform.poll(self._poll_timeout)`, dispatch (cache an `Assistant` per `(surface, chat_id)` via `factory.for_chat`), call `asst.respond(text)`, `platform.reply(chat_id, result.final_text)`; wrap each message in try/except so one error doesn't kill the loop. `poll_timeout` defaults to 30. (Lifecycle wiring added in Task 9.)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/gateway/test_daemon.py`:

```python
import threading
from ai_dev_system.harness.runtime import TurnResult
from ai_dev_system.gateway.base import Inbound
from ai_dev_system.gateway.daemon import GatewayDaemon


class _FakeAssistant:
    def __init__(self, sid):
        self._session_id = sid
    def respond(self, text):
        return TurnResult(f"echo:{text}", [], {}, None, self._session_id)


class _FakeFactory:
    def __init__(self):
        self.made = []
    def for_chat(self, surface, chat_id):
        self.made.append((surface, chat_id))
        return _FakeAssistant(f"{surface}:{chat_id}")


class _FakePlatform:
    name = "telegram"
    def __init__(self, batches):
        self._batches = list(batches)
        self.sent = []
    def poll(self, timeout_s):
        return self._batches.pop(0) if self._batches else []
    def reply(self, chat_id, text):
        self.sent.append((chat_id, text))


def _daemon(platform, tmp_path, **kw):
    return GatewayDaemon(factory=_FakeFactory(), platforms=[platform],
                         home=tmp_path, sleep_fn=lambda s: None, **kw)


def test_dispatches_and_replies(tmp_path):
    p = _FakePlatform([[Inbound("telegram", 111, "hi")]])
    _daemon(p, tmp_path).run(max_iterations=1)
    assert p.sent == [(111, "echo:hi")]


def test_caches_assistant_per_chat(tmp_path):
    p = _FakePlatform([[Inbound("telegram", 111, "a"), Inbound("telegram", 111, "b")]])
    d = _daemon(p, tmp_path)
    d.run(max_iterations=1)
    assert d._factory.made == [("telegram", "111")]   # for_chat called once for chat 111


def test_one_bad_message_does_not_kill_loop(tmp_path):
    class _Boom(_FakePlatform):
        def reply(self, chat_id, text):
            if chat_id == 1:
                raise RuntimeError("boom")
            super().reply(chat_id, text)
    p = _Boom([[Inbound("telegram", 1, "x"), Inbound("telegram", 111, "ok")]])
    _daemon(p, tmp_path).run(max_iterations=1)
    assert p.sent == [(111, "echo:ok")]   # second message still handled


def test_stop_event_ends_loop(tmp_path):
    ev = threading.Event(); ev.set()
    p = _FakePlatform([[Inbound("telegram", 111, "hi")]])
    _daemon(p, tmp_path, stop_event=ev).run(max_iterations=None)
    assert p.sent == []   # stopped before polling
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/unit/gateway/test_daemon.py -q -p no:cacheprovider`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the daemon (routing only; lifecycle in Task 9)**

Create `src/ai_dev_system/gateway/daemon.py`:

```python
"""The gateway daemon: poll enabled platforms, route each inbound message to a
per-(surface, chat_id) Assistant, send the reply back. Single-threaded; one bad
message never kills the loop."""
from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)


class GatewayDaemon:
    def __init__(self, *, factory, platforms, home, poll_timeout: int = 30,
                 sleep_fn=None, stop_event=None) -> None:
        self._factory = factory
        self._platforms = list(platforms)
        self._home = home
        self._poll_timeout = poll_timeout
        self._sleep = sleep_fn or (lambda s: threading.Event().wait(s))
        self._stop = stop_event or threading.Event()
        self._cache: dict[tuple[str, int], object] = {}

    def _handle(self, platform, inbound) -> None:
        key = (inbound.surface, inbound.chat_id)
        asst = self._cache.get(key)
        if asst is None:
            asst = self._factory.for_chat(inbound.surface, str(inbound.chat_id))
            self._cache[key] = asst
        result = asst.respond(inbound.text)
        platform.reply(inbound.chat_id, result.final_text)

    def run(self, max_iterations: int | None = None) -> None:
        i = 0
        while not self._stop.is_set():
            for platform in self._platforms:
                try:
                    batch = platform.poll(self._poll_timeout)
                except Exception:  # noqa: BLE001 - a poll error must not kill the daemon
                    logger.exception("gateway: poll failed for %s", getattr(platform, "name", "?"))
                    batch = []
                for inbound in batch:
                    try:
                        self._handle(platform, inbound)
                    except Exception:  # noqa: BLE001 - one bad message must not kill the loop
                        logger.exception("gateway: error handling message from %s", inbound.chat_id)
            i += 1
            if max_iterations is not None and i >= max_iterations:
                break
            if not self._stop.is_set():
                self._sleep(0)  # long-poll already blocks; no extra wait by default
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/unit/gateway/test_daemon.py -q -p no:cacheprovider`
Expected: PASS (4 passed).

- [ ] **Step 5: Bump README + full suite + commit**

```bash
git add src/ai_dev_system/gateway/daemon.py tests/unit/gateway/test_daemon.py README.md
git commit -m "feat(gateway): GatewayDaemon routing + per-message error isolation"
```

---

### Task 9: Daemon lifecycle (clean-shutdown / resume / signals)

**Files:**
- Modify: `src/ai_dev_system/gateway/daemon.py` (add lifecycle to `run` + a session_store hook + signal install)
- Test: `tests/unit/gateway/test_daemon_lifecycle.py`

**Interfaces:**
- `GatewayDaemon.__init__` also takes `session_store` (for `mark_recent_resume_pending`) — the daemon needs it for crash recovery. `run` now: on entry, if `consume_clean_shutdown(home)` is False → `session_store.mark_recent_resume_pending()`; install signal handlers on the main thread (SIGINT always; SIGTERM/SIGBREAK guarded) that set `self._stop`; in a `finally`, `mark_clean_shutdown(home)`.

> Update the Task-8 daemon tests' `_daemon` helper to pass `session_store=<a fake or SessionStore over conn>` — a `SimpleNamespace(mark_recent_resume_pending=lambda **k: 0)` fake is fine for the routing tests.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/gateway/test_daemon_lifecycle.py`:

```python
from types import SimpleNamespace
from ai_dev_system.assistant.session import mark_clean_shutdown, clean_shutdown_path
from ai_dev_system.gateway.daemon import GatewayDaemon


class _NoPlatform:
    name = "telegram"
    def poll(self, timeout_s): return []
    def reply(self, chat_id, text): pass


def _daemon(tmp_path, recorder):
    ss = SimpleNamespace(mark_recent_resume_pending=lambda **k: recorder.append("resume") or 0)
    return GatewayDaemon(factory=SimpleNamespace(for_chat=lambda *a: None),
                         platforms=[_NoPlatform()], home=tmp_path,
                         session_store=ss, sleep_fn=lambda s: None)


def test_marks_resume_when_no_clean_marker(tmp_path):
    rec = []
    _daemon(tmp_path, rec).run(max_iterations=1)
    assert rec == ["resume"]                    # crash recovery fired
    assert clean_shutdown_path(tmp_path).exists()  # marker written in finally


def test_skips_resume_when_clean_marker_present(tmp_path):
    mark_clean_shutdown(tmp_path)
    rec = []
    _daemon(tmp_path, rec).run(max_iterations=1)
    assert rec == []                            # clean prior shutdown -> no resume flagging
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/unit/gateway/test_daemon_lifecycle.py -q -p no:cacheprovider`
Expected: FAIL — `GatewayDaemon.__init__` has no `session_store` (TypeError).

- [ ] **Step 3: Add lifecycle to the daemon**

In `src/ai_dev_system/gateway/daemon.py`: add imports + extend `__init__` and `run`.

Top of file:
```python
import signal
from ai_dev_system.assistant.session import consume_clean_shutdown, mark_clean_shutdown
```

`__init__` signature: add `session_store=None` (keyword) and store `self._session_store = session_store`.

Replace `run` with:
```python
    def run(self, max_iterations: int | None = None) -> None:
        self._install_signal_handlers()
        if not consume_clean_shutdown(self._home) and self._session_store is not None:
            self._session_store.mark_recent_resume_pending()
        try:
            i = 0
            while not self._stop.is_set():
                for platform in self._platforms:
                    try:
                        batch = platform.poll(self._poll_timeout)
                    except Exception:  # noqa: BLE001
                        logger.exception("gateway: poll failed for %s", getattr(platform, "name", "?"))
                        batch = []
                    for inbound in batch:
                        try:
                            self._handle(platform, inbound)
                        except Exception:  # noqa: BLE001
                            logger.exception("gateway: error handling message from %s", inbound.chat_id)
                i += 1
                if max_iterations is not None and i >= max_iterations:
                    break
                if not self._stop.is_set():
                    self._sleep(0)
        except KeyboardInterrupt:
            self._stop.set()
        finally:
            mark_clean_shutdown(self._home)

    def _install_signal_handlers(self) -> None:
        def _stop(_signum, _frame):
            self._stop.set()
        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            sig = getattr(signal, name, None)
            if sig is None:
                continue
            try:
                signal.signal(sig, _stop)
            except (ValueError, OSError):
                pass  # not on the main thread, or unsupported on this platform
```

- [ ] **Step 4: Update the Task-8 daemon tests to pass `session_store`**

In `tests/unit/gateway/test_daemon.py`, change the `_daemon` helper to include a no-op session store:
```python
from types import SimpleNamespace
def _daemon(platform, tmp_path, **kw):
    return GatewayDaemon(factory=_FakeFactory(), platforms=[platform], home=tmp_path,
                         session_store=SimpleNamespace(mark_recent_resume_pending=lambda **k: 0),
                         sleep_fn=lambda s: None, **kw)
```

- [ ] **Step 5: Run both daemon test files to verify they pass**

Run: `python -m pytest tests/unit/gateway/test_daemon.py tests/unit/gateway/test_daemon_lifecycle.py -q -p no:cacheprovider`
Expected: PASS (6 passed).

- [ ] **Step 6: Bump README + full suite + commit**

```bash
git add src/ai_dev_system/gateway/daemon.py tests/unit/gateway/test_daemon.py tests/unit/gateway/test_daemon_lifecycle.py README.md
git commit -m "feat(gateway): daemon lifecycle (clean-shutdown/resume + signals)"
```

---

### Task 10: `ai-dev gateway` CLI command

**Files:**
- Create: `src/ai_dev_system/cli/commands/gateway.py`
- Modify: `src/ai_dev_system/cli/commands/__init__.py` (import to register)
- Test: `tests/unit/cli/test_gateway_command.py`

**Interfaces:**
- `@command(verb="gateway")` `gateway_cmd(once=False, max_iterations=0, poll_timeout=30)`: UTF-8 stdout reconfigure; `cfg = Config.from_env()`; `registry = PlatformRegistry.from_config(cfg)`; if not `registry.enabled()` → `typer.echo(...err)` + `raise typer.Exit(1)`; build `factory = build_assistant_factory(None)`; `GatewayDaemon(factory=factory, platforms=registry.adapters(), home=assistant_home(), session_store=<SessionStore over conn_factory>, poll_timeout=poll_timeout)`; `daemon.run(max_iterations=1 if once else (max_iterations or None))`; `raise typer.Exit(0)`.
- A testable `build_gateway(cfg, *, transport=None, sender=None) -> GatewayDaemon | None` helper that returns the wired daemon (or None if no platform enabled) so the command + tests share construction.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/cli/test_gateway_command.py`:

```python
import pytest


@pytest.fixture(autouse=True)
def _clear(monkeypatch, tmp_path):
    monkeypatch.delenv("AI_DEV_TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("AI_DEV_TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    monkeypatch.setenv("AI_DEV_ASSISTANT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'ctl.db'}")


def test_gateway_cmd_exits_1_when_no_platform():
    import typer
    from ai_dev_system.cli.commands.gateway import gateway_cmd
    with pytest.raises(typer.Exit) as exc:
        gateway_cmd(once=True, max_iterations=0, poll_timeout=1)
    assert exc.value.exit_code == 1


def test_gateway_command_registered():
    import ai_dev_system.cli.commands  # noqa: F401
    from ai_dev_system.cli.core.registry import get_app
    assert "gateway" in {c.name for c in get_app().registered_commands}


def test_build_gateway_returns_none_without_token():
    from ai_dev_system.config import Config
    from ai_dev_system.cli.commands.gateway import build_gateway
    assert build_gateway(Config.from_env()) is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/unit/cli/test_gateway_command.py -q -p no:cacheprovider`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the command**

Create `src/ai_dev_system/cli/commands/gateway.py`:

```python
"""ai-dev gateway — launch the chat-gateway daemon (Telegram). Reply-only in Plan 3;
proactive run-status push (the notifier) lands in Plan 5."""
from __future__ import annotations

import typer

from ai_dev_system.cli.core.registry import command


def build_gateway(cfg, *, transport=None, sender=None, poll_timeout: int = 30):
    """Wire a GatewayDaemon from config, or return None if no platform is enabled."""
    from ai_dev_system.gateway.registry import PlatformRegistry
    from ai_dev_system.gateway.daemon import GatewayDaemon
    from ai_dev_system.assistant.factory import build_assistant_factory
    from ai_dev_system.assistant.memory import assistant_home
    from ai_dev_system.assistant.session import SessionStore
    from ai_dev_system.db.connection import get_connection

    registry = PlatformRegistry.from_config(cfg, transport=transport, sender=sender)
    if not registry.enabled():
        return None
    factory = build_assistant_factory(model=None)
    return GatewayDaemon(
        factory=factory, platforms=registry.adapters(), home=assistant_home(),
        session_store=SessionStore(lambda: get_connection(cfg.database_url)),
        poll_timeout=poll_timeout,
    )


@command(verb="gateway", help="Launch the chat-gateway daemon (Telegram).")
def gateway_cmd(
    once: bool = typer.Option(False, "--once", help="Poll a single batch then exit (smoke)."),
    max_iterations: int = typer.Option(0, "--max-iterations", help="0 = run forever."),
    poll_timeout: int = typer.Option(30, "--poll-timeout", help="Telegram long-poll seconds."),
) -> None:
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    from ai_dev_system.config import Config

    daemon = build_gateway(Config.from_env(), poll_timeout=poll_timeout)
    if daemon is None:
        typer.echo("No gateway platform enabled (set AI_DEV_TELEGRAM_TOKEN).", err=True)
        raise typer.Exit(1)
    daemon.run(max_iterations=1 if once else (max_iterations or None))
    raise typer.Exit(0)
```

In `src/ai_dev_system/cli/commands/__init__.py`, add (after the `assistant` import):
```python
from ai_dev_system.cli.commands import gateway  # noqa: F401
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/unit/cli/test_gateway_command.py -q -p no:cacheprovider`
Expected: PASS (3 passed).

> If `registered_commands` doesn't expose the verb as expected, confirm with `python -c "from ai_dev_system.cli.main import app; print([c.name for c in app.registered_commands])"` and adjust the assertion (the `assistant` command verified this attribute in Plan 1).

- [ ] **Step 5: Bump README + full suite + commit**

```bash
git add src/ai_dev_system/cli/commands/gateway.py src/ai_dev_system/cli/commands/__init__.py tests/unit/cli/test_gateway_command.py README.md
git commit -m "feat(cli): ai-dev gateway launches the chat-gateway daemon"
```

---

### Task 11: End-to-end closable loop via fakes (no network)

**Files:**
- Test: `tests/unit/gateway/test_gateway_e2e.py`

**Interfaces:** none new — wires the REAL `AssistantFactory` (temp DB, recording runtime) + REAL `TelegramAdapter`/`GatewayDaemon` with a canned transport + send recorder, proving the talk-to-assistant loop end to end without network.

- [ ] **Step 1: Write the test**

Create `tests/unit/gateway/test_gateway_e2e.py`:

```python
import json
from types import SimpleNamespace

from ai_dev_system.db.connection import get_connection
from ai_dev_system.db.migrator import apply_schema
from ai_dev_system.harness.runtime import TurnResult
from ai_dev_system.assistant.memory import MemoryStore
from ai_dev_system.assistant.session import SessionStore
from ai_dev_system.assistant.budget import BudgetTracker
from ai_dev_system.assistant.factory import AssistantFactory
from ai_dev_system.gateway.platforms.telegram import TelegramAdapter
from ai_dev_system.gateway.daemon import GatewayDaemon


class _EchoRuntime:
    def run_turn(self, system_prompt, user_text):
        return TurnResult(f"reply: {user_text}", [], {"input_tokens": 1, "output_tokens": 1}, 0.0, None)


def test_telegram_to_assistant_to_reply(tmp_path, file_db_url):
    conn_factory = lambda: get_connection(file_db_url)
    factory = AssistantFactory(
        runtime=_EchoRuntime(), memory_store=MemoryStore(tmp_path / "home"),
        session_store=SessionStore(conn_factory), budget=BudgetTracker(conn_factory),
        base_prompt="BASE",
    )
    sent = []
    upd = [{"update_id": 1, "message": {"chat": {"id": 111}, "text": "hello"}}]
    transport_calls = iter([json.dumps({"ok": True, "result": upd}).encode(),
                            json.dumps({"ok": True, "result": []}).encode()])
    adapter = TelegramAdapter(
        token="TOK", allowed_chat_ids=(111,),
        transport=lambda url, data, timeout: next(transport_calls),
        sender=lambda token, chat_id, text, transport=None: sent.append((chat_id, text)),
    )
    daemon = GatewayDaemon(
        factory=factory, platforms=[adapter], home=tmp_path,
        session_store=SessionStore(conn_factory), sleep_fn=lambda s: None,
    )
    daemon.run(max_iterations=1)
    assert sent == [(111, "reply: hello")]
    # the turn was persisted in the (telegram, 111) session
    sid = SessionStore(conn_factory).load_or_create("telegram", "111")
    turns = SessionStore(conn_factory).recent(sid, 10)
    assert [(t.role, t.content) for t in turns] == [("user", "hello"), ("assistant", "reply: hello")]
```

- [ ] **Step 2: Run it (RED→GREEN are the same here — all deps exist by Task 11)**

Run: `python -m pytest tests/unit/gateway/test_gateway_e2e.py -q -p no:cacheprovider`
Expected: PASS (1 passed). If it fails, the failure pinpoints the integration gap to fix.

- [ ] **Step 3: Full suite + commit**

Bump README count, run full suite (0 failed).

```bash
git add tests/unit/gateway/test_gateway_e2e.py README.md
git commit -m "test(gateway): end-to-end Telegram->Assistant->reply via fakes (no network)"
```

---

### Task 12: Live Telegram smoke (operator-run — needs a bot token)

**Files:** none (manual verification + a note).
- Create: `docs/superpowers/plans/notes/2026-06-29-plan3-smoke.md`

This is the human-approver closable-loop confirmation; it needs a bot token only the operator can create.

- [ ] **Step 1: Create a bot + get your chat id**
  - In Telegram, message **@BotFather** → `/newbot` → copy the **token**.
  - Get your numeric **chat id**: message your new bot once, then run
    `curl "https://api.telegram.org/bot<TOKEN>/getUpdates"` and read `result[].message.chat.id` (or use **@userinfobot**).

- [ ] **Step 2: Configure** (in the project `.env` or shell env):
  ```
  AI_DEV_TELEGRAM_TOKEN=<token from BotFather>
  AI_DEV_TELEGRAM_ALLOWED_CHAT_IDS=<your numeric chat id>
  ```
  Ensure `ANTHROPIC_API_KEY` is empty (Max), as for the assistant.

- [ ] **Step 3: Run the gateway** (Windows shell where `ai-dev` is installed, or via the module):
  ```
  ai-dev gateway --once
  ```
  Send a message to your bot from Telegram *before* (or while) it polls. Expected: the assistant's reply arrives **in Telegram**. Then run without `--once` for a continuous session and confirm multi-turn + memory work over Telegram (e.g. "remember my name is X" then "what's my name?").

- [ ] **Step 4: Record + commit** the result in `docs/superpowers/plans/notes/2026-06-29-plan3-smoke.md` (reply arrived? multi-turn over Telegram? anything surprising). If it fails, capture output verbatim.

```bash
git add docs/superpowers/plans/notes/2026-06-29-plan3-smoke.md
git commit -m "docs(gateway): record Plan 3 Telegram smoke result"
```

---

## Self-Review

**Spec coverage (Plan 3 portion):** Telegram surface ✅ (Tasks 5–6); `Platform` ABC + `PlatformRegistry` ✅ (Tasks 6–7); gateway daemon routing ✅ (Task 8) + lifecycle/clean-shutdown/resume/signals ✅ (Task 9); `chat_id` allowlist (deny-all default) ✅ (Task 6); multi-session via `AssistantFactory` ✅ (Tasks 1–2); `ai-dev gateway` entry ✅ (Task 10); closable loop proven via fakes ✅ (Task 11) + live smoke ✅ (Task 12, operator-run). **Notifier/RunStatusWatcher explicitly deferred to Plan 5** (reply-only gateway — nothing to push). Harness unchanged; Plan-2 stores unchanged except additive `mark_recent_resume_pending`.

**Placeholder scan:** every code step has complete code; commands have expected output. The two "if a linter/attribute differs" notes are real fallbacks, not deferred work.

**Type consistency:** `Inbound(surface, chat_id: int, text)` consistent across base/adapter/daemon/tests; `Platform.poll(timeout_s)->list[Inbound]` / `reply(chat_id:int, text)` matched by `TelegramAdapter` and the fakes. `AssistantFactory.for_chat(surface, chat_id)->Assistant` consumed by daemon (`for_chat(surface, str(chat_id))`) and CLI shim. `build_assistant_factory(model)->AssistantFactory`; `build_assistant(model)->Assistant` (shim) keeps the Plan-2 test green. Telegram client `get_updates(token, offset, timeout, transport)` / `send_message(token, chat_id, text, transport)` matched by the adapter. `mark_recent_resume_pending(window_minutes)->int` consumed by the daemon lifecycle.

**Decision flags for the reviewer:** (1) empty allowlist = **deny-all** (first run ignores you until you set your chat id); (2) **single-threaded** daemon (one platform); (3) **stdlib-only**, injectable transport; (4) notifier **deferred to Plan 5**; (5) the daemon caches one `Assistant` per `(surface, chat_id)` keyed by the int `chat_id` while the session row uses `str(chat_id)` — intentional (cache key vs DB key); (6) offset is in-memory (restart may replay the last unacked batch within ~24h — acceptable for a single operator).
