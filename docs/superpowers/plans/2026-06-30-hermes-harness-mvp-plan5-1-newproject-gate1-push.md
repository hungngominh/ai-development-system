# Plan 5.1 — New-project: conversational → Gate 1 + reactive push Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Let the operator drive the **new-project** flow from chat (Telegram/REPL via the owned harness): start a project → the debate runs in the background → get a **one-shot push** when it pauses at Gate 1 → answer Gate 1 in chat → Phase B runs automatically (Gate 2 auto-approved for now) → get a **one-shot push** when it finishes.

**Architecture:** Three harness tools (`dev_newproject_start`, `dev_run_status`, `dev_answer_gate`) wrap the EXISTING, proven machinery — they spawn the same detached `ai-dev start` / `ai-dev phase-b run` processes the WebUI already uses, and read run state straight from the `runs` table. A new `RunLinkStore` (v8 migration) maps `run_id → (surface, chat_id)`; a new `RunStatusWatcher` polls linked runs once per gateway loop and pushes a single message per state transition via the existing `Platform.reply`. Gate 2 stays auto-approved (the non-interactive `phase-b run` path already does this when stdin isn't a TTY) — **conversational Gate 2 is Plan 5.2.**

**Tech Stack:** Python stdlib, SQLite (WAL), the Claude-Agent-SDK harness (`@tool`), detached `subprocess.Popen`, pytest with the existing stub-LLM pattern.

## Design decisions (please confirm at approval)

1. **Intake = conversational via the Assistant, NOT the structured IntakeState wizard (deferred).** The Assistant is already a conversational agent; for 5.1 it gathers the idea in natural chat and calls `dev_newproject_start(project_name, idea)`. The 13-field `IntakeState` wizard + `brief_v2`→debate wiring is **half-built** (`start_project.main` doesn't pass `brief_v2`; the intake run and a debate run are separate) and porting it is risky. Reusing the battle-tested `ai-dev start --idea` spawn keeps 5.1 a clean, runnable loop. *(Structured intake wizard = a later enhancement; flag if you want it in 5.1 instead.)*
2. **Reuse the proven spawns.** `dev_newproject_start` spawns `python -m ai_dev_system.cli.main start --project-name N --idea I` detached (exact WebUI pattern). `dev_answer_gate` (on Gate-1 approve) spawns `python -m ai_dev_system.cli.main phase-b run --run-id R` detached — which auto-approves Gate 2 because its stdin isn't a TTY (`run_phase_b._make_gate2_io`).
3. **Chat-bound tools.** `AssistantFactory.for_chat(surface, chat_id)` builds the `dev_*` tools bound to that chat, so `dev_newproject_start` can record the run-link `(run_id → surface, chat_id)` without the model supplying ids.
4. **Notifier pushes via the existing daemon adapters**, invoked through a new `post_poll_hook` on `GatewayDaemon` (one check per loop iteration). Reply-only surfaces gain proactive push with no new transport.

## Global Constraints

- **stdlib only**; no new third-party dependency.
- **Reuse, don't rebuild:** spawn the existing `cli.main start` / `cli.main phase-b run`; read run state from `runs`; use existing `parse_user_input` / `finalize_gate1` / `load_gate1_context`. Do NOT modify `debate_pipeline.py`, `run_phase_b.py`, or the gate internals in this plan.
- **Push exactly once per (run_id, state):** dedup via `run_notifications` UNIQUE(run_id, state). Push states = `PAUSED_AT_GATE_1`, `COMPLETED`, `FAILED`, `ABORTED` (Gate-2 pause states arrive in Plan 5.2).
- **chat_id allowlist already enforced** at the gateway; the notifier only ever pushes to a `run_links` chat_id (which came from an allowlisted inbound).
- **README test-count chore:** each task adds tests → bump `README.md`; reconciliation test enforces README == collected count. New top-level package? none (we add modules to existing `assistant/`, `gateway/`, `harness/tools/`). If a brand-new sub-package dir is created, add it to `EXPECTED_SUB_PACKAGES` in `tests/unit/test_docs_reconciliation.py`.
- **UTF-8 everywhere** (Vietnamese chat text); files write `encoding="utf-8"`.
- **Migration:** add `docs/schema/migrations/v8-run-links.sql` with `CREATE TABLE IF NOT EXISTS` only (no special runner handling needed); `apply_schema` picks it up lexicographically after v7.

---

### Task 1: v8 migration + `RunLinkStore` (run-links + push dedup)

**Files:**
- Create: `docs/schema/migrations/v8-run-links.sql`
- Create: `src/ai_dev_system/assistant/run_links.py`
- Test: `tests/unit/assistant/test_run_links.py`

**Interfaces (produced; consumed by Tasks 2–4):**
- `RunLink` dataclass: `run_id, surface, chat_id, session_id, kind`.
- `RunLinkStore(conn_factory)` with:
  - `link(run_id, surface, chat_id, *, session_id=None, kind="newproject") -> None` (upsert)
  - `lookup(run_id) -> RunLink | None`
  - `active() -> list[RunLink]` (all links whose run is NOT yet terminal-notified — for simplicity: all links; the watcher filters by status)
  - `already_notified(run_id, state) -> bool`
  - `mark_notified(run_id, state) -> None` (idempotent via INSERT OR IGNORE)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/assistant/test_run_links.py`:

```python
from __future__ import annotations

from ai_dev_system.db.connection import get_connection
from ai_dev_system.db.migrator import apply_schema
from ai_dev_system.assistant.run_links import RunLinkStore, RunLink


def _factory(file_db_url):
    return lambda: get_connection(file_db_url)


def test_link_and_lookup_round_trip(file_db_url):
    cf = _factory(file_db_url)
    apply_schema(cf())
    s = RunLinkStore(cf)
    s.link("run1", "telegram", "111", session_id="sess1")
    got = s.lookup("run1")
    assert got == RunLink(run_id="run1", surface="telegram", chat_id="111",
                          session_id="sess1", kind="newproject")


def test_lookup_missing_returns_none(file_db_url):
    cf = _factory(file_db_url)
    apply_schema(cf())
    assert RunLinkStore(cf).lookup("nope") is None


def test_link_upsert_overwrites(file_db_url):
    cf = _factory(file_db_url)
    apply_schema(cf())
    s = RunLinkStore(cf)
    s.link("run1", "telegram", "111")
    s.link("run1", "telegram", "222")
    assert s.lookup("run1").chat_id == "222"


def test_active_lists_links(file_db_url):
    cf = _factory(file_db_url)
    apply_schema(cf())
    s = RunLinkStore(cf)
    s.link("a", "telegram", "1"); s.link("b", "telegram", "2")
    assert {l.run_id for l in s.active()} == {"a", "b"}


def test_notify_dedup(file_db_url):
    cf = _factory(file_db_url)
    apply_schema(cf())
    s = RunLinkStore(cf)
    s.link("run1", "telegram", "111")
    assert s.already_notified("run1", "PAUSED_AT_GATE_1") is False
    s.mark_notified("run1", "PAUSED_AT_GATE_1")
    assert s.already_notified("run1", "PAUSED_AT_GATE_1") is True
    s.mark_notified("run1", "PAUSED_AT_GATE_1")  # idempotent, no raise
    assert s.already_notified("run1", "COMPLETED") is False
```

- [ ] **Step 2: Run → RED** (`ModuleNotFoundError` / no such table).
Run: `python -m pytest tests/unit/assistant/test_run_links.py -q -p no:cacheprovider`

- [ ] **Step 3: Implement**

Create `docs/schema/migrations/v8-run-links.sql`:
```sql
-- v8: run-links (run_id -> chat) + one-shot push dedup for the notifier (Plan 5.1)
CREATE TABLE IF NOT EXISTS run_links (
    run_id     TEXT PRIMARY KEY,
    surface    TEXT NOT NULL,
    chat_id    TEXT NOT NULL,
    session_id TEXT,
    kind       TEXT NOT NULL DEFAULT 'newproject',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS run_notifications (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id  TEXT NOT NULL,
    state   TEXT NOT NULL,
    sent_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (run_id, state)
);
```

Create `src/ai_dev_system/assistant/run_links.py`:
```python
"""run_links: maps a pipeline run_id to the chat that started it, so the
notifier can push gate/terminal transitions back to the right surface.
run_notifications dedupes so each (run_id, state) is pushed exactly once."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RunLink:
    run_id: str
    surface: str
    chat_id: str
    session_id: str | None = None
    kind: str = "newproject"


class RunLinkStore:
    def __init__(self, conn_factory) -> None:
        self._conn_factory = conn_factory

    def link(self, run_id, surface, chat_id, *, session_id=None, kind="newproject") -> None:
        conn = self._conn_factory()
        conn.execute(
            "INSERT INTO run_links (run_id, surface, chat_id, session_id, kind) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT(run_id) DO UPDATE SET surface=excluded.surface, "
            "chat_id=excluded.chat_id, session_id=excluded.session_id, kind=excluded.kind",
            (run_id, surface, str(chat_id), session_id, kind),
        )
        conn.commit()

    def lookup(self, run_id) -> RunLink | None:
        conn = self._conn_factory()
        row = conn.execute(
            "SELECT run_id, surface, chat_id, session_id, kind FROM run_links WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        return RunLink(run_id=row["run_id"], surface=row["surface"], chat_id=row["chat_id"],
                       session_id=row["session_id"], kind=row["kind"])

    def active(self) -> list[RunLink]:
        conn = self._conn_factory()
        rows = conn.execute(
            "SELECT run_id, surface, chat_id, session_id, kind FROM run_links"
        ).fetchall()
        return [RunLink(run_id=r["run_id"], surface=r["surface"], chat_id=r["chat_id"],
                        session_id=r["session_id"], kind=r["kind"]) for r in rows]

    def already_notified(self, run_id, state) -> bool:
        conn = self._conn_factory()
        row = conn.execute(
            "SELECT 1 FROM run_notifications WHERE run_id=? AND state=?", (run_id, state)
        ).fetchone()
        return row is not None

    def mark_notified(self, run_id, state) -> None:
        conn = self._conn_factory()
        conn.execute(
            "INSERT OR IGNORE INTO run_notifications (run_id, state) VALUES (?,?)",
            (run_id, state),
        )
        conn.commit()
```

- [ ] **Step 4: Run → GREEN** (5 passed).
- [ ] **Step 5:** Bump README (+5); full suite `python -m pytest -q -p no:cacheprovider`; set README to real count. Commit:
```bash
git add docs/schema/migrations/v8-run-links.sql src/ai_dev_system/assistant/run_links.py tests/unit/assistant/test_run_links.py README.md
git commit -m "feat(assistant): run_links store + v8 migration (run-links + push dedup)"
```

---

### Task 2: `dev_newproject_start` + `dev_run_status` tools (chat-bound)

**Files:**
- Create: `src/ai_dev_system/harness/tools/dev_pipeline.py`
- Test: `tests/unit/harness/test_dev_pipeline_tools.py`

**Interfaces:**
- `make_dev_pipeline_tools(*, surface, chat_id, conn_factory, config, link_store, spawn_start=None, spawn_phase_b=None) -> list[SdkMcpTool]` — returns the chat-bound `dev_newproject_start`, `dev_run_status`, and (Task 3) `dev_answer_gate`. `spawn_start`/`spawn_phase_b` are injectable for tests (default = real detached `subprocess.Popen`).
- `dev_newproject_start(project_name, idea)` → spawns `cli.main start`, derives `project_id`/`run` is created by the spawned process; **but** the run_id isn't known until the debate creates it. **Resolution:** the tool computes the deterministic `project_id = make_project_id(name_to_slug(project_name))` (from `cli.start_project`) and links by **project_id→chat** as well, then `dev_run_status`/the watcher resolve the newest run for that project. To keep it simple and robust, the tool spawns `start`, then polls up to ~10s for the newest `runs` row with that `project_id`, links `run_id→chat`, and returns `{run_id, status}`. (If not found in time, returns `{project_id, status:"starting"}` and the watcher links on first status poll by project_id.)
- `dev_run_status(run_id)` → `{status, gate, questions?}`: reads `runs.status`; if `PAUSED_AT_GATE_1`, loads `load_gate1_context(run_id, conn).questions` (id + text) into `questions`.

*(Note for implementer: prefer the simplest correct linkage. If polling-for-run_id proves flaky, fall back to linking by project_id and resolving run_id in `dev_run_status`/watcher via `SELECT run_id FROM runs WHERE project_id=? ORDER BY created_at DESC LIMIT 1`. Pick one and TEST it.)*

- [ ] **Step 1: Write failing tests** — `tests/unit/harness/test_dev_pipeline_tools.py`:
  - `dev_newproject_start` with an injected `spawn_start` (records argv, does not actually spawn) + a pre-seeded `runs` row (project_id matches) → returns the run_id and creates a `run_links` row for the chat.
  - `dev_run_status` on a seeded `PAUSED_AT_GATE_1` run with a DEBATE_REPORT → returns `status` + non-empty `questions`. (Seed the artifact the way `tests/` seed gate1 fixtures — reuse an existing helper if present; otherwise seed a minimal debate_report artifact.)
  - `dev_run_status` on a `RUNNING_PHASE_1B` run → `status` with no questions.

  *(The implementer: inspect existing gate1 test fixtures under `tests/` to seed a DEBATE_REPORT minimally; if too heavy, assert `dev_run_status` returns the raw status and gate flag, and cover the questions-payload path in an integration test marked accordingly. Keep tests offline/deterministic.)*

- [ ] **Step 2: RED.**
- [ ] **Step 3: Implement** `dev_pipeline.py` using the `@tool` pattern (see `harness/tools/memory_tool.py`). `dev_newproject_start` builds argv `[sys.executable, "-m", "ai_dev_system.cli.main", "start", "--project-name", project_name, "--idea", idea]` with the detached `creationflags`/`start_new_session` pattern from `webui._start`, cwd = repo root. Use `make_project_id`/`name_to_slug` from `ai_dev_system.cli.start_project`. Link via `link_store.link(run_id, surface, chat_id)`. `dev_run_status` reads `runs` + (on gate) `load_gate1_context`.
- [ ] **Step 4: GREEN.**
- [ ] **Step 5:** README bump; full suite; commit:
```bash
git add src/ai_dev_system/harness/tools/dev_pipeline.py tests/unit/harness/test_dev_pipeline_tools.py README.md
git commit -m "feat(harness): dev_newproject_start + dev_run_status tools (chat-bound)"
```

---

### Task 3: `dev_answer_gate` tool (Gate 1 conversational → Phase B)

**Files:**
- Modify: `src/ai_dev_system/harness/tools/dev_pipeline.py` (add the tool to `make_dev_pipeline_tools`)
- Test: `tests/unit/harness/test_dev_answer_gate.py`

**Interface:**
- `dev_answer_gate(run_id, text)` → routes a free-text Gate-1 answer:
  - load `GateSessionState` (`gate1_review.state.load_state`), `load_gate1_context`;
  - `parse_user_input(text, llm_client=None)` (regex-first; LLM off in v1 tool path → unknown falls back to a help message);
  - `answer` → `state.record_choice(qid, choice, override)` + `save_state`; return remaining-count message;
  - `approve_all`/`confirm` → build `decisions` from the resolved `GateSessionState` + context, call `finalize_gate1(run_id, decisions, config.storage_root, conn)` (sets `RUNNING_PHASE_1D`), then spawn `cli.main phase-b run --run-id <run_id>` detached (auto-approves Gate 2 via non-TTY) and return `{started_phase_b: true}`;
  - `expand`/`edit_brief`/`unknown` → return a short guidance string (full edit_brief handling is later).

*(Implementer: derive the `decisions` list from `load_gate1_context(...).decisions` / `.questions` combined with `GateSessionState.resolved`, mirroring how `gate/gate1_review/__main__.py` builds decisions for `finalize_gate1`. Read that file for the exact decision-assembly; reuse a helper if one exists rather than re-deriving.)*

- [ ] **Step 1: Write failing tests** — with a seeded `PAUSED_AT_GATE_1` run:
  - an `answer` input records a choice (assert `load_state` shows it resolved) and does NOT spawn phase-b;
  - an `approve`/`confirm` input (all questions resolved) calls a (patched) `spawn_phase_b` exactly once and the run status becomes `RUNNING_PHASE_1D`;
  - an `unknown` input returns guidance and does not change state/spawn.
- [ ] **Step 2: RED.** **Step 3: Implement.** **Step 4: GREEN.**
- [ ] **Step 5:** README bump; full suite; commit:
```bash
git add src/ai_dev_system/harness/tools/dev_pipeline.py tests/unit/harness/test_dev_answer_gate.py README.md
git commit -m "feat(harness): dev_answer_gate (Gate 1 NLU -> finalize -> Phase B auto)"
```

---

### Task 4: `RunStatusWatcher` notifier + daemon hook + factory/gateway wiring

**Files:**
- Create: `src/ai_dev_system/gateway/notifier.py`
- Modify: `src/ai_dev_system/gateway/daemon.py` (add `post_poll_hook` param, call once per loop)
- Modify: `src/ai_dev_system/assistant/factory.py` (register chat-bound dev tools in `for_chat`)
- Modify: `src/ai_dev_system/cli/commands/gateway.py` (`build_gateway` constructs the watcher + passes `post_poll_hook`; pass `link_store` + `conn_factory` to the factory)
- Test: `tests/unit/gateway/test_notifier.py`, and extend `tests/unit/gateway/test_daemon.py`

**Interfaces:**
- `RunStatusWatcher(conn_factory, link_store, platforms_by_name, *, push_states=DEFAULT)` with `check_once() -> int` (number pushed). `DEFAULT_PUSH_STATES = ("PAUSED_AT_GATE_1","COMPLETED","FAILED","ABORTED")`. For each `link_store.active()`: read `runs.status`; if status in push_states and `not already_notified(run_id, status)` and the surface has a platform → `platform.reply(int(chat_id), msg)` + `mark_notified`. Messages: gate → "🔔 Run <8> tới Gate 1 — trả lời để duyệt"; terminal → "✅/❌ Run <8>: <status>".
- `GatewayDaemon(..., post_poll_hook=None)`: in `run()`, after the platform loop and before sleep, `if self._post_poll_hook: try: self._post_poll_hook() except Exception: logger.exception(...)`.
- `AssistantFactory.for_chat(surface, chat_id)`: build the dev tools via `make_dev_pipeline_tools(surface=surface, chat_id=chat_id, conn_factory=..., config=..., link_store=...)` and include them in that chat's runtime. *(This requires `for_chat` to assemble a per-chat registry/runtime; keep the shared pieces shared and add only the chat-bound dev tools. If that's too invasive, an acceptable v1: register dev tools globally and pass `surface`/`chat_id` via a per-turn contextvar set by `Assistant.respond`. Choose the simpler correct option and TEST that a started run gets linked to the right chat.)*

- [ ] **Step 1: Write failing tests** — `tests/unit/gateway/test_notifier.py`:
  - seed a `run_links` row + a `runs` row at `PAUSED_AT_GATE_1`; a fake platform records `reply` calls; `check_once()` pushes exactly once; a second `check_once()` pushes 0 (dedup).
  - terminal `COMPLETED` pushes once; `RUNNING_*` pushes 0.
  - unknown surface (no platform) → no push, no crash.
  Extend `tests/unit/gateway/test_daemon.py`: a daemon with an injected `post_poll_hook` calls it once per iteration (use `max_iterations`, a recording hook).
- [ ] **Step 2: RED.** **Step 3: Implement** notifier + daemon hook + wiring. **Step 4: GREEN** (focused: `tests/unit/gateway/ tests/unit/assistant/ tests/unit/harness/`).
- [ ] **Step 5:** README bump; full suite; commit:
```bash
git add src/ai_dev_system/gateway/notifier.py src/ai_dev_system/gateway/daemon.py src/ai_dev_system/assistant/factory.py src/ai_dev_system/cli/commands/gateway.py tests/unit/gateway/test_notifier.py tests/unit/gateway/test_daemon.py README.md
git commit -m "feat(gateway): RunStatusWatcher push on gate/terminal + daemon hook + wire dev tools"
```

---

## Acceptance (whole plan)

A closable chat-driven loop (verified via fakes; real run is the operator smoke):
- **Start from chat:** `dev_newproject_start` spawns the debate, links `run_id→chat`.
- **Push at Gate 1:** the watcher pushes once when the run hits `PAUSED_AT_GATE_1`.
- **Answer in chat:** `dev_answer_gate` records Gate-1 answers and, on approve, finalizes + spawns Phase B (Gate 2 auto-approved).
- **Push on finish:** the watcher pushes once on `COMPLETED`/`FAILED`/`ABORTED`.
- Each (run_id, state) pushed exactly once (dedup).

## Out of scope (→ Plan 5.2 / later)
- Conversational Gate 2 (the Phase B suspend/resume refactor + `PAUSED_AT_GATE_2`).
- Structured `IntakeState` wizard + `brief_v2`→debate wiring.
- `edit_brief` Gate-1 action; per-session pre-bless of CONFIRM tools.

## Self-Review (plan author)
- **Reuse-first:** no edits to `debate_pipeline.py`/`run_phase_b.py`/gate internals; tools spawn the proven CLIs and read `runs`. ✓
- **Known soft spots flagged, not hidden:** (a) run_id discovery after spawn (poll vs link-by-project_id) — implementer must pick one and test it; (b) chat-binding of tools (per-chat registry vs contextvar) — implementer must pick the simpler correct option and test linkage; (c) decision-assembly for `finalize_gate1` — reuse `gate1_review/__main__.py`'s approach. These are the real integration risks; each names the fallback. ✓
- **Dedup + push-once** is the load-bearing notifier property; covered by Task 1 + Task 4 tests. ✓
- **No placeholders in the novel code** (RunLinkStore, migration, notifier shape are complete); the two judgment seams are explicitly delegated with concrete options rather than hand-waved. ✓
