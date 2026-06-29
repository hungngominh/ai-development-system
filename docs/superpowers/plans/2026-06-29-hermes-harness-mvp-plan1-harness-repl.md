# Hermes + Harness MVP — Plan 1: Owned Harness + Local REPL — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up an *owned* agentic tool-use loop via the Claude Agent SDK (on the Max subscription, zero API cost), reachable from a local terminal REPL, with one custom in-process tool and a permission gate — proving the load-bearing "Agent SDK on Max with custom tools + permissions" assumption before anything else is built on it.

**Architecture:** A thin `harness/` package wraps `claude-agent-sdk`. `SdkAgentRuntime.run_turn()` builds `ClaudeAgentOptions` (system prompt + an in-process MCP server of our tools + a `can_use_tool` permission callback) and consumes the SDK's streamed messages, reducing them to a `TurnResult`. A sync `gateway/local_cli.py` REPL drives it. The SDK orchestrates the per-turn loop and invokes our tool functions in-process; we own the tools, permissions, prompt, and result reduction. Everything is unit-tested with a duck-typed fake SDK (no network); a final manual smoke test exercises the real SDK on Max.

**Tech Stack:** Python ≥3.11, `claude-agent-sdk` (new dep), `typer` (existing CLI), `pytest` + `pytest-mock` (existing). Async tested via `asyncio.run` (no `pytest-asyncio`).

## Plan sequence (this is Plan 1 of 7)

Each plan produces working, testable software on its own. Order de-risks highest-uncertainty first.

1. **Plan 1 (this doc) — Owned harness + local REPL.** Prove SDK-on-Max + custom tool + permission gate.
2. **Plan 2 — Memory + sessions + budget.** `MemoryStore` (MEMORY.md/USER.md inject + `memory` tool), `SessionStore` (SQLite transcript + crash-resume), `BudgetTracker`, multi-turn history window.
3. **Plan 3 — Telegram surface + gateway daemon.** `Platform` ABC, `PlatformRegistry`, Telegram long-poll, `gateway/run.py` daemon + clean-shutdown lifecycle, `chat_id` allowlist.
4. **Plan 4 — Single-task tools + spec→plan→exec refactor.** Extract `_build_task_graph` into a reviewable plan step; `dev_singletask_*` tools.
5. **Plan 5 — New-project tools + reactive push.** `dev_intake_*` (drive `IntakeState`), `dev_newproject_start`/`dev_run_status`/`dev_answer_gate` (gate-aware), `run_links`, `RunStatusWatcher`.
6. **Plan 6 — Spec self-review critic.** `spec/self_review.py` (4 superpowers dimensions) wired into both flows.
7. **Plan 7 — Discord fast-follow.** Second `Platform` adapter on the same ABC.

Spec: [`docs/superpowers/specs/2026-06-29-hermes-harness-internal-mvp-design.md`](../specs/2026-06-29-hermes-harness-internal-mvp-design.md).

## Global Constraints

- **Python ≥ 3.11** (matches `pyproject.toml` `requires-python = ">=3.11"`).
- **Max subscription auth:** the runtime must work with `claude login` and **no** `ANTHROPIC_API_KEY` in the process env. If `ANTHROPIC_API_KEY` is set, the SDK switches to paid API billing — never set it for the assistant.
- **In-process tools only** in v1 — registered via `create_sdk_mcp_server`; exposed to the model as `mcp__ai_dev__<toolname>`.
- **No `pytest-asyncio`** — test async functions with `asyncio.run(...)`.
- **Follow the existing CLI pattern:** commands use the `@command(...)` decorator from `ai_dev_system.cli.core.registry` and are imported in `ai_dev_system/cli/commands/__init__.py`.
- **Server key is the literal `"ai_dev"`** everywhere (mcp_servers dict key, `create_sdk_mcp_server(name=...)`, and the `mcp__ai_dev__` allowed-tool prefix) — they must match.
- Source under `src/ai_dev_system/`, tests under `tests/unit/`.

---

### Task 1: Add the SDK dependency + the `now` proof tool

