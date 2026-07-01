# Gateway per-project routing (SP-2) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route the gateway so each repo-bound Telegram bot reads/writes its own `<repo>/.ai-dev/state/{control.db, storage/}` (via SP-1's `ProjectRegistry`), while non-repo bots keep the global DB/storage.

**Architecture:** `surface (bot label) → repo_path → ProjectRegistry.get(repo)` is the routing hub. `ProjectResources` is extended to carry per-project `link_store`/`session_store`/`budget`. `AssistantFactory.for_chat` resolves per-surface and builds dev tools + subprocess spawns against the project's storage/DB (env-injecting `STORAGE_ROOT`/`DATABASE_URL` for `start`/`phase-b`). `build_gateway` runs one watcher pair per project plus a global pair for non-repo bots.

**Tech Stack:** Python 3.12, stdlib, pytest. Reuses SP-1's `config.resolve_project`/`ProjectRegistry`, and existing `RunLinkStore`/`SessionStore`/`BudgetTracker` (all take a `conn_factory`).

## Global Constraints

- SP-2 = gateway path only. Do NOT touch webui or human-invoked CLI (SP-3). No migration of global-DB data.
- Non-repo bots (empty `repo_path`) MUST keep today's global behavior — the fallback path stays intact.
- Back-compat: `make_dev_pipeline_tools` new params default to the config's global values; `build_assistant_factory` without a registry behaves exactly as today. All existing tests must stay green.
- Subprocess env injection overlays ONLY `STORAGE_ROOT` + `DATABASE_URL` onto a copy of `os.environ` (keep tokens, `IS_SANDBOX`, git identity).
- Registry cache key = `os.path.abspath` of repo_path (matches SP-1).
- `ProjectRegistry.close_all()` is called on daemon shutdown.

---

### Task 1: `repo_path_for_label` helper in `config.py`

**Files:**
- Modify: `src/ai_dev_system/config.py` (add function)
- Modify: `src/ai_dev_system/assistant/factory.py` (use it in `build_clarify_prompt_suffix`)
- Modify: `src/ai_dev_system/harness/tools/dev_pipeline.py` (use it for `_repo_path`)
- Test: `tests/unit/test_repo_path_for_label.py`

**Interfaces:**
- Produces: `repo_path_for_label(telegram_bots, label: str) -> str` (returns `""` when no match or no repo).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_repo_path_for_label.py
from ai_dev_system.config import TelegramBotConfig, repo_path_for_label


def test_returns_repo_for_matching_label():
    bots = (
        TelegramBotConfig(label="a", token="t", repo_path="/repos/A"),
        TelegramBotConfig(label="b", token="t", repo_path="/repos/B"),
    )
    assert repo_path_for_label(bots, "b") == "/repos/B"


def test_empty_for_no_match_or_no_repo():
    bots = (TelegramBotConfig(label="a", token="t", repo_path=""),)
    assert repo_path_for_label(bots, "a") == ""      # bound but no repo
    assert repo_path_for_label(bots, "zzz") == ""    # no such label
    assert repo_path_for_label((), "a") == ""        # no bots
    assert repo_path_for_label(None, "a") == ""      # None-safe
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_repo_path_for_label.py -q`
Expected: FAIL (ImportError: `repo_path_for_label`).

- [ ] **Step 3: Implement the helper in `config.py`**

Add near `TelegramBotConfig` (module level):

```python
def repo_path_for_label(telegram_bots, label: str) -> str:
    """Return the bound repo_path for the bot whose label matches, else ''."""
    for b in telegram_bots or ():
        if getattr(b, "label", None) == label:
            return getattr(b, "repo_path", "") or ""
    return ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_repo_path_for_label.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Use the helper in `build_clarify_prompt_suffix` (factory.py)**

Replace the manual loop in `build_clarify_prompt_suffix` (the `for b in telegram_bots or ():` block that sets `repo_path`/`base_branch`). Keep `base_branch` (still needed there):

