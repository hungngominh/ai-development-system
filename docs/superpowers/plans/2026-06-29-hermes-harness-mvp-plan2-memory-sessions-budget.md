# Hermes + Harness MVP — Plan 2: Memory + Sessions + Budget — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the owned assistant a durable conversational envelope: long-term memory (`MEMORY.md` + `USER.md` injected into the system prompt + a `memory` tool), persistent multi-turn sessions that survive restart, and per-session token/cost budget rollup — all driving the existing local REPL through a new `Assistant` orchestration layer.

**Architecture:** A new `assistant/` package sits between the `gateway` REPL and the Plan-1 `harness`. `Assistant.respond(text)` loads memory + the recent-history window from SQLite, builds the prompt, calls the unchanged `SdkAgentRuntime.run_turn(system_prompt, user_text)`, persists both turns, and records budget. Multi-turn context is achieved by **rendering the recent-history window into the user turn from SQLite each call** — fully restart-durable and bounded — rather than holding a live SDK client (in-process only). The harness from Plan 1 is **not modified**.

**Tech Stack:** Python ≥3.11, stdlib `sqlite3` (via `db.connection.get_connection` / `db.migrator.apply_schema`), `claude-agent-sdk` (Plan 1), `pytest`. Async tested via `asyncio.run`.

## Plan sequence

This is **Plan 2 of 7** (see Plan 1 header for the full roadmap). Plan 1 (owned harness + REPL) is merged to master. Plan 3 (Telegram + gateway daemon + notifier) builds on this; Plan 5 (new-project tools) reuses `SessionStore`/`run_links`.

Spec: [`docs/superpowers/specs/2026-06-29-hermes-harness-internal-mvp-design.md`](../specs/2026-06-29-hermes-harness-internal-mvp-design.md).

## Key design decisions (flag for review)

1. **Multi-turn = history rendered from SQLite into each turn, fresh client per turn.** The Plan-1 `run_turn(system_prompt, user_text)` is unchanged; the `Assistant` puts base persona + memory in the (stable, cache-friendly) `system_prompt` and prepends the recent-history window to `user_text`. Durable across restart, bounded by a window. Trade-off: the window is re-sent each turn (bounded token cost). Keeping a live `ClaudeSDKClient` for cache efficiency is a deferred optimization (Plan 3+ daemon).
2. **Home dir = `~/.ai-dev-system/assistant/`** (matches the repo's existing `~/.ai-dev-system/` convention; the spec's `~/.ai-dev/assistant/` is reconciled to this), overridable by `AI_DEV_ASSISTANT_HOME`. Holds `MEMORY.md`, `USER.md`, `.clean_shutdown`.
3. **Crash-resume is turn-level:** the transcript is durable in SQLite (that IS the resume); a `.clean_shutdown` marker + a `resume_pending` session flag let the REPL tell the operator it resumed. Full process-supervisor/daemon auto-resume is Plan 3.
4. **Budget = aggregation over `assistant_messages`** (no separate table); optional soft cap via env, off by default.

## Global Constraints

- **Python ≥ 3.11**; source under `src/ai_dev_system/`, tests under `tests/unit/`.
- **DB:** open connections only via `from ai_dev_system.db.connection import get_connection`; apply schema via `from ai_dev_system.db.migrator import apply_schema`. SQLite `?` placeholders. `row_factory` is `sqlite3.Row` (access `row["col"]`).
- **New tables go in a migration** `docs/schema/migrations/v7-assistant.sql` (idempotent `CREATE TABLE IF NOT EXISTS`), discovered automatically by `apply_schema`.
- **Atomic file writes** use the house pattern (temp file → `flush` → `os.fsync` → `os.replace`), mirroring [`rules/learning.py`](../../../src/ai_dev_system/rules/learning.py) `_atomic_write_yaml`.
- **The Plan-1 harness (`harness/runtime.py`) MUST NOT change.** Plan 2 only adds `assistant/`, a `memory` tool, a migration, and rewires `gateway`/`cli`.
- **Keep the suite green (recurring chore):** any task that adds tests must bump the README test-count number (a value `test_docs_reconciliation.py::test_readme_test_count_matches_collected_count` checks against `pytest --collect-only`); any new source package must be added to that file's `EXPECTED_PACKAGES`. The `assistant` package is new → add it in Task 2 (the first task that creates it). Current README count at plan start: **1638** (re-check with `python -m pytest --collect-only -q -p no:cacheprovider 2>&1 | tail -3` before each commit).
- Tests get a DB via the `conn` fixture (in-memory + `apply_schema`) or `file_db_url` (file-backed); see `tests/conftest.py`.

---

### Task 1: DB migration — assistant_sessions + assistant_messages

**Files:**
- Create: `docs/schema/migrations/v7-assistant.sql`
- Test: `tests/unit/db/test_assistant_schema.py`

**Interfaces:**
- Produces tables (read by Task 4/5/7):
  - `assistant_sessions(session_id TEXT PK, surface TEXT, chat_id TEXT, status TEXT, created_at, updated_at)` with `UNIQUE(surface, chat_id)`, `status ∈ {active, resume_pending, suspended}`.
  - `assistant_messages(id INTEGER PK AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT, created_at, input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL)`, `role ∈ {user, assistant}`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/db/test_assistant_schema.py`:

```python
from ai_dev_system.db.connection import get_connection
from ai_dev_system.db.migrator import apply_schema


def _tables(conn):
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r["name"] for r in rows}


def test_assistant_tables_created():
    conn = get_connection("sqlite:///:memory:")
    apply_schema(conn)
    assert "assistant_sessions" in _tables(conn)
    assert "assistant_messages" in _tables(conn)
    conn.close()


def test_assistant_session_unique_surface_chat():
    conn = get_connection("sqlite:///:memory:")
    apply_schema(conn)
    conn.execute(
        "INSERT INTO assistant_sessions (session_id, surface, chat_id, status) VALUES (?,?,?,?)",
        ("s1", "local", "cli", "active"),
    )
    import sqlite3
    try:
        conn.execute(
            "INSERT INTO assistant_sessions (session_id, surface, chat_id, status) VALUES (?,?,?,?)",
            ("s2", "local", "cli", "active"),
        )
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised, "duplicate (surface, chat_id) must violate UNIQUE"
    conn.close()