**Files:**
- Modify: `pyproject.toml` (dependencies list)
- Create: `src/ai_dev_system/harness/__init__.py`
- Create: `src/ai_dev_system/harness/tools/__init__.py`
- Create: `src/ai_dev_system/harness/tools/builtin.py`
- Test: `tests/unit/harness/test_builtin_tools.py`

**Interfaces:**
- Produces: `now_tool` — an `SdkMcpTool` created by `@tool("now", ...)`; its handler is `async (args: dict) -> dict` returning `{"content": [{"type": "text", "text": <iso-8601 utc>}]}`.

- [ ] **Step 1: Add the dependency**

In `pyproject.toml`, add `claude-agent-sdk` to `[project].dependencies`:

```toml
dependencies = [
    # SQLite is stdlib — no DB driver dependency
    "anthropic>=0.25",
    "openai>=1.30",
    "crewai>=0.51",
    "python-dotenv>=1.0",
    "typer>=0.12",
    "rich>=13",
    "pyyaml>=6.0",
    "claude-agent-sdk>=0.1",
]
```

- [ ] **Step 2: Install it and smoke-import**

Run: `pip install -e . && python -c "import claude_agent_sdk; from claude_agent_sdk import tool, create_sdk_mcp_server; print('ok')"`
Expected: prints `ok` (confirms the package name and the two symbols exist).

- [ ] **Step 3: Write the failing test**

Create `tests/unit/harness/test_builtin_tools.py`:

```python
import asyncio
from datetime import datetime

from ai_dev_system.harness.tools.builtin import now_tool


def _call(sdk_tool, args):
    # SdkMcpTool stores its async handler; invoke it directly for unit testing.
    return asyncio.run(sdk_tool.handler(args))


def test_now_tool_returns_iso8601_utc_text():
    result = _call(now_tool, {})
    assert "content" in result
    text = result["content"][0]["text"]
    # Must parse as an ISO-8601 timestamp and carry timezone info (UTC).
    parsed = datetime.fromisoformat(text)
    assert parsed.tzinfo is not None
```

- [ ] **Step 4: Run test to verify it fails**

Run: `pytest tests/unit/harness/test_builtin_tools.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ai_dev_system.harness'`.

- [ ] **Step 5: Create the package + tool**

Create `src/ai_dev_system/harness/__init__.py`:

```python
"""ai-dev owned agent harness (Claude Agent SDK wrapper)."""
```

Create `src/ai_dev_system/harness/tools/__init__.py`:

```python
"""In-process tools exposed to the assistant's owned tool-use loop."""
```

Create `src/ai_dev_system/harness/tools/builtin.py`:

```python
"""Trivial built-in tools. `now` is the proof tool: it shows the model can
invoke an in-process tool through the SDK loop and we can dispatch it locally."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from claude_agent_sdk import tool


@tool("now", "Return the current time in ISO-8601 (UTC).", {})
async def now_tool(args: dict[str, Any]) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": datetime.now(timezone.utc).isoformat()}]}
```

> Note: if `SdkMcpTool` exposes the handler under a different attribute than `.handler`, adjust the test's `_call` helper to match (the implementation code is unaffected). Confirm with `python -c "from ai_dev_system.harness.tools.builtin import now_tool; print([a for a in dir(now_tool) if not a.startswith('__')])"`.

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/unit/harness/test_builtin_tools.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/ai_dev_system/harness/__init__.py src/ai_dev_system/harness/tools/__init__.py src/ai_dev_system/harness/tools/builtin.py tests/unit/harness/test_builtin_tools.py
git commit -m "feat(harness): add claude-agent-sdk dep + now proof tool"
```

---

### Task 2: ToolRegistry

**Files:**
- Create: `src/ai_dev_system/harness/tools/registry.py`
- Test: `tests/unit/harness/test_registry.py`

**Interfaces:**
- Consumes: `now_tool` (Task 1).
- Produces: `SERVER_NAME = "ai_dev"`; `ToolRegistry` with `register(tool, name: str) -> None`, `tools() -> list`, `allowed_tool_names() -> list[str]` (each `"mcp__ai_dev__<name>"`), `build_server() -> McpSdkServerConfig` (via `create_sdk_mcp_server(name="ai_dev", version="0.1.0", tools=...)`).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/harness/test_registry.py`:

```python
from ai_dev_system.harness.tools.registry import ToolRegistry, SERVER_NAME
from ai_dev_system.harness.tools.builtin import now_tool


def test_server_name_is_ai_dev():
    assert SERVER_NAME == "ai_dev"


def test_allowed_tool_names_use_mcp_prefix():
    reg = ToolRegistry()
    reg.register(now_tool, "now")
    assert reg.allowed_tool_names() == ["mcp__ai_dev__now"]


def test_tools_returns_registered_tools():
    reg = ToolRegistry()
    reg.register(now_tool, "now")
    assert reg.tools() == [now_tool]


def test_build_server_returns_a_config_object():
    reg = ToolRegistry()
    reg.register(now_tool, "now")
    server = reg.build_server()
    assert server is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/harness/test_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ai_dev_system.harness.tools.registry'`.

- [ ] **Step 3: Implement the registry**

Create `src/ai_dev_system/harness/tools/registry.py`:

```python
"""Owns the set of in-process tools and builds the SDK MCP server + allow-list."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server

SERVER_NAME = "ai_dev"


@dataclass
class ToolRegistry:
    _tools: list[Any] = field(default_factory=list)
    _names: list[str] = field(default_factory=list)

    def register(self, tool: Any, name: str) -> None:
        self._tools.append(tool)
        self._names.append(name)

    def tools(self) -> list[Any]:
        return list(self._tools)

    def allowed_tool_names(self) -> list[str]:
        return [f"mcp__{SERVER_NAME}__{n}" for n in self._names]

    def build_server(self) -> Any:
        return create_sdk_mcp_server(name=SERVER_NAME, version="0.1.0", tools=self._tools)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/harness/test_registry.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/harness/tools/registry.py tests/unit/harness/test_registry.py
git commit -m "feat(harness): ToolRegistry builds SDK MCP server + allow-list"
```

---

### Task 3: Permission callback

**Files:**
- Create: `src/ai_dev_system/harness/permissions.py`
- Test: `tests/unit/harness/test_permissions.py`

**Interfaces:**
- Produces: `make_permission_callback(extra_allowed: set[str] | None = None) -> CanUseTool`. The returned `async (tool_name, input_data, context) -> PermissionResultAllow | PermissionResultDeny`. v1 policy: ALLOW our own `mcp__ai_dev__*` tools, the read-only built-ins `{"Read","Grep","Glob"}`, and any name in `extra_allowed`; DENY everything else (the CONFIRM/destructive policy lands in a later plan).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/harness/test_permissions.py`:

```python
import asyncio
from types import SimpleNamespace

from ai_dev_system.harness.permissions import make_permission_callback


def _decide(cb, name, inp=None):
    return asyncio.run(cb(name, inp or {}, SimpleNamespace()))


def test_allows_our_mcp_tools():
    cb = make_permission_callback()
    res = _decide(cb, "mcp__ai_dev__now")
    assert res.behavior == "allow"


def test_allows_read_only_builtins():
    cb = make_permission_callback()
    assert _decide(cb, "Read", {"file_path": "/x"}).behavior == "allow"


def test_denies_unlisted_tool():
    cb = make_permission_callback()
    res = _decide(cb, "Bash", {"command": "rm -rf /"})
    assert res.behavior == "deny"
    assert res.message


def test_extra_allowed_is_honored():
    cb = make_permission_callback(extra_allowed={"Bash"})
    assert _decide(cb, "Bash", {"command": "ls"}).behavior == "allow"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/harness/test_permissions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ai_dev_system.harness.permissions'`.

- [ ] **Step 3: Implement the callback**

Create `src/ai_dev_system/harness/permissions.py`:

```python
"""The owned permission gate passed to the SDK as `can_use_tool`.

v1 policy is intentionally narrow: allow our own in-process tools and read-only
built-ins, deny the rest. CONFIRM (destructive-with-confirmation) lands in a
later plan once there is a surface to ask the operator on."""
from __future__ import annotations

from typing import Any

from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

SAFE_PREFIX = "mcp__ai_dev__"
READ_ONLY = {"Read", "Grep", "Glob"}


def make_permission_callback(extra_allowed: set[str] | None = None):
    allowed = set(extra_allowed or ())

    async def can_use_tool(tool_name: str, input_data: dict[str, Any], context: Any):
        if tool_name.startswith(SAFE_PREFIX) or tool_name in READ_ONLY or tool_name in allowed:
            return PermissionResultAllow(updated_input=input_data)
        return PermissionResultDeny(message=f"Tool '{tool_name}' is not allowed by the v1 policy.")

    return can_use_tool
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/harness/test_permissions.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/harness/permissions.py tests/unit/harness/test_permissions.py
git commit -m "feat(harness): v1 permission callback (allow own tools + read-only)"
```

---

### Task 4: TurnResult, message reducer, and FakeAgentRuntime

**Files:**
- Create: `src/ai_dev_system/harness/runtime.py`
- Test: `tests/unit/harness/test_runtime_reduce.py`

**Interfaces:**
- Produces:
  - `TurnEvent(kind: str, data: dict)` — `kind ∈ {"text","tool_use"}`.
  - `TurnResult(final_text: str, events: list[TurnEvent], usage: dict, cost_usd: float | None, session_id: str | None)`.
  - `reduce_messages(messages: Iterable[Any]) -> TurnResult` — duck-typed: a message with `total_cost_usd` is the result; a message with `content` is an assistant message whose blocks with `.text` are text and blocks with `.name`+`.input` are tool_use.
  - `AgentRuntime` (Protocol): `run_turn(system_prompt: str, user_text: str) -> TurnResult`.
  - `FakeAgentRuntime(scripted: TurnResult)` — returns `scripted` from `run_turn`, records calls in `.calls`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/harness/test_runtime_reduce.py`:

```python
from types import SimpleNamespace

from ai_dev_system.harness.runtime import (
    reduce_messages,
    TurnResult,
    TurnEvent,
    FakeAgentRuntime,
)


def _assistant(blocks):
    return SimpleNamespace(content=blocks, usage={"output_tokens": 1})


def _text(t):
    return SimpleNamespace(text=t)


def _tool_use(name, inp):
    return SimpleNamespace(name=name, input=inp)