```python
def build_clarify_prompt_suffix(surface: str, telegram_bots) -> str:
    """Per-chat awareness: which repo this bot serves + how to route a clarify answer."""
    from ai_dev_system.config import repo_path_for_label
    repo_path = repo_path_for_label(telegram_bots, surface)
    base_branch = ""
    for b in telegram_bots or ():
        if getattr(b, "label", None) == surface:
            base_branch = getattr(b, "base_branch", "") or ""
            break
    parts = []
    if repo_path:
        parts.append(
            f"Bạn được gắn với repo '{surface}' (nhánh nền '{base_branch or 'main'}'). "
            "Khi người dùng yêu cầu sửa/thêm code, dùng tool dev_task_start; hỏi tiến độ "
            "dùng dev_run_status; duyệt plan dùng dev_answer_gate."
        )
    parts.append(
        "QUAN TRỌNG: nếu lượt trước bạn (bot) đã hỏi người dùng một câu LÀM RÕ, thì tin "
        "nhắn kế tiếp của họ là CÂU TRẢ LỜI — gọi tool dev_answer_clarify với nguyên văn, "
        "KHÔNG tạo task mới."
    )
    return "\n\n".join(parts)
```

- [ ] **Step 6: Use the helper for `_repo_path` in `make_dev_pipeline_tools` (dev_pipeline.py)**

Find the block that resolves `_repo_path`/`_base_branch` (a `for _b in getattr(config, "telegram_bots", ()):` loop). Replace the `_repo_path` assignment to use the helper, keeping `_base_branch`:

```python
        from ai_dev_system.config import repo_path_for_label
        _repo_path = repo_path_for_label(getattr(config, "telegram_bots", ()), surface)
        _base_branch = ""
        for _b in getattr(config, "telegram_bots", ()):
            if getattr(_b, "label", None) == surface:
                _base_branch = getattr(_b, "base_branch", "") or ""
                break
```

- [ ] **Step 7: Run the affected unit tests**

Run: `python -m pytest tests/unit/test_repo_path_for_label.py tests/unit/harness/test_dev_task_tools.py tests/unit/assistant/test_factory_chat_binding.py -q`
Expected: PASS (helper tests + unchanged behavior in the dev-task and factory-binding tests).

- [ ] **Step 8: Commit**

```bash
git add src/ai_dev_system/config.py src/ai_dev_system/assistant/factory.py src/ai_dev_system/harness/tools/dev_pipeline.py tests/unit/test_repo_path_for_label.py
git commit -m "feat(config): repo_path_for_label helper; centralize surface→repo lookup"
```

---

### Task 2: Extend `ProjectResources` + `ProjectRegistry` with per-project stores

**Files:**
- Modify: `src/ai_dev_system/gateway/project_registry.py`
- Test: `tests/unit/gateway/test_project_registry_stores.py`

**Interfaces:**
- Consumes: SP-1 `ProjectResources`/`ProjectRegistry`; `assistant.run_links.RunLinkStore(conn_factory)`, `assistant.session.SessionStore(conn_factory)`, `assistant.budget.BudgetTracker(conn_factory)`.
- Produces: `ProjectResources` now also has `link_store: RunLinkStore`, `session_store: SessionStore`, `budget: BudgetTracker`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/gateway/test_project_registry_stores.py
from ai_dev_system.gateway.project_registry import ProjectRegistry


def test_resources_carry_per_project_stores(tmp_path):
    reg = ProjectRegistry()
    try:
        res = reg.get(str(tmp_path / "repo"))
        # link_store usable on the project DB: link then read back
        res.link_store.link("run-1", "tg", "42")
        assert res.link_store.latest_for_chat("tg", "42") == "run-1"
        # session + budget present and bound to this project's conn
        assert res.session_store is not None
        assert res.budget is not None
    finally:
        reg.close_all()


def test_two_repos_have_independent_link_stores(tmp_path):
    reg = ProjectRegistry()
    try:
        a = reg.get(str(tmp_path / "a"))
        b = reg.get(str(tmp_path / "b"))
        a.link_store.link("run-a", "tg", "1")
        # b's DB must not see a's link
        assert b.link_store.latest_for_chat("tg", "1") is None
    finally:
        reg.close_all()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/gateway/test_project_registry_stores.py -q`
Expected: FAIL (`ProjectResources` has no `link_store`).

- [ ] **Step 3: Extend the dataclass + `get`**

In `src/ai_dev_system/gateway/project_registry.py`, add imports at top:

```python
from ai_dev_system.assistant.run_links import RunLinkStore
from ai_dev_system.assistant.session import SessionStore
from ai_dev_system.assistant.budget import BudgetTracker
```

Add three fields to `ProjectResources` (after `conn_factory`):

```python
    link_store: RunLinkStore
    session_store: SessionStore
    budget: BudgetTracker