def test_assistant_message_role_check():
    conn = get_connection("sqlite:///:memory:")
    apply_schema(conn)
    conn.execute(
        "INSERT INTO assistant_sessions (session_id, surface, chat_id, status) VALUES (?,?,?,?)",
        ("s1", "local", "cli", "active"),
    )
    import sqlite3
    try:
        conn.execute(
            "INSERT INTO assistant_messages (session_id, role, content) VALUES (?,?,?)",
            ("s1", "system", "x"),
        )
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised, "role must be constrained to user/assistant"
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/db/test_assistant_schema.py -q -p no:cacheprovider`
Expected: FAIL — tables not found (`assistant_sessions` missing).

- [ ] **Step 3: Create the migration**

Create `docs/schema/migrations/v7-assistant.sql`:

```sql
-- v7-assistant.sql (SQLite)
--
-- Assistant subsystem (Hermes+harness MVP, Plan 2): persistent conversational
-- sessions + per-turn message transcript with token/cost columns for budget rollup.
--
-- Idempotent + additive. Safe to re-run.

CREATE TABLE IF NOT EXISTS assistant_sessions (
    session_id  TEXT PRIMARY KEY,
    surface     TEXT NOT NULL,
    chat_id     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'resume_pending', 'suspended')),
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (surface, chat_id)
);

CREATE TABLE IF NOT EXISTS assistant_messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT NOT NULL REFERENCES assistant_sessions(session_id),
    role          TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content       TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    cost_usd      REAL
);

CREATE INDEX IF NOT EXISTS idx_assistant_messages_session
    ON assistant_messages(session_id, id);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/db/test_assistant_schema.py -q -p no:cacheprovider`
Expected: PASS (3 passed). (No README bump yet handled here — do it in the commit step.)

- [ ] **Step 5: Bump README count + run full suite + commit**

Run: `python -m pytest --collect-only -q -p no:cacheprovider 2>&1 | tail -3`, update the count in `README.md` to the new total, then:
Run: `python -m pytest -q -p no:cacheprovider 2>&1 | tail -3` → Expected: 0 failed.

```bash
git add docs/schema/migrations/v7-assistant.sql tests/unit/db/test_assistant_schema.py README.md
git commit -m "feat(assistant): v7 migration — assistant_sessions + assistant_messages"
```

---

### Task 2: MemoryStore (MEMORY.md + USER.md)

**Files:**
- Create: `src/ai_dev_system/assistant/__init__.py`
- Create: `src/ai_dev_system/assistant/memory.py`
- Modify: `tests/unit/test_docs_reconciliation.py` (add `"assistant"` to `EXPECTED_PACKAGES`)
- Test: `tests/unit/assistant/__init__.py`, `tests/unit/assistant/test_memory.py`

**Interfaces:**
- Produces (consumed by Tasks 3, 7):
  - `assistant_home() -> Path` — `$AI_DEV_ASSISTANT_HOME` or `~/.ai-dev-system/assistant`; created on access.
  - `@dataclass Memory(agent: str, user: str)`.
  - `class MemoryStore(home: Path)`: `load() -> Memory` (empty strings if files absent); `write(target: str, action: str, text: str) -> None` where `target ∈ {"MEMORY","USER"}`, `action ∈ {"add","replace","remove"}` (`add` appends a line, `replace` overwrites, `remove` deletes a matching line). Writes atomically.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/assistant/__init__.py` (empty), and `tests/unit/assistant/test_memory.py`:

```python
from ai_dev_system.assistant.memory import MemoryStore, Memory


def test_load_empty_when_no_files(tmp_path):
    store = MemoryStore(tmp_path)
    mem = store.load()
    assert isinstance(mem, Memory)
    assert mem.agent == ""
    assert mem.user == ""


def test_add_then_load_roundtrip(tmp_path):
    store = MemoryStore(tmp_path)
    store.write("MEMORY", "add", "Prefers Vietnamese.")
    store.write("USER", "add", "Role: solo dev.")
    mem = store.load()
    assert "Prefers Vietnamese." in mem.agent
    assert "Role: solo dev." in mem.user


def test_replace_overwrites(tmp_path):
    store = MemoryStore(tmp_path)
    store.write("MEMORY", "add", "old")
    store.write("MEMORY", "replace", "new only")
    assert store.load().agent.strip() == "new only"


def test_remove_drops_matching_line(tmp_path):
    store = MemoryStore(tmp_path)
    store.write("MEMORY", "add", "keep")
    store.write("MEMORY", "add", "drop me")
    store.write("MEMORY", "remove", "drop me")
    mem = store.load()
    assert "keep" in mem.agent
    assert "drop me" not in mem.agent


def test_invalid_target_raises(tmp_path):
    store = MemoryStore(tmp_path)
    try:
        store.write("OTHER", "add", "x")
        raised = False
    except ValueError:
        raised = True
    assert raised
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/assistant/test_memory.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: No module named 'ai_dev_system.assistant'`.

- [ ] **Step 3: Implement the package + MemoryStore**

Create `src/ai_dev_system/assistant/__init__.py`:

```python
"""Conversational orchestration layer: memory, sessions, budget, prompt, Assistant."""
```

Create `src/ai_dev_system/assistant/memory.py`:

```python
"""Long-term memory: MEMORY.md (agent facts) + USER.md (operator model).

Both files live on disk so they are human-editable and travel with the operator;
they are injected into the system prompt at the start of every turn."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_TARGETS = {"MEMORY": "MEMORY.md", "USER": "USER.md"}
_ACTIONS = {"add", "replace", "remove"}


def assistant_home() -> Path:
    home = os.environ.get("AI_DEV_ASSISTANT_HOME")
    path = Path(home) if home else Path.home() / ".ai-dev-system" / "assistant"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class Memory:
    agent: str  # MEMORY.md contents
    user: str   # USER.md contents


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class MemoryStore:
    def __init__(self, home: Path) -> None:
        self._home = Path(home)
        self._home.mkdir(parents=True, exist_ok=True)

    def _path(self, target: str) -> Path:
        if target not in _TARGETS:
            raise ValueError(f"unknown memory target {target!r} (want MEMORY|USER)")
        return self._home / _TARGETS[target]

    def load(self) -> Memory:
        def _read(name: str) -> str:
            p = self._home / name
            return p.read_text(encoding="utf-8") if p.exists() else ""
        return Memory(agent=_read("MEMORY.md"), user=_read("USER.md"))

    def write(self, target: str, action: str, text: str) -> None:
        if action not in _ACTIONS:
            raise ValueError(f"unknown memory action {action!r}")
        path = self._path(target)
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        if action == "replace":
            new = text.rstrip() + "\n"
        elif action == "add":
            new = (current.rstrip() + "\n" if current.strip() else "") + text.rstrip() + "\n"
        else:  # remove
            kept = [ln for ln in current.splitlines() if ln.strip() != text.strip()]
            new = ("\n".join(kept).rstrip() + "\n") if kept else ""
        _atomic_write(path, new)
```