def _result(**kw):
    base = dict(total_cost_usd=0.01, usage={"input_tokens": 5, "output_tokens": 7},
                session_id="sess-1", result=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_reduce_collects_text_tooluse_and_result():
    messages = [
        _assistant([_tool_use("mcp__ai_dev__now", {})]),
        _assistant([_text("It is noon.")]),
        _result(),
    ]
    out = reduce_messages(messages)
    assert isinstance(out, TurnResult)
    assert out.final_text == "It is noon."
    assert TurnEvent("tool_use", {"name": "mcp__ai_dev__now", "input": {}}) in out.events
    assert out.cost_usd == 0.01
    assert out.usage == {"input_tokens": 5, "output_tokens": 7}
    assert out.session_id == "sess-1"


def test_reduce_joins_multiple_text_blocks():
    out = reduce_messages([_assistant([_text("a"), _text("b")]), _result()])
    assert out.final_text == "a\nb"


def test_fake_runtime_returns_scripted_and_records_calls():
    scripted = TurnResult("hi", [], {}, None, None)
    fake = FakeAgentRuntime(scripted)
    got = fake.run_turn("sys", "hello")
    assert got is scripted
    assert fake.calls == [("sys", "hello")]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/harness/test_runtime_reduce.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ai_dev_system.harness.runtime'`.

- [ ] **Step 3: Implement TurnResult + reducer + fake**

Create `src/ai_dev_system/harness/runtime.py`:

```python
"""The owned agent runtime: builds SDK options, runs the loop, reduces messages.

`reduce_messages` is a pure function (duck-typed over the SDK message shapes) so
the loop's output handling is unit-testable without the SDK or network."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol


@dataclass(frozen=True)
class TurnEvent:
    kind: str          # "text" | "tool_use"
    data: dict[str, Any]


@dataclass
class TurnResult:
    final_text: str
    events: list[TurnEvent]
    usage: dict[str, Any]
    cost_usd: float | None
    session_id: str | None


def reduce_messages(messages: Iterable[Any]) -> TurnResult:
    texts: list[str] = []
    events: list[TurnEvent] = []
    usage: dict[str, Any] = {}
    cost_usd: float | None = None
    session_id: str | None = None
    result_text: str | None = None

    for msg in messages:
        if hasattr(msg, "total_cost_usd"):  # ResultMessage
            cost_usd = getattr(msg, "total_cost_usd", None)
            usage = getattr(msg, "usage", None) or usage
            session_id = getattr(msg, "session_id", None)
            result_text = getattr(msg, "result", None)
            continue
        if hasattr(msg, "content"):  # AssistantMessage
            for block in msg.content:
                if hasattr(block, "text"):
                    texts.append(block.text)
                    events.append(TurnEvent("text", {"text": block.text}))
                elif hasattr(block, "name") and hasattr(block, "input"):
                    events.append(TurnEvent("tool_use", {"name": block.name, "input": block.input}))

    final_text = result_text if result_text else "\n".join(texts)
    return TurnResult(final_text, events, usage, cost_usd, session_id)


class AgentRuntime(Protocol):
    def run_turn(self, system_prompt: str, user_text: str) -> TurnResult: ...


@dataclass
class FakeAgentRuntime:
    scripted: TurnResult
    calls: list[tuple[str, str]] = field(default_factory=list)

    def run_turn(self, system_prompt: str, user_text: str) -> TurnResult:
        self.calls.append((system_prompt, user_text))
        return self.scripted
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/harness/test_runtime_reduce.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/harness/runtime.py tests/unit/harness/test_runtime_reduce.py
git commit -m "feat(harness): TurnResult + duck-typed message reducer + FakeAgentRuntime"
```

---

### Task 5: SdkAgentRuntime (real loop, injectable query)

**Files:**
- Modify: `src/ai_dev_system/harness/runtime.py` (append `SdkAgentRuntime`)
- Test: `tests/unit/harness/test_sdk_runtime.py`

**Interfaces:**
- Consumes: `ToolRegistry` (Task 2), the permission callback (Task 3), `reduce_messages` (Task 4), `claude_agent_sdk.ClaudeAgentOptions`, `claude_agent_sdk.query`.
- Produces: `SdkAgentRuntime(*, registry, permission_callback, model=None, max_turns=20, query_fn=None)` implementing `AgentRuntime`. `query_fn` defaults to `claude_agent_sdk.query` and is injectable for tests (an async generator yielding SDK messages).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/harness/test_sdk_runtime.py`:

```python
from types import SimpleNamespace

from ai_dev_system.harness.runtime import SdkAgentRuntime
from ai_dev_system.harness.tools.registry import ToolRegistry
from ai_dev_system.harness.tools.builtin import now_tool
from ai_dev_system.harness.permissions import make_permission_callback


def _fake_query_factory(captured):
    async def fake_query(*, prompt, options):
        captured["prompt"] = prompt
        captured["options"] = options
        yield SimpleNamespace(content=[SimpleNamespace(text="done")], usage={})
        yield SimpleNamespace(total_cost_usd=0.02,
                              usage={"input_tokens": 1, "output_tokens": 2},
                              session_id="s1", result=None)
    return fake_query


def _runtime(captured):
    reg = ToolRegistry()
    reg.register(now_tool, "now")
    return SdkAgentRuntime(
        registry=reg,
        permission_callback=make_permission_callback(),
        model=None,
        query_fn=_fake_query_factory(captured),
    )


def test_run_turn_reduces_fake_query_output():
    captured = {}
    result = _runtime(captured).run_turn("you are ai-dev", "hi")
    assert result.final_text == "done"
    assert result.cost_usd == 0.02
    assert result.session_id == "s1"


def test_run_turn_passes_prompt_and_builds_options():
    captured = {}
    _runtime(captured).run_turn("you are ai-dev", "what time is it?")
    assert captured["prompt"] == "what time is it?"
    opts = captured["options"]
    assert opts.system_prompt == "you are ai-dev"
    assert opts.allowed_tools == ["mcp__ai_dev__now"]
    assert "ai_dev" in opts.mcp_servers
    assert opts.can_use_tool is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/harness/test_sdk_runtime.py -v`
Expected: FAIL — `ImportError: cannot import name 'SdkAgentRuntime'`.

- [ ] **Step 3: Implement SdkAgentRuntime**

Append to `src/ai_dev_system/harness/runtime.py`:

```python
import asyncio

from claude_agent_sdk import ClaudeAgentOptions, query as _sdk_query

from ai_dev_system.harness.tools.registry import ToolRegistry, SERVER_NAME


class SdkAgentRuntime:
    """Owns the loop via the Claude Agent SDK. The SDK orchestrates the per-turn
    tool-use loop and invokes our in-process tools; we own the tools, the
    permission gate, the system prompt, and the result reduction."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        permission_callback,
        model: str | None = None,
        max_turns: int = 20,
        query_fn=None,
    ) -> None:
        self._registry = registry
        self._permission_callback = permission_callback
        self._model = model
        self._max_turns = max_turns
        self._query_fn = query_fn or _sdk_query

    def run_turn(self, system_prompt: str, user_text: str) -> TurnResult:
        return asyncio.run(self._run_async(system_prompt, user_text))

    async def _run_async(self, system_prompt: str, user_text: str) -> TurnResult:
        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            mcp_servers={SERVER_NAME: self._registry.build_server()},
            allowed_tools=self._registry.allowed_tool_names(),
            can_use_tool=self._permission_callback,
            model=self._model,
            max_turns=self._max_turns,
        )
        messages = [m async for m in self._query_fn(prompt=user_text, options=options)]
        return reduce_messages(messages)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/harness/test_sdk_runtime.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Run the whole harness suite**

Run: `pytest tests/unit/harness/ -v`
Expected: PASS (all harness tests green).

- [ ] **Step 6: Commit**

```bash
git add src/ai_dev_system/harness/runtime.py tests/unit/harness/test_sdk_runtime.py
git commit -m "feat(harness): SdkAgentRuntime wires SDK options + injectable query"
```

---

### Task 6: Local REPL surface

**Files:**
- Create: `src/ai_dev_system/gateway/__init__.py`
- Create: `src/ai_dev_system/gateway/local_cli.py`
- Test: `tests/unit/gateway/test_local_cli.py`

**Interfaces:**
- Consumes: any `AgentRuntime` (Task 4 Protocol) — uses `FakeAgentRuntime` in tests.
- Produces: `run_repl(runtime, system_prompt, *, input_fn=input, output_fn=print) -> None`. Loops: read a line via `input_fn`; on `"exit"`/`"quit"`/EOF, stop; on blank, skip; otherwise call `runtime.run_turn`, print each `tool_use` event as `  [tool] <name>`, then `assistant> <final_text>`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/gateway/test_local_cli.py`:

```python
from ai_dev_system.harness.runtime import FakeAgentRuntime, TurnResult, TurnEvent
from ai_dev_system.gateway.local_cli import run_repl


def _input_seq(lines):
    it = iter(lines)

    def _fn(_prompt=""):
        try:
            return next(it)
        except StopIteration:
            raise EOFError

    return _fn


def test_repl_prints_assistant_reply_then_exits():
    scripted = TurnResult("the time is noon",
                          [TurnEvent("tool_use", {"name": "mcp__ai_dev__now"})],
                          {}, None, None)
    runtime = FakeAgentRuntime(scripted)
    out = []
    run_repl(runtime, "sys", input_fn=_input_seq(["what time is it?", "exit"]),
             output_fn=out.append)
    joined = "\n".join(out)
    assert "[tool] mcp__ai_dev__now" in joined
    assert "assistant> the time is noon" in joined
    assert runtime.calls == [("sys", "what time is it?")]


def test_repl_skips_blank_and_stops_on_eof():
    runtime = FakeAgentRuntime(TurnResult("x", [], {}, None, None))
    out = []
    run_repl(runtime, "sys", input_fn=_input_seq(["   "]), output_fn=out.append)
    # blank skipped, then EOF ends the loop → no run_turn call
    assert runtime.calls == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/gateway/test_local_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ai_dev_system.gateway'`.

- [ ] **Step 3: Implement the REPL**

Create `src/ai_dev_system/gateway/__init__.py`:

```python
"""Surfaces the assistant can be reached on (local REPL now; chat platforms later)."""
```

Create `src/ai_dev_system/gateway/local_cli.py`:

```python
"""Local terminal REPL — the first (dependency-free) surface for the assistant."""
from __future__ import annotations

from ai_dev_system.harness.runtime import AgentRuntime

BANNER = "ai-dev assistant — type 'exit' to quit."
_STOP = {"exit", "quit"}


def run_repl(runtime: AgentRuntime, system_prompt: str, *, input_fn=input, output_fn=print) -> None:
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
        result = runtime.run_turn(system_prompt, text)
        for ev in result.events:
            if ev.kind == "tool_use":
                output_fn(f"  [tool] {ev.data['name']}")
        output_fn(f"assistant> {result.final_text}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/gateway/test_local_cli.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/gateway/__init__.py src/ai_dev_system/gateway/local_cli.py tests/unit/gateway/test_local_cli.py
git commit -m "feat(gateway): local REPL surface driving an AgentRuntime"
```

---

### Task 7: `ai-dev assistant` CLI command

**Files:**
- Create: `src/ai_dev_system/cli/commands/assistant.py`
- Modify: `src/ai_dev_system/cli/commands/__init__.py` (add the import)
- Test: `tests/unit/cli/test_assistant_command.py`

**Interfaces:**
- Consumes: `ToolRegistry`, `now_tool`, `make_permission_callback`, `SdkAgentRuntime`, `run_repl`.
- Produces: `build_assistant() -> tuple[SdkAgentRuntime, str]` (testable wiring helper) and a registered `assistant` command that calls `run_repl(*build_assistant())`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/cli/test_assistant_command.py`:

```python
def test_build_assistant_registers_now_tool():
    from ai_dev_system.cli.commands.assistant import build_assistant
    runtime, system_prompt = build_assistant(model=None)
    assert runtime._registry.allowed_tool_names() == ["mcp__ai_dev__now"]
    assert "assistant" in system_prompt.lower() or "ai-dev" in system_prompt.lower()


def test_assistant_command_is_registered_on_root_app():
    # Importing the commands package triggers @command registration.
    import ai_dev_system.cli.commands  # noqa: F401
    from ai_dev_system.cli.core.registry import get_app

    app = get_app()
    names = {c.name for c in app.registered_commands}
    assert "assistant" in names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/cli/test_assistant_command.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ai_dev_system.cli.commands.assistant'`.

- [ ] **Step 3: Implement the command + wiring helper**

Create `src/ai_dev_system/cli/commands/assistant.py`:

```python
"""ai-dev assistant — launch the conversational assistant (local REPL surface).

Plan 1 scope: a single-turn REPL over the owned harness with the `now` proof tool.
Memory, persistent sessions, budget, and chat surfaces arrive in later plans."""
from __future__ import annotations

import typer

from ai_dev_system.cli.core.registry import command

_SYSTEM_PROMPT = (
    "You are ai-dev's internal assistant. You own your tool-use loop. "
    "When the user asks for the current time, call the 'now' tool and report it."
)


def build_assistant(model: str | None):
    from ai_dev_system.harness.tools.registry import ToolRegistry
    from ai_dev_system.harness.tools.builtin import now_tool
    from ai_dev_system.harness.permissions import make_permission_callback
    from ai_dev_system.harness.runtime import SdkAgentRuntime

    registry = ToolRegistry()
    registry.register(now_tool, "now")
    runtime = SdkAgentRuntime(
        registry=registry,
        permission_callback=make_permission_callback(),
        model=model,
    )
    return runtime, _SYSTEM_PROMPT


@command(verb="assistant", help="Launch the conversational assistant (local REPL).")
def assistant_cmd(
    model: str = typer.Option(None, "--model", help="Model alias (default: SDK/account default)."),
) -> None:
    from ai_dev_system.gateway.local_cli import run_repl

    runtime, system_prompt = build_assistant(model=model)
    run_repl(runtime, system_prompt)
```

In `src/ai_dev_system/cli/commands/__init__.py`, add the import (after the existing `info` import):

```python
from ai_dev_system.cli.commands import assistant  # noqa: F401
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/cli/test_assistant_command.py -v`
Expected: PASS (2 passed).

> If `app.registered_commands` does not expose top-level commands the way the assertion expects (typer internals vary), confirm the real shape with `python -c "from ai_dev_system.cli.main import app; print([c.name for c in app.registered_commands])"` and adjust the assertion to match the actual attribute — the implementation is unaffected.

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/cli/commands/assistant.py src/ai_dev_system/cli/commands/__init__.py tests/unit/cli/test_assistant_command.py
git commit -m "feat(cli): ai-dev assistant command launches the REPL"
```

---

### Task 8: Manual smoke test on Max (the de-risking payoff)

**Files:** none (manual verification + a short note).
- Create: `docs/superpowers/plans/notes/2026-06-29-plan1-smoke.md` (record the result)

This task verifies the one thing unit tests cannot: the **real** Agent SDK driving a tool-use loop on the **Max subscription**, dispatching our in-process `now` tool.

- [ ] **Step 1: Confirm auth env**

Run: `python -c "import os; print('API_KEY_SET=' + str(bool(os.environ.get('ANTHROPIC_API_KEY'))))"`
Expected: `API_KEY_SET=False`. If `True`, unset it for this shell (`unset ANTHROPIC_API_KEY` / remove from `.env`) so the SDK uses the Max subscription, not paid API.

- [ ] **Step 2: Confirm subscription login**

Run: `claude --version` and ensure you have logged in with `claude login` (Max account). (The SDK reuses this auth.)

- [ ] **Step 3: Run the assistant for real**

Run: `ai-dev assistant`
At the `you>` prompt, type: `what time is it right now?`
Expected:
- a `  [tool] mcp__ai_dev__now` line appears (the model invoked our in-process tool through the SDK loop), and
- an `assistant>` reply containing a current ISO-8601 timestamp.
Then type `exit`.

- [ ] **Step 4: Record the result**

Create `docs/superpowers/plans/notes/2026-06-29-plan1-smoke.md` with: date, that the tool line + timestamp appeared, the model used, and anything surprising (latency, auth prompts, ToS notices). If it failed, capture the error verbatim — this is the signal for whether the SDK-on-Max + custom-tool assumption holds before building Plans 2–7 on it.

- [ ] **Step 5: Run the full test suite**

Run: `pytest -q`
Expected: the whole suite is green (no regressions; ~1618 baseline + the new harness/gateway/cli tests).

- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/plans/notes/2026-06-29-plan1-smoke.md
git commit -m "docs(harness): record Plan 1 Max smoke-test result"
```

---

## Self-Review

**Spec coverage (Plan 1 portion):** owned harness via Agent SDK ✅ (Tasks 1–5); in-process custom tool ✅ (Task 1, registry Task 2); permission `can_use_tool` ✅ (Task 3); local REPL surface ✅ (Task 6); `ai-dev assistant` entry ✅ (Task 7); Max-auth (no `ANTHROPIC_API_KEY`) honored + verified ✅ (Global Constraints + Task 8); FakeAgentRuntime test pattern ✅ (Task 4). Memory, sessions, budget, Telegram, pipeline tools, intake, notifier, spec self-review, single-task refactor are **explicitly Plans 2–7**, not gaps in this slice.

**Placeholder scan:** every code step shows complete code; commands have expected output; the two "if the SDK attribute differs, confirm with …" notes are real fallbacks for genuine SDK-introspection uncertainty, not deferred work.

**Type consistency:** `SERVER_NAME="ai_dev"` used in `registry.build_server()`, `allowed_tool_names()` (`mcp__ai_dev__*`), and `SdkAgentRuntime` `mcp_servers={SERVER_NAME: ...}` — consistent. `run_turn(system_prompt, user_text) -> TurnResult` identical across `AgentRuntime` Protocol, `FakeAgentRuntime`, `SdkAgentRuntime`. `TurnResult`/`TurnEvent` field names match across reducer, REPL, and tests. `build_assistant(model)` returns `(runtime, system_prompt)`, consumed positionally by `run_repl` in `assistant_cmd`.

**Known SDK-introspection risks (flagged, not deferred):** `SdkMcpTool.handler` attribute name (Task 1) and `typer.Typer.registered_commands` shape (Task 7) — each has an inline `python -c` confirmation step; both affect only test-helper lines, never implementation.