```

In `ProjectRegistry.get`, after building `conn` and before constructing `ProjectResources`, build the stores and pass them in:

```python
        paths = resolve_project(key, ensure=True)
        conn = get_connection(paths.database_url)

        def _cf() -> sqlite3.Connection:
            return conn

        res = ProjectResources(
            paths=paths,
            conn=conn,
            conn_factory=_cf,
            link_store=RunLinkStore(_cf),
            session_store=SessionStore(_cf),
            budget=BudgetTracker(_cf),
        )
        self._cache[key] = res
        return res
```

(Replace the previous `conn_factory=lambda: conn` construction with the `_cf` closure so all four stores share the identical connection.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/gateway/test_project_registry_stores.py tests/unit/gateway/test_project_registry.py -q`
Expected: PASS (new tests + SP-1 registry tests still green — they only assert `paths`/`conn`/`conn_factory`, which are unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/gateway/project_registry.py tests/unit/gateway/test_project_registry_stores.py
git commit -m "feat(gateway): ProjectResources carries per-project link/session/budget stores"
```

---

### Task 3: Per-project storage/DB + subprocess env in `make_dev_pipeline_tools`

**Files:**
- Modify: `src/ai_dev_system/harness/tools/dev_pipeline.py`
- Test: `tests/unit/harness/test_dev_pipeline_per_project.py`

**Interfaces:**
- Consumes: existing tool factory.
- Produces: `make_dev_pipeline_tools(..., storage_root: str | None = None, database_url: str | None = None)`. When provided, all storage/DB paths (logs, `sr`, spawn flags) and the `start`/`phase-b` subprocess env use them; when omitted, they fall back to `config.storage_root`/`config.database_url`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/harness/test_dev_pipeline_per_project.py
import asyncio
from ai_dev_system.harness.tools.dev_pipeline import make_dev_pipeline_tools
from ai_dev_system.harness.tools.chat_task_store import ChatTaskStore


class _Cfg:
    def __init__(self, tmp, bots):
        self.storage_root = str(tmp / "global")
        self.telegram_bots = bots
        self.database_url = "sqlite:///global.db"


class _Bot:
    def __init__(self, label, repo_path="", base_branch=""):
        self.label, self.repo_path, self.base_branch = label, repo_path, base_branch


def _find(tools, name):
    for t in tools:
        if (getattr(t, "name", None) or getattr(t, "__name__", "")) == name:
            return t
    raise AssertionError(name)


def test_task_start_spawn_uses_per_project_storage_and_db(tmp_path):
    cfg = _Cfg(tmp_path, [_Bot("tg", repo_path="/repos/app", base_branch="main")])
    proj_sr = str(tmp_path / "proj" / "storage")
    proj_db = "sqlite:///proj/control.db"
    spawned = []
    tools = make_dev_pipeline_tools(
        surface="tg", chat_id="1", conn_factory=lambda: None, config=cfg,
        link_store=None, chat_task_store=ChatTaskStore(proj_sr),
        storage_root=proj_sr, database_url=proj_db,
        spawn_task_worker=lambda argv, **kw: spawned.append(argv),
        make_spec_id=lambda: "spec123",
    )
    start = _find(tools, "dev_task_start")
    asyncio.run(start.handler({"task_description": "do it"}))
    argv = spawned[0]
    assert proj_sr in argv and proj_db in argv  # per-project flags, not global


def test_newproject_spawn_injects_per_project_env(tmp_path):
    cfg = _Cfg(tmp_path, [_Bot("tg", repo_path="/repos/app")])
    proj_sr = str(tmp_path / "proj" / "storage")
    proj_db = "sqlite:///proj/control.db"
    captured = {}

    def rec_spawn(argv, **kw):
        captured["argv"] = argv
        captured["env"] = kw.get("env")

    class _Conn:
        def execute(self, *a, **k):
            class _R:  # no run row yet
                def fetchone(self_):
                    return None
            return _R()

    class _Link:
        def link(self, *a): pass
        def add_pending(self, *a): pass

    tools = make_dev_pipeline_tools(
        surface="tg", chat_id="1", conn_factory=lambda: _Conn(), config=cfg,
        link_store=_Link(), storage_root=proj_sr, database_url=proj_db,
        spawn_start=rec_spawn,
    )
    npt = _find(tools, "dev_newproject_start")
    asyncio.run(npt.handler({"project_name": "P", "idea": "x"}))
    assert captured["env"]["STORAGE_ROOT"] == proj_sr
    assert captured["env"]["DATABASE_URL"] == proj_db
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/harness/test_dev_pipeline_per_project.py -q`
Expected: FAIL (`make_dev_pipeline_tools` has no `storage_root`/`database_url` kwargs; env not injected).

- [ ] **Step 3: Add params + resolve effective values**

In `make_dev_pipeline_tools`'s signature (the `def make_dev_pipeline_tools(*, ...)` keyword list), add:

```python
    storage_root: str | None = None,
    database_url: str | None = None,
```

Immediately after the spawn injectables are resolved (near the top of the function body, after `_spawn`/`_spawn_exec` are set), add:

```python
    _storage_root = storage_root if storage_root is not None else str(config.storage_root)
    _database_url = database_url if database_url is not None else str(config.database_url)
    _env_overlay = {**os.environ, "STORAGE_ROOT": _storage_root, "DATABASE_URL": _database_url}
```

- [ ] **Step 4: Replace every `config.storage_root`/`config.database_url` use inside the tools**

Within `make_dev_pipeline_tools`, replace:
- `ChatTaskStore(config.storage_root)` → `ChatTaskStore(_storage_root)` (only in the default-construction branch)
- every `Path(config.storage_root) / "ui_logs"` → `Path(_storage_root) / "ui_logs"`
- every `sr = str(config.storage_root)` → `sr = _storage_root`
- every `str(config.database_url)` (in spawn argv) → `_database_url`
- `finalize_gate1(run_id, decisions, config.storage_root, conn)` → `finalize_gate1(run_id, decisions, _storage_root, conn)`

(These are the sites at the enumerated lines: ChatTaskStore default, the `ui_logs` log dirs, the two `sr =` assignments, the worker/executor/plan spawn `--storage-root`/`--database-url` flags, and the finalize_gate1 call.)

- [ ] **Step 5: Inject env into the `start` and `phase-b` spawns**

In `dev_newproject_start`, add `env=_env_overlay` to the `_spawn(argv, …)` call. In `dev_answer_gate`, add `env=_env_overlay` to each `_spawn_pb(pb_argv, …)` call (the `phase-b to-gate2` and `phase-b resume-gate2` spawns). Leave the worker/executor spawns as-is (they pass explicit `--storage-root`/`--database-url` flags).

- [ ] **Step 6: Run the new tests + regression**

Run: `python -m pytest tests/unit/harness/test_dev_pipeline_per_project.py tests/unit/harness/test_dev_task_tools.py tests/unit/harness/test_dev_answer_gate.py -q`
Expected: PASS. New tests prove per-project routing; existing tests (which omit `storage_root`/`database_url`) prove the fallback to `config.*` is unchanged.

- [ ] **Step 7: Commit**

```bash
git add src/ai_dev_system/harness/tools/dev_pipeline.py tests/unit/harness/test_dev_pipeline_per_project.py
git commit -m "feat(gateway): per-project storage/db + subprocess env in dev_pipeline tools"
```

---

### Task 4: `AssistantFactory.for_chat` project-aware

**Files:**
- Modify: `src/ai_dev_system/assistant/factory.py`
- Test: `tests/unit/assistant/test_factory_per_project.py`

**Interfaces:**
- Consumes: `config.repo_path_for_label`; `ProjectRegistry.get(repo) -> ProjectResources` (with `link_store`/`session_store`/`budget`/`paths`).
- Produces: `build_assistant_factory(..., project_registry=None)`; `AssistantFactory.__init__` stores `project_registry`; `for_chat` resolves per-surface.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/assistant/test_factory_per_project.py
from unittest.mock import MagicMock
from ai_dev_system.assistant.factory import AssistantFactory


class _Bot:
    def __init__(self, label, repo_path):
        self.label, self.repo_path, self.base_branch = label, repo_path, ""


def _factory(**kw):
    return AssistantFactory(
        runtime=MagicMock(), memory_store=MagicMock(),
        session_store=MagicMock(), budget=MagicMock(),
        base_prompt="P", **kw,
    )


def test_for_chat_repo_bound_uses_registry(monkeypatch):
    cfg = MagicMock()
    cfg.telegram_bots = (_Bot("tg", "/repos/app"),)
    proj = MagicMock()
    proj.session_store.load_or_create.return_value = "sid-proj"
    registry = MagicMock()
    registry.get.return_value = proj

    captured = {}
    import ai_dev_system.assistant.factory as fac

    def fake_tools(**kw):
        captured.update(kw)
        return []
    monkeypatch.setattr(fac, "make_dev_pipeline_tools", fake_tools, raising=False)

    f = _factory(link_store=MagicMock(), config=cfg, conn_factory=lambda: None,
                 project_registry=registry)
    # a minimal base runtime so _build_chat_runtime can read its attrs
    f._runtime = MagicMock(_permission_callback=None, _model=None, _max_turns=20, _client_factory=None)
    f.for_chat("tg", "42")
    registry.get.assert_called_once_with("/repos/app")
    # dev tools built with the project's storage_root + conn_factory
    assert captured["storage_root"] == proj.paths.storage_root
    assert captured["conn_factory"] is proj.conn_factory
    assert captured["link_store"] is proj.link_store


def test_for_chat_non_repo_uses_global(monkeypatch):
    cfg = MagicMock()
    cfg.telegram_bots = (_Bot("tg", ""),)  # bound but no repo
    registry = MagicMock()
    gstore = MagicMock(); gstore.load_or_create.return_value = "sid-global"
    import ai_dev_system.assistant.factory as fac
    captured = {}
    monkeypatch.setattr(fac, "make_dev_pipeline_tools", lambda **kw: captured.update(kw) or [], raising=False)

    f = _factory(link_store=MagicMock(), config=cfg, conn_factory=lambda: "gconn",
                 project_registry=registry, session_store=gstore)
    f._runtime = MagicMock(_permission_callback=None, _model=None, _max_turns=20, _client_factory=None)
    f.for_chat("tg", "42")
    registry.get.assert_not_called()               # no repo → no registry use
    assert captured["storage_root"] is None or captured["storage_root"] == cfg.storage_root
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/assistant/test_factory_per_project.py -q`
Expected: FAIL (`AssistantFactory.__init__` has no `project_registry`; `for_chat` doesn't resolve per-project).

- [ ] **Step 3: Add `project_registry` to `AssistantFactory.__init__`**

Add the parameter (keyword-only, after `spawn_phase_b`) and store it:

```python
        spawn_phase_b=None,
        project_registry=None,
    ) -> None:
        ...
        self._spawn_phase_b = spawn_phase_b
        self._project_registry = project_registry