In `tests/unit/test_docs_reconciliation.py`, add `"assistant"` to the `EXPECTED_PACKAGES` set (match the file's existing format; read it to find the set).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/assistant/test_memory.py -q -p no:cacheprovider`
Expected: PASS (5 passed).

- [ ] **Step 5: Bump README count + full suite + commit**

Bump README count (collect-only), confirm `test_docs_reconciliation.py` passes, full suite 0 failed.

```bash
git add src/ai_dev_system/assistant/__init__.py src/ai_dev_system/assistant/memory.py tests/unit/assistant/__init__.py tests/unit/assistant/test_memory.py tests/unit/test_docs_reconciliation.py README.md
git commit -m "feat(assistant): MemoryStore (MEMORY.md + USER.md, atomic)"
```

---

### Task 3: `memory` tool

**Files:**
- Create: `src/ai_dev_system/harness/tools/memory_tool.py`
- Test: `tests/unit/harness/test_memory_tool.py`

**Interfaces:**
- Consumes: `MemoryStore` (Task 2), `claude_agent_sdk.tool`.
- Produces (consumed by Task 8): `make_memory_tool(store: MemoryStore) -> SdkMcpTool` — an `@tool("memory", ...)` whose handler calls `store.write(target, action, text)` and returns a confirmation; input schema `{"target": str, "action": str, "text": str}`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/harness/test_memory_tool.py`:

```python
import asyncio
from ai_dev_system.assistant.memory import MemoryStore
from ai_dev_system.harness.tools.memory_tool import make_memory_tool


def test_memory_tool_writes_to_store(tmp_path):
    store = MemoryStore(tmp_path)
    sdk_tool = make_memory_tool(store)
    result = asyncio.run(sdk_tool.handler({"target": "MEMORY", "action": "add", "text": "fact one"}))
    assert "content" in result
    assert "fact one" in store.load().agent


def test_memory_tool_reports_error_on_bad_target(tmp_path):
    store = MemoryStore(tmp_path)
    sdk_tool = make_memory_tool(store)
    result = asyncio.run(sdk_tool.handler({"target": "NOPE", "action": "add", "text": "x"}))
    # Tool returns an error message in content rather than raising (so the loop can recover).
    text = result["content"][0]["text"].lower()
    assert "error" in text or "unknown" in text


def test_memory_tool_name(tmp_path):
    sdk_tool = make_memory_tool(MemoryStore(tmp_path))
    assert sdk_tool.name == "memory"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/harness/test_memory_tool.py -q -p no:cacheprovider`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the tool factory**

Create `src/ai_dev_system/harness/tools/memory_tool.py`:

```python
"""The `memory` tool: lets the assistant durably record facts about itself
(MEMORY.md) or the operator (USER.md) mid-conversation."""
from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from ai_dev_system.assistant.memory import MemoryStore

_SCHEMA = {"target": str, "action": str, "text": str}


def make_memory_tool(store: MemoryStore):
    @tool(
        "memory",
        "Record durable memory. target=MEMORY (facts/conventions) or USER "
        "(operator preferences/style); action=add|replace|remove; text=the line.",
        _SCHEMA,
    )
    async def memory_tool(args: dict[str, Any]) -> dict[str, Any]:
        try:
            store.write(args["target"], args["action"], args["text"])
            msg = f"Saved to {args['target']} ({args['action']})."
        except (ValueError, KeyError) as exc:
            msg = f"memory error: {exc}"
        return {"content": [{"type": "text", "text": msg}]}

    return memory_tool
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/harness/test_memory_tool.py -q -p no:cacheprovider`
Expected: PASS (3 passed).

- [ ] **Step 5: Bump README count + full suite + commit**

```bash
git add src/ai_dev_system/harness/tools/memory_tool.py tests/unit/harness/test_memory_tool.py README.md
git commit -m "feat(harness): memory tool (writes MEMORY.md/USER.md via MemoryStore)"
```

---

### Task 4: SessionStore + crash-resume marker

**Files:**
- Create: `src/ai_dev_system/assistant/session.py`
- Test: `tests/unit/assistant/test_session.py`

**Interfaces:**
- Consumes: `get_connection`/`apply_schema`; the `assistant_sessions`/`assistant_messages` tables (Task 1).
- Produces (consumed by Tasks 5, 7, 8):
  - `@dataclass Turn(role: str, content: str)`.
  - `class SessionStore(conn_factory)` where `conn_factory() -> sqlite3.Connection`:
    - `load_or_create(surface: str, chat_id: str) -> str` — returns existing or new `session_id` (a `uuid4` hex); upserts.
    - `append(session_id, role, content, *, input_tokens=None, output_tokens=None, cost_usd=None) -> None` — inserts a message, bumps `updated_at`.
    - `recent(session_id, limit: int) -> list[Turn]` — last `limit` turns in chronological order.
    - `set_status(session_id, status) -> None`; `get_status(session_id) -> str`.
  - Marker helpers (module-level): `clean_shutdown_path(home) -> Path`, `mark_clean_shutdown(home)`, `consume_clean_shutdown(home) -> bool` (returns True if marker existed, then deletes it).

> Use `uuid.uuid4().hex` for ids. Do NOT use `Date.now()`-style nondeterminism in logic; `created_at`/`updated_at` use SQLite `CURRENT_TIMESTAMP` (or `datetime('now')`).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/assistant/test_session.py`:

```python
from ai_dev_system.assistant.session import (
    SessionStore, Turn, mark_clean_shutdown, consume_clean_shutdown, clean_shutdown_path,
)


def test_load_or_create_is_stable(conn):
    store = SessionStore(lambda: conn)
    sid1 = store.load_or_create("local", "cli")
    sid2 = store.load_or_create("local", "cli")
    assert sid1 == sid2  # same (surface, chat_id) → same session


def test_append_and_recent_chronological(conn):
    store = SessionStore(lambda: conn)
    sid = store.load_or_create("local", "cli")
    store.append(sid, "user", "hi")
    store.append(sid, "assistant", "hello")
    store.append(sid, "user", "bye")
    turns = store.recent(sid, limit=2)
    assert [t.content for t in turns] == ["hello", "bye"]
    assert all(isinstance(t, Turn) for t in turns)


def test_status_roundtrip(conn):
    store = SessionStore(lambda: conn)
    sid = store.load_or_create("local", "cli")
    assert store.get_status(sid) == "active"
    store.set_status(sid, "resume_pending")
    assert store.get_status(sid) == "resume_pending"


def test_clean_shutdown_marker(tmp_path):
    assert consume_clean_shutdown(tmp_path) is False
    mark_clean_shutdown(tmp_path)
    assert clean_shutdown_path(tmp_path).exists()
    assert consume_clean_shutdown(tmp_path) is True   # existed → True, now deleted
    assert consume_clean_shutdown(tmp_path) is False  # gone
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/assistant/test_session.py -q -p no:cacheprovider`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement SessionStore + markers**

Create `src/ai_dev_system/assistant/session.py`:

```python
"""Durable conversational sessions: one persistent transcript per (surface, chat_id),
keyed by session_id. The transcript IS the crash-resume state (turn-level)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Turn:
    role: str
    content: str


class SessionStore:
    def __init__(self, conn_factory) -> None:
        self._conn_factory = conn_factory

    def load_or_create(self, surface: str, chat_id: str) -> str:
        conn = self._conn_factory()
        row = conn.execute(
            "SELECT session_id FROM assistant_sessions WHERE surface=? AND chat_id=?",
            (surface, chat_id),
        ).fetchone()
        if row is not None:
            return row["session_id"]
        sid = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO assistant_sessions (session_id, surface, chat_id, status) "
            "VALUES (?,?,?, 'active')",
            (sid, surface, chat_id),
        )
        conn.commit()
        return sid

    def append(self, session_id: str, role: str, content: str, *,
               input_tokens=None, output_tokens=None, cost_usd=None) -> None:
        conn = self._conn_factory()
        conn.execute(
            "INSERT INTO assistant_messages "
            "(session_id, role, content, input_tokens, output_tokens, cost_usd) "
            "VALUES (?,?,?,?,?,?)",
            (session_id, role, content, input_tokens, output_tokens, cost_usd),
        )
        conn.execute(
            "UPDATE assistant_sessions SET updated_at=datetime('now') WHERE session_id=?",
            (session_id,),
        )
        conn.commit()

    def recent(self, session_id: str, limit: int) -> list[Turn]:
        conn = self._conn_factory()
        rows = conn.execute(
            "SELECT role, content FROM assistant_messages WHERE session_id=? "
            "ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [Turn(role=r["role"], content=r["content"]) for r in reversed(rows)]

    def set_status(self, session_id: str, status: str) -> None:
        conn = self._conn_factory()
        conn.execute(
            "UPDATE assistant_sessions SET status=? WHERE session_id=?", (status, session_id)
        )
        conn.commit()

    def get_status(self, session_id: str) -> str:
        conn = self._conn_factory()
        row = conn.execute(
            "SELECT status FROM assistant_sessions WHERE session_id=?", (session_id,)
        ).fetchone()
        return row["status"] if row else ""


# --- crash-shutdown marker ---------------------------------------------------

def clean_shutdown_path(home) -> Path:
    return Path(home) / ".clean_shutdown"


def mark_clean_shutdown(home) -> None:
    clean_shutdown_path(home).write_text("ok", encoding="utf-8")


def consume_clean_shutdown(home) -> bool:
    """True if a clean-shutdown marker existed (then deletes it); False otherwise."""
    p = clean_shutdown_path(home)
    if p.exists():
        p.unlink()
        return True
    return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/assistant/test_session.py -q -p no:cacheprovider`
Expected: PASS (4 passed).

- [ ] **Step 5: Bump README count + full suite + commit**

```bash
git add src/ai_dev_system/assistant/session.py tests/unit/assistant/test_session.py README.md
git commit -m "feat(assistant): SessionStore (durable transcript) + clean-shutdown marker"
```

---

### Task 5: BudgetTracker

**Files:**
- Create: `src/ai_dev_system/assistant/budget.py`
- Test: `tests/unit/assistant/test_budget.py`

**Interfaces:**
- Consumes: the `assistant_messages` rows written by `SessionStore.append` (Task 4).
- Produces (consumed by Task 7):
  - `@dataclass Budget(input_tokens: int, output_tokens: int, cost_usd: float)`.
  - `class BudgetTracker(conn_factory)`: `session_total(session_id) -> Budget` (sums non-null columns, treating null as 0); `over_cap(session_id, cap_usd: float | None) -> bool` (False when `cap_usd` is None).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/assistant/test_budget.py`:

```python
from ai_dev_system.assistant.session import SessionStore
from ai_dev_system.assistant.budget import BudgetTracker, Budget


def test_session_total_sums_costs(conn):
    sessions = SessionStore(lambda: conn)
    sid = sessions.load_or_create("local", "cli")
    sessions.append(sid, "user", "hi")  # null tokens/cost
    sessions.append(sid, "assistant", "hello", input_tokens=10, output_tokens=5, cost_usd=0.01)
    sessions.append(sid, "assistant", "again", input_tokens=20, output_tokens=7, cost_usd=0.02)
    total = BudgetTracker(lambda: conn).session_total(sid)
    assert isinstance(total, Budget)
    assert total.input_tokens == 30
    assert total.output_tokens == 12
    assert abs(total.cost_usd - 0.03) < 1e-9


def test_over_cap(conn):
    sessions = SessionStore(lambda: conn)
    sid = sessions.load_or_create("local", "cli")
    sessions.append(sid, "assistant", "x", cost_usd=0.5)
    bt = BudgetTracker(lambda: conn)
    assert bt.over_cap(sid, None) is False
    assert bt.over_cap(sid, 1.0) is False
    assert bt.over_cap(sid, 0.4) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/assistant/test_budget.py -q -p no:cacheprovider`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement BudgetTracker**

Create `src/ai_dev_system/assistant/budget.py`:

```python
"""Per-session token/cost rollup, aggregated from assistant_messages."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Budget:
    input_tokens: int
    output_tokens: int
    cost_usd: float


class BudgetTracker:
    def __init__(self, conn_factory) -> None:
        self._conn_factory = conn_factory

    def session_total(self, session_id: str) -> Budget:
        conn = self._conn_factory()
        row = conn.execute(
            "SELECT "
            "COALESCE(SUM(input_tokens),0) AS i, "
            "COALESCE(SUM(output_tokens),0) AS o, "
            "COALESCE(SUM(cost_usd),0.0) AS c "
            "FROM assistant_messages WHERE session_id=?",
            (session_id,),
        ).fetchone()
        return Budget(input_tokens=int(row["i"]), output_tokens=int(row["o"]),
                      cost_usd=float(row["c"]))

    def over_cap(self, session_id: str, cap_usd: float | None) -> bool:
        if cap_usd is None:
            return False
        return self.session_total(session_id).cost_usd >= cap_usd
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/assistant/test_budget.py -q -p no:cacheprovider`
Expected: PASS (2 passed).

- [ ] **Step 5: Bump README count + full suite + commit**

```bash
git add src/ai_dev_system/assistant/budget.py tests/unit/assistant/test_budget.py README.md
git commit -m "feat(assistant): BudgetTracker (per-session token/cost rollup + cap)"
```

---

### Task 6: PromptBuilder

**Files:**
- Create: `src/ai_dev_system/assistant/prompt.py`
- Test: `tests/unit/assistant/test_prompt.py`

**Interfaces:**
- Consumes: `Memory` (Task 2), `Turn` (Task 4).
- Produces (consumed by Task 7):
  - `build_system_prompt(base: str, mem: Memory) -> str` — base persona, then a `## What you remember` section with `mem.agent` (omitted if empty), then `## About the operator` with `mem.user` (omitted if empty).
  - `render_user_turn(history: list[Turn], message: str) -> str` — if `history` is empty, returns `message` unchanged; otherwise a `Conversation so far:` block (`User:`/`Assistant:` lines) followed by `Now reply to this message:\n<message>`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/assistant/test_prompt.py`:

```python
from ai_dev_system.assistant.memory import Memory
from ai_dev_system.assistant.session import Turn
from ai_dev_system.assistant.prompt import build_system_prompt, render_user_turn


def test_system_prompt_includes_memory_sections():
    out = build_system_prompt("BASE", Memory(agent="agent fact", user="user pref"))
    assert "BASE" in out
    assert "agent fact" in out
    assert "user pref" in out


def test_system_prompt_omits_empty_sections():
    out = build_system_prompt("BASE", Memory(agent="", user=""))
    assert out.strip() == "BASE" or "remember" not in out.lower()


def test_render_user_turn_no_history_is_passthrough():
    assert render_user_turn([], "hello") == "hello"


def test_render_user_turn_includes_history_and_message():
    hist = [Turn("user", "q1"), Turn("assistant", "a1")]
    out = render_user_turn(hist, "q2")
    assert "q1" in out and "a1" in out and "q2" in out
    assert out.rstrip().endswith("q2")  # the new message is last
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/assistant/test_prompt.py -q -p no:cacheprovider`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement PromptBuilder**

Create `src/ai_dev_system/assistant/prompt.py`:

```python
"""Assembles the system prompt (base persona + memory) and renders the recent-history
window into the user turn. Multi-turn context is carried here, from durable storage,
so the harness stays stateless per turn."""
from __future__ import annotations

from ai_dev_system.assistant.memory import Memory
from ai_dev_system.assistant.session import Turn


def build_system_prompt(base: str, mem: Memory) -> str:
    parts = [base.rstrip()]
    if mem.agent.strip():
        parts.append("## What you remember\n" + mem.agent.strip())
    if mem.user.strip():
        parts.append("## About the operator\n" + mem.user.strip())
    return "\n\n".join(parts)


def render_user_turn(history: list[Turn], message: str) -> str:
    if not history:
        return message
    lines = []
    for t in history:
        label = "User" if t.role == "user" else "Assistant"
        lines.append(f"{label}: {t.content}")
    return (
        "Conversation so far:\n" + "\n".join(lines)
        + "\n\nNow reply to this message:\n" + message
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/assistant/test_prompt.py -q -p no:cacheprovider`
Expected: PASS (4 passed).

- [ ] **Step 5: Bump README count + full suite + commit**

```bash
git add src/ai_dev_system/assistant/prompt.py tests/unit/assistant/test_prompt.py README.md
git commit -m "feat(assistant): PromptBuilder (memory system prompt + history window)"
```

---

### Task 7: Assistant.respond (orchestration)

**Files:**
- Create: `src/ai_dev_system/assistant/agent.py`
- Test: `tests/unit/assistant/test_agent.py`

**Interfaces:**
- Consumes: an `AgentRuntime` (Plan 1 — `run_turn(system_prompt, user_text) -> TurnResult`), `MemoryStore`, `SessionStore`, `BudgetTracker`, `build_system_prompt`/`render_user_turn`.
- Produces (consumed by Task 8):
  - `class Assistant(*, runtime, memory_store, session_store, budget, base_prompt, session_id, window=10, cap_usd=None)` with `respond(user_text: str) -> TurnResult`.
  - `respond` flow: if `budget.over_cap(session_id, cap_usd)` → return a `TurnResult` with a cap message (no model call); else load memory, build system prompt, fetch `recent(session_id, window)`, render the user turn, call `runtime.run_turn`, persist the **raw** user text then the assistant `final_text` (with usage/cost on the assistant row), return the runtime's `TurnResult`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/assistant/test_agent.py`:

```python
from ai_dev_system.harness.runtime import TurnResult
from ai_dev_system.assistant.memory import MemoryStore
from ai_dev_system.assistant.session import SessionStore
from ai_dev_system.assistant.budget import BudgetTracker
from ai_dev_system.assistant.agent import Assistant


class _RecordingRuntime:
    def __init__(self, result):
        self._result = result
        self.calls = []

    def run_turn(self, system_prompt, user_text):
        self.calls.append((system_prompt, user_text))
        return self._result


def _assistant(conn, tmp_path, runtime, **kw):
    sessions = SessionStore(lambda: conn)
    sid = sessions.load_or_create("local", "cli")
    return Assistant(
        runtime=runtime,
        memory_store=MemoryStore(tmp_path),
        session_store=sessions,
        budget=BudgetTracker(lambda: conn),
        base_prompt="BASE",
        session_id=sid,
        **kw,
    ), sid, sessions


def test_respond_persists_user_and_assistant(conn, tmp_path):
    result = TurnResult("the answer", [], {"input_tokens": 3, "output_tokens": 4}, 0.05, "x")
    runtime = _RecordingRuntime(result)
    asst, sid, sessions = _assistant(conn, tmp_path, runtime)
    out = asst.respond("the question")
    assert out is result
    turns = sessions.recent(sid, 10)
    assert [(t.role, t.content) for t in turns] == [
        ("user", "the question"), ("assistant", "the answer"),
    ]


def test_respond_feeds_history_on_second_turn(conn, tmp_path):
    runtime = _RecordingRuntime(TurnResult("a2", [], {}, None, None))
    asst, sid, sessions = _assistant(conn, tmp_path, runtime)
    sessions.append(sid, "user", "q1")
    sessions.append(sid, "assistant", "a1")
    asst.respond("q2")
    _, sent_user = runtime.calls[-1]
    assert "q1" in sent_user and "a1" in sent_user and sent_user.rstrip().endswith("q2")


def test_respond_injects_memory_into_system_prompt(conn, tmp_path):
    runtime = _RecordingRuntime(TurnResult("ok", [], {}, None, None))
    asst, sid, sessions = _assistant(conn, tmp_path, runtime)
    asst._memory_store.write("MEMORY", "add", "remember-this-fact")
    asst.respond("hi")
    sent_system, _ = runtime.calls[-1]
    assert "remember-this-fact" in sent_system


def test_respond_blocks_when_over_cap(conn, tmp_path):
    runtime = _RecordingRuntime(TurnResult("should not run", [], {}, None, None))
    asst, sid, sessions = _assistant(conn, tmp_path, runtime, cap_usd=0.01)
    sessions.append(sid, "assistant", "prior", cost_usd=0.02)  # already over
    out = asst.respond("hi")
    assert runtime.calls == []                       # model NOT called
    assert "budget" in out.final_text.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/assistant/test_agent.py -q -p no:cacheprovider`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement Assistant**

Create `src/ai_dev_system/assistant/agent.py`:

```python
"""Assistant — ties harness + memory + sessions + budget into one turn.

respond(): load memory → build prompt (base + memory) → fetch recent-history window
→ render the user turn with history → run the harness → persist both turns + usage."""
from __future__ import annotations

from ai_dev_system.harness.runtime import TurnResult, TurnEvent
from ai_dev_system.assistant.prompt import build_system_prompt, render_user_turn


class Assistant:
    def __init__(self, *, runtime, memory_store, session_store, budget,
                 base_prompt: str, session_id: str, window: int = 10,
                 cap_usd: float | None = None) -> None:
        self._runtime = runtime
        self._memory_store = memory_store
        self._session_store = session_store
        self._budget = budget
        self._base_prompt = base_prompt
        self._session_id = session_id
        self._window = window
        self._cap_usd = cap_usd

    def respond(self, user_text: str) -> TurnResult:
        if self._budget.over_cap(self._session_id, self._cap_usd):
            total = self._budget.session_total(self._session_id)
            return TurnResult(
                final_text=(
                    f"Budget cap reached (${total.cost_usd:.4f} ≥ ${self._cap_usd}). "
                    "Raise AI_DEV_ASSISTANT_BUDGET_USD or start a new session."
                ),
                events=[], usage={}, cost_usd=None, session_id=self._session_id,
            )
        mem = self._memory_store.load()
        system_prompt = build_system_prompt(self._base_prompt, mem)
        history = self._session_store.recent(self._session_id, self._window)
        composed = render_user_turn(history, user_text)

        result = self._runtime.run_turn(system_prompt, composed)

        usage = result.usage or {}
        self._session_store.append(self._session_id, "user", user_text)
        self._session_store.append(
            self._session_id, "assistant", result.final_text,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            cost_usd=result.cost_usd,
        )
        return result
```

> `TurnEvent` is imported for parity with the runtime module surface even though the cap-path returns no events; if a linter flags it as unused, drop the import.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/assistant/test_agent.py -q -p no:cacheprovider`
Expected: PASS (4 passed).

- [ ] **Step 5: Bump README count + full suite + commit**

```bash
git add src/ai_dev_system/assistant/agent.py tests/unit/assistant/test_agent.py README.md
git commit -m "feat(assistant): Assistant.respond ties memory+session+budget to the harness"
```

---

### Task 8: Wire the REPL through the Assistant

**Files:**
- Modify: `src/ai_dev_system/gateway/local_cli.py` (REPL takes a responder)
- Modify: `tests/unit/gateway/test_local_cli.py` (update to the responder contract)
- Modify: `src/ai_dev_system/cli/commands/assistant.py` (`build_assistant` returns an `Assistant`; wire stores + clean-shutdown)
- Modify: `tests/unit/cli/test_assistant_command.py` (update to new `build_assistant`)
- Test: `tests/unit/gateway/test_local_cli.py`, `tests/unit/cli/test_assistant_command.py`

**Interfaces:**
- `run_repl(responder, *, input_fn=input, output_fn=print) -> None` — `responder` is any object with `respond(text: str) -> TurnResult`. Loop unchanged otherwise (banner, blank-skip, exit/quit/EOF, print `  [tool] <name>` for tool_use events, then `assistant> <final_text>`).
- `build_assistant(model: str | None) -> Assistant` — builds `ToolRegistry` (now_tool + `make_memory_tool(store)`), permission callback, `SdkAgentRuntime`, `MemoryStore(assistant_home())`, `SessionStore`/`BudgetTracker` over `get_connection(Config.from_env().database_url)` with `apply_schema` applied, `session_id = load_or_create("local","cli")`, `cap_usd` from `AI_DEV_ASSISTANT_BUDGET_USD` (None if unset). Returns the `Assistant`.
- `assistant_cmd`: on start, `consume_clean_shutdown(home)`; if it was NOT clean, `set_status(session_id,"resume_pending")` and print a "(resumed previous session)" note; run the REPL; in a `finally`, `mark_clean_shutdown(home)`.

- [ ] **Step 1: Update the REPL test (responder contract)**

Replace the body of `tests/unit/gateway/test_local_cli.py` so it drives a responder instead of `(runtime, system_prompt)`:

```python
from ai_dev_system.harness.runtime import TurnResult, TurnEvent
from ai_dev_system.gateway.local_cli import run_repl


class _FakeResponder:
    def __init__(self, result):
        self._result = result
        self.calls = []

    def respond(self, text):
        self.calls.append(text)
        return self._result


def _input_seq(lines):
    it = iter(lines)

    def _fn(_prompt=""):
        try:
            return next(it)
        except StopIteration:
            raise EOFError

    return _fn


def test_repl_prints_assistant_reply_then_exits():
    result = TurnResult("the time is noon",
                        [TurnEvent("tool_use", {"name": "mcp__ai_dev__now"})],
                        {}, None, None)
    responder = _FakeResponder(result)
    out = []
    run_repl(responder, input_fn=_input_seq(["what time is it?", "exit"]), output_fn=out.append)
    joined = "\n".join(out)
    assert "[tool] mcp__ai_dev__now" in joined
    assert "assistant> the time is noon" in joined
    assert responder.calls == ["what time is it?"]


def test_repl_skips_blank_and_stops_on_eof():
    responder = _FakeResponder(TurnResult("x", [], {}, None, None))
    out = []
    run_repl(responder, input_fn=_input_seq(["   "]), output_fn=out.append)
    assert responder.calls == []
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/unit/gateway/test_local_cli.py -q -p no:cacheprovider`
Expected: FAIL — `run_repl` still expects `(runtime, system_prompt)`.

- [ ] **Step 3: Update `run_repl` to the responder contract**

In `src/ai_dev_system/gateway/local_cli.py`, replace the function:

```python
"""Local terminal REPL — drives an object with respond(text) -> TurnResult."""
from __future__ import annotations

BANNER = "ai-dev assistant — type 'exit' to quit."
_STOP = {"exit", "quit"}


def run_repl(responder, *, input_fn=input, output_fn=print) -> None:
    output_fn(BANNER)
    while True:
        try:
            line = input_fn("you> ")
        except EOFError:
            break
        text = line.strip()
        if text.lower() in _STOP:
            break
        if not text:
            continue
        result = responder.respond(text)
        for ev in result.events:
            if ev.kind == "tool_use":
                output_fn(f"  [tool] {ev.data['name']}")
        output_fn(f"assistant> {result.final_text}")
```

(The old `from ai_dev_system.harness.runtime import AgentRuntime` import and the `system_prompt` parameter are removed.)

- [ ] **Step 4: Run the REPL test to verify it passes**

Run: `python -m pytest tests/unit/gateway/test_local_cli.py -q -p no:cacheprovider`
Expected: PASS (2 passed).

- [ ] **Step 5: Update the CLI test for the new `build_assistant`**

Replace `tests/unit/cli/test_assistant_command.py` with:

```python
def test_build_assistant_returns_assistant_with_memory_and_now_tools(tmp_path, monkeypatch):
    # Isolate the assistant home + DB so the test doesn't touch the real ones.
    monkeypatch.setenv("AI_DEV_ASSISTANT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'ctl.db'}")
    from ai_dev_system.cli.commands.assistant import build_assistant
    from ai_dev_system.assistant.agent import Assistant

    asst = build_assistant(model=None)
    assert isinstance(asst, Assistant)
    # the runtime carries both tools (now + memory)
    names = asst._runtime._registry.allowed_tool_names()
    assert "mcp__ai_dev__now" in names
    assert "mcp__ai_dev__memory" in names


def test_assistant_command_is_registered_on_root_app():
    import ai_dev_system.cli.commands  # noqa: F401
    from ai_dev_system.cli.core.registry import get_app

    names = {c.name for c in get_app().registered_commands}
    assert "assistant" in names
```

- [ ] **Step 6: Run it to verify it fails**

Run: `python -m pytest tests/unit/cli/test_assistant_command.py -q -p no:cacheprovider`
Expected: FAIL — `build_assistant` still returns a tuple / lacks the memory tool.

- [ ] **Step 7: Rewire `build_assistant` + `assistant_cmd`**

Replace `src/ai_dev_system/cli/commands/assistant.py` with:

```python
"""ai-dev assistant — conversational assistant over the owned harness, with
durable memory, sessions, and budget (Plan 2). Local REPL surface."""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

import typer

from ai_dev_system.cli.core.registry import command

if TYPE_CHECKING:
    from ai_dev_system.assistant.agent import Assistant

_SYSTEM_PROMPT = (
    "You are ai-dev's internal assistant. You own your tool-use loop. "
    "Use the 'now' tool for the current time. Use the 'memory' tool to durably "
    "record facts about yourself (MEMORY) or the operator (USER) when worth remembering."
)


def build_assistant(model: str | None) -> "Assistant":
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
    from ai_dev_system.assistant.agent import Assistant

    cfg = Config.from_env()
    apply_schema(get_connection(cfg.database_url))  # ensure tables exist

    def conn_factory():
        return get_connection(cfg.database_url)

    store = MemoryStore(assistant_home())
    registry = ToolRegistry()
    registry.register(now_tool, "now")
    registry.register(make_memory_tool(store), "memory")

    runtime = SdkAgentRuntime(
        registry=registry,
        permission_callback=make_permission_callback(),
        model=model,
    )
    sessions = SessionStore(conn_factory)
    session_id = sessions.load_or_create("local", "cli")
    cap = os.environ.get("AI_DEV_ASSISTANT_BUDGET_USD")
    return Assistant(
        runtime=runtime,
        memory_store=store,
        session_store=sessions,
        budget=BudgetTracker(conn_factory),
        base_prompt=_SYSTEM_PROMPT,
        session_id=session_id,
        cap_usd=float(cap) if cap else None,
    )


@command(verb="assistant", help="Launch the conversational assistant (local REPL).")
def assistant_cmd(
    model: str = typer.Option(None, "--model", help="Model alias (default: account default)."),
) -> None:
    from ai_dev_system.gateway.local_cli import run_repl
    from ai_dev_system.assistant.memory import assistant_home
    from ai_dev_system.assistant.session import (
        consume_clean_shutdown, mark_clean_shutdown,
    )

    home = assistant_home()
    asst = build_assistant(model=model)
    if not consume_clean_shutdown(home):
        asst._session_store.set_status(asst._session_id, "resume_pending")
        typer.echo("(resumed previous session)")
    try:
        run_repl(asst)
    finally:
        mark_clean_shutdown(home)
    raise typer.Exit(0)
```

- [ ] **Step 8: Run both wiring tests to verify they pass**

Run: `python -m pytest tests/unit/cli/test_assistant_command.py tests/unit/gateway/test_local_cli.py -q -p no:cacheprovider`
Expected: PASS (4 passed).

- [ ] **Step 9: Bump README count + full suite + commit**

Bump README count (collect-only), full suite 0 failed.

```bash
git add src/ai_dev_system/gateway/local_cli.py src/ai_dev_system/cli/commands/assistant.py tests/unit/gateway/test_local_cli.py tests/unit/cli/test_assistant_command.py README.md
git commit -m "feat(assistant): route REPL through Assistant (memory+session+budget+resume)"
```

---

### Task 9: Manual multi-turn + memory smoke test on Max (human-in-loop)

**Files:** none (manual verification + a note).
- Create: `docs/superpowers/plans/notes/2026-06-29-plan2-smoke.md`

Unit tests use a recording runtime; this verifies the real path: multi-turn memory across turns on the Max subscription.

- [ ] **Step 1: Confirm auth env** — `python -c "import os; print('API_KEY_SET=', bool(os.environ.get('ANTHROPIC_API_KEY')))"` → False (the project `.env` keeps it empty).

- [ ] **Step 2: Multi-turn + memory run**

Run (from a Windows shell where `ai-dev` is installed, or via the module as in Plan 1):
```
printf 'remember that my name is Hung\nwhat is my name?\nexit\n' | python -c "import sys; sys.argv=['ai-dev','assistant']; from ai_dev_system.cli.main import main; main()"
```
Expected: turn 2 answers "Hung" (multi-turn history carried), and ideally a `[tool] mcp__ai_dev__memory` marker appears on turn 1 (the model chose to save it). At minimum, the second turn must show the model received the first turn's content.

- [ ] **Step 3: Restart persistence**

Run the assistant again with `printf 'what is my name?\nexit\n' | ...`. Because the transcript is durable, the model should still answer "Hung" from the recent-history window (or from `MEMORY.md`/`USER.md` if it saved one). Confirm `~/.ai-dev-system/assistant/MEMORY.md`/`USER.md` and the `assistant_messages` rows exist.

- [ ] **Step 4: Record result** in `docs/superpowers/plans/notes/2026-06-29-plan2-smoke.md` (what worked, whether memory tool fired, restart continuity, anything surprising). If it fails, capture output verbatim.

- [ ] **Step 5: Full suite** — `python -m pytest -q -p no:cacheprovider 2>&1 | tail -3` → green.

- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/plans/notes/2026-06-29-plan2-smoke.md
git commit -m "docs(assistant): record Plan 2 multi-turn + memory smoke result"
```

---

## Self-Review

**Spec coverage (Plan 2 portion):** long-term memory `MEMORY.md`+`USER.md` ✅ (Task 2) injected into the system prompt ✅ (Task 6/7) + `memory` tool ✅ (Task 3); persistent sessions ✅ (Task 4) with multi-turn history ✅ (Task 6/7); crash-resume marker + `resume_pending` ✅ (Task 4/8, turn-level per the spec); budget rollup ✅ (Task 5) + optional cap ✅ (Task 7); REPL routed through the `Assistant` ✅ (Task 8); harness unchanged ✅ (constraint honored — only `assistant/`, a tool, a migration, and gateway/cli rewiring). Telegram/notifier/run_links remain Plan 3/5.

**Placeholder scan:** every code step contains complete code; commands have expected output; the one `TurnEvent`-import parity note is a real lint hint, not deferred work.

**Type consistency:** `Turn(role, content)` and `TurnResult(final_text, events, usage, cost_usd, session_id)` used identically across session/prompt/agent/tests. `SessionStore.append(..., input_tokens, output_tokens, cost_usd)` matches the columns in Task 1's migration and the reads in `BudgetTracker.session_total`. `build_assistant(model) -> Assistant` is consumed by `assistant_cmd` and the updated CLI test. `run_repl(responder, ...)` with `responder.respond(text) -> TurnResult` matches `Assistant.respond` and both fakes. `make_memory_tool(store)` registered as `"memory"` → allow-list `mcp__ai_dev__memory` asserted in Task 8.

**Decision flags for the reviewer:** (1) multi-turn via history-in-prompt (re-sent each turn, bounded) vs. live client — chosen for durability; (2) home dir reconciled to `~/.ai-dev-system/assistant/`; (3) crash-resume is turn-level (durable transcript + marker), full daemon auto-resume is Plan 3. Tests access private attrs (`asst._runtime`, `asst._session_store`) for wiring assertions — acceptable for same-package unit tests, but a reviewer may prefer a small public accessor.