```

- [ ] **Step 4: Resolve per-project in `for_chat` + pass into the runtime builder**

Rewrite `for_chat` to resolve the project and thread the resolved pieces:

```python
    def for_chat(self, surface: str, chat_id: str):
        from ai_dev_system.assistant.agent import Assistant
        from ai_dev_system.config import repo_path_for_label

        repo = repo_path_for_label(
            getattr(self._config, "telegram_bots", ()) if self._config else (), surface
        )
        proj = None
        if repo and self._project_registry is not None:
            proj = self._project_registry.get(repo)

        session_store = proj.session_store if proj else self._session_store
        budget = proj.budget if proj else self._budget
        session_id = session_store.load_or_create(surface, chat_id)

        if self._link_store is not None:
            runtime = self._build_chat_runtime(surface, chat_id, proj)
        else:
            runtime = self._runtime

        suffix = build_clarify_prompt_suffix(
            surface, getattr(self._config, "telegram_bots", ()) if self._config else ()
        )
        effective_prompt = self._base_prompt + ("\n\n" + suffix if suffix else "")
        return Assistant(
            runtime=runtime, memory_store=self._memory_store,
            session_store=session_store, budget=budget,
            base_prompt=effective_prompt, session_id=session_id,
            window=self._window, cap_usd=self._cap_usd,
        )
```

- [ ] **Step 5: Thread the project into `_build_chat_runtime`**

Change `_build_chat_runtime` to accept `proj` and pass per-project pieces into `make_dev_pipeline_tools`:

```python
    def _build_chat_runtime(self, surface: str, chat_id: str, proj=None):
        ...
        dev_tools = make_dev_pipeline_tools(
            surface=surface,
            chat_id=chat_id,
            conn_factory=(proj.conn_factory if proj else self._conn_factory),
            config=self._config,
            link_store=(proj.link_store if proj else self._link_store),
            storage_root=(proj.paths.storage_root if proj else None),
            database_url=(proj.paths.database_url if proj else None),
            spawn_start=self._spawn_start,
            spawn_phase_b=self._spawn_phase_b,
        )
        ...
```

(The rest of `_build_chat_runtime` — registry, now/memory tools, permission callback, runtime kwargs — is unchanged.)

- [ ] **Step 6: Pass `project_registry` through `build_assistant_factory`**

Add `project_registry=None` to `build_assistant_factory`'s signature and forward it into the `AssistantFactory(...)` construction:

```python
def build_assistant_factory(
    model: str | None,
    *,
    link_store=None,
    config=None,
    conn_factory=None,
    spawn_start=None,
    spawn_phase_b=None,
    project_registry=None,
) -> AssistantFactory:
    ...
    return AssistantFactory(
        ...
        spawn_start=spawn_start,
        spawn_phase_b=spawn_phase_b,
        project_registry=project_registry,
    )
```

- [ ] **Step 7: Run the new tests + regression**

Run: `python -m pytest tests/unit/assistant/test_factory_per_project.py tests/unit/assistant/test_factory_chat_binding.py tests/unit/assistant/test_factory.py -q`
Expected: PASS. New tests prove per-project vs global resolution; existing factory tests (no `project_registry`) prove unchanged behavior.

- [ ] **Step 8: Commit**

```bash
git add src/ai_dev_system/assistant/factory.py tests/unit/assistant/test_factory_per_project.py
git commit -m "feat(assistant): AssistantFactory.for_chat routes per-project via ProjectRegistry"
```

---

### Task 5: `build_gateway` per-project watchers + registry + resume fanout

**Files:**
- Modify: `src/ai_dev_system/cli/commands/gateway.py`
- Test: `tests/unit/gateway/test_build_gateway_per_project.py`

**Interfaces:**
- Consumes: `ProjectRegistry`, `config.repo_path_for_label`, `build_assistant_factory(project_registry=…)`, `RunStatusWatcher`, `ClarifyWatcher`.
- Produces: `build_gateway` creates a `ProjectRegistry`, one `(RunStatusWatcher, ClarifyWatcher)` per distinct bound repo + a global pair when any non-repo bot exists; `_post_poll` sweeps all; resume marking fans out; `registry.close_all()` on shutdown.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/gateway/test_build_gateway_per_project.py
from unittest.mock import MagicMock, patch
from ai_dev_system.config import Config, TelegramBotConfig
from ai_dev_system.cli.commands import gateway as gw


def _cfg(tmp_path, bots):
    return Config(storage_root=str(tmp_path / "global"),
                  database_url=f"sqlite:///{tmp_path/'global.db'}",
                  telegram_bots=tuple(bots))


def test_build_gateway_makes_one_watcher_pair_per_repo(tmp_path, monkeypatch):
    bots = [
        TelegramBotConfig(label="a", token="t", repo_path=str(tmp_path / "A")),
        TelegramBotConfig(label="b", token="t", repo_path=str(tmp_path / "B")),
    ]
    cfg = _cfg(tmp_path, bots)

    run_watchers, clarify_watchers = [], []
    monkeypatch.setattr(gw, "RunStatusWatcher",
                        lambda *a, **k: run_watchers.append((a, k)) or MagicMock(check_once=lambda: 0))
    # ClarifyWatcher is imported inside build_gateway; patch where it is defined
    import ai_dev_system.gateway.clarify_watcher as cw
    monkeypatch.setattr(cw, "ClarifyWatcher",
                        lambda *a, **k: clarify_watchers.append((a, k)) or MagicMock(check_once=lambda: 0))

    daemon = gw.build_gateway(cfg, transport=MagicMock(), sender=MagicMock())
    # a registry-backed watcher pair for each of the 2 repos (no non-repo bot → no global pair)
    assert len(run_watchers) == 2
    assert len(clarify_watchers) == 2
```

(If `PlatformRegistry.from_config` needs a live token to report `enabled()`, the two bots above carry tokens, so the registry is enabled. If the harness cannot construct real platform adapters in a unit test, this test may need the `transport`/`sender` injectables shown — they are already parameters of `build_gateway`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/gateway/test_build_gateway_per_project.py -q`
Expected: FAIL (today's `build_gateway` builds exactly ONE watcher pair regardless of bots).

- [ ] **Step 3: Rewrite `build_gateway` for per-project wiring**

Replace the body of `build_gateway` (keep the signature) with:

```python
def build_gateway(cfg, *, transport=None, sender=None, poll_timeout: int = 30):
    """Wire a GatewayDaemon from config, or return None if no platform is enabled."""
    from ai_dev_system.gateway.registry import PlatformRegistry
    from ai_dev_system.gateway.daemon import GatewayDaemon
    from ai_dev_system.gateway.notifier import RunStatusWatcher
    from ai_dev_system.gateway.clarify_watcher import ClarifyWatcher
    from ai_dev_system.gateway.project_registry import ProjectRegistry
    from ai_dev_system.assistant.factory import build_assistant_factory
    from ai_dev_system.assistant.memory import assistant_home
    from ai_dev_system.assistant.session import SessionStore
    from ai_dev_system.assistant.run_links import RunLinkStore
    from ai_dev_system.harness.tools.chat_task_store import ChatTaskStore
    from ai_dev_system.db.connection import get_connection
    from ai_dev_system.config import repo_path_for_label

    registry = PlatformRegistry.from_config(cfg, transport=transport, sender=sender)
    if not registry.enabled():
        return None

    platforms_by_name = {p.name: p for p in registry.adapters()}
    project_registry = ProjectRegistry()

    # Global (fallback) resources for non-repo bots.
    gw_conn = get_connection(cfg.database_url)

    def global_conn_factory():
        return gw_conn

    global_link_store = RunLinkStore(global_conn_factory)
    global_session_store = SessionStore(global_conn_factory)

    factory = build_assistant_factory(
        model=None,
        link_store=global_link_store,
        config=cfg,
        conn_factory=global_conn_factory,
        project_registry=project_registry,
    )

    # Distinct bound repos → one watcher pair each; global pair iff any non-repo bot.
    repos: list[str] = []
    has_non_repo = False
    for b in getattr(cfg, "telegram_bots", ()):
        rp = repo_path_for_label((b,), b.label)
        if rp:
            if rp not in repos:
                repos.append(rp)
        else:
            has_non_repo = True

    watchers = []            # (RunStatusWatcher, ClarifyWatcher)
    resume_stores = []       # session stores to mark resume-pending on unclean restart

    for rp in repos:
        res = project_registry.get(rp)
        rw = RunStatusWatcher(res.conn_factory, res.link_store, platforms_by_name)
        cwt = ClarifyWatcher(
            ChatTaskStore(res.paths.storage_root), platforms_by_name,
            res.session_store, res.paths.storage_root,
        )
        watchers.append((rw, cwt))
        resume_stores.append(res.session_store)

    if has_non_repo or not repos:
        rw = RunStatusWatcher(global_conn_factory, global_link_store, platforms_by_name)
        cwt = ClarifyWatcher(
            ChatTaskStore(cfg.storage_root), platforms_by_name,
            global_session_store, str(cfg.storage_root),
        )
        watchers.append((rw, cwt))
        resume_stores.append(global_session_store)

    def _post_poll():
        for rw, cwt in watchers:
            rw.check_once()
            cwt.check_once()

    class _ResumeFanout:
        """Daemon calls mark_recent_resume_pending() once; fan it out to every store."""
        def mark_recent_resume_pending(self):
            for s in resume_stores:
                try:
                    s.mark_recent_resume_pending()
                except Exception:  # noqa: BLE001
                    logger.exception("gateway: resume-pending mark failed")

    daemon = GatewayDaemon(
        factory=factory, platforms=registry.adapters(), home=assistant_home(),
        session_store=_ResumeFanout(),
        poll_timeout=poll_timeout,
        post_poll_hook=_post_poll,
    )
    daemon._project_registry = project_registry  # closed in gateway_cmd finally
    return daemon
```

Add at module top (after `import typer`):

```python
import logging
logger = logging.getLogger(__name__)
```

- [ ] **Step 4: Close the registry on daemon exit**

In `gateway_cmd`, wrap the `daemon.run(...)` call so the registry is closed on exit:

```python
    daemon = build_gateway(cfg, poll_timeout=poll_timeout)
    if daemon is None:
        typer.echo("No gateway platform enabled (set AI_DEV_TELEGRAM_TOKEN).", err=True)
        raise typer.Exit(1)
    try:
        daemon.run(max_iterations=1 if once else (max_iterations or None))
    finally:
        reg = getattr(daemon, "_project_registry", None)
        if reg is not None:
            reg.close_all()
    raise typer.Exit(0)
```

- [ ] **Step 5: Run the new test + regression**

Run: `python -m pytest tests/unit/gateway/test_build_gateway_per_project.py -q`
Expected: PASS (two repo bots → two watcher pairs).

Then run the existing gateway/daemon tests to confirm no regression:

Run: `python -m pytest tests/unit/gateway -q tests/unit/test_heartbeat.py -q`
Expected: PASS. (If a pre-existing test asserted exactly one watcher or the old `session_store` object identity, update it to the new per-project wiring — that is an intended behavior change, not a defect.)

- [ ] **Step 6: Commit**

```bash
git add src/ai_dev_system/cli/commands/gateway.py tests/unit/gateway/test_build_gateway_per_project.py
git commit -m "feat(gateway): build_gateway wires per-project registry, watchers, resume fanout"
```

---

### Task 6: Full suite + README test-count bump

**Files:**
- Modify: `README.md` (test count in `## Trạng thái`, only if changed)

- [ ] **Step 1: Run the full suite**

Run: `python -m pytest -q -p no:cacheprovider`
Expected: all pass EXCEPT possibly `test_docs_reconciliation.py::test_readme_test_count_matches_collected_count` (stale count after new tests). No other failures — investigate any (esp. `tests/unit/gateway/*`, `tests/unit/assistant/*`, `tests/unit/harness/*`).

- [ ] **Step 2: Get the live collected count**

Run: `python -m pytest --collect-only -q -p no:cacheprovider` and read the final `N tests collected` line.

- [ ] **Step 3: Update the README count**

In `README.md`, `## Trạng thái` section, set the `- **<N> tests** — …` line to the collected count (currently `1931`; it becomes `1931 + new tests`).

- [ ] **Step 4: Verify reconciliation passes**

Run: `python -m pytest tests/unit/test_docs_reconciliation.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs(readme): bump test count for gateway per-project routing (SP-2)"
```

---

## Self-Review

**Spec coverage:**
- Routing hub `surface→repo→registry` → Task 4 (`for_chat`) + Task 1 (`repo_path_for_label`) ✓
- `ProjectResources` gains link/session/budget → Task 2 ✓
- `AssistantFactory` project-aware + `build_assistant_factory(project_registry=)` → Task 4 ✓
- `make_dev_pipeline_tools` per-project storage/db params + `start`/`phase-b` env injection → Task 3 ✓
- `build_gateway` per-project watcher pairs + global fallback pair + resume fanout + `close_all` → Task 5 ✓
- Non-repo fallback preserved → Tasks 3/4/5 (default-to-config; global watcher pair) ✓
- Non-goals (webui, CLI-direct, migration) → untouched ✓
- Back-compat (defaults, existing tests) → Tasks 3/4 regression steps ✓
- README chore → Task 6 ✓

**Placeholder scan:** none — every code step has complete content.

**Type consistency:** `repo_path_for_label(telegram_bots, label) -> str` used identically in Tasks 1/4/5. `ProjectResources` new fields (`link_store`, `session_store`, `budget`) produced in Task 2 and consumed by name in Tasks 4/5. `make_dev_pipeline_tools(..., storage_root=None, database_url=None)` defined in Task 3 and called with those kwargs in Task 4. `build_assistant_factory(..., project_registry=None)` defined in Task 4 and called in Task 5. `ProjectRegistry.get(repo).{conn_factory,link_store,session_store,paths.storage_root,paths.database_url}` consumed consistently.

**Note for executor:** Tasks 3–5 modify complex existing functions — the implementer must READ the current function bodies before editing (line numbers drift); anchor edits by the quoted code, not line numbers. Existing tests that encode the OLD single-DB wiring may need updates in Task 5 — that is an intended behavior change; update them to the per-project wiring rather than weakening them.
