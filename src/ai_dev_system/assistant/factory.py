"""AssistantFactory — builds the shared harness/memory/session/budget pieces once
and hands out a per-(surface, chat_id) Assistant (varying only session_id). Lets a
long-lived gateway daemon serve many chats from one set of shared objects.

Chat-binding option: **Option A — per-chat runtime**.
When link_store/config/conn_factory are provided, for_chat builds a fresh
SdkAgentRuntime whose registry contains both the shared base tools (now, memory)
and the chat-bound dev tools (dev_newproject_start, dev_run_status, dev_answer_gate).
This keeps the tool bindings explicit and testable: each chat has its own runtime so
dev tools know which (surface, chat_id) they serve without using contextvars.

When link_store is None (REPL / old callers / tests without a pipeline), for_chat
reuses the shared base_runtime as before — no dev tools added.
"""
from __future__ import annotations

import os

_SYSTEM_PROMPT = (
    "You are ai-dev's internal assistant. You own your tool-use loop. "
    "Use the 'now' tool for the current time. Use the 'memory' tool to durably "
    "record facts about yourself (MEMORY) or the operator (USER) when worth remembering."
)


class AssistantFactory:
    def __init__(
        self,
        *,
        runtime,
        memory_store,
        session_store,
        budget,
        base_prompt: str,
        cap_usd: float | None = None,
        window: int = 10,
        # Optional extras for chat-bound dev tools (Option A)
        link_store=None,
        config=None,
        conn_factory=None,
        spawn_start=None,
        spawn_phase_b=None,
    ) -> None:
        self._runtime = runtime               # shared base runtime (no dev tools)
        self._memory_store = memory_store
        self._session_store = session_store
        self._budget = budget
        self._base_prompt = base_prompt
        self._cap_usd = cap_usd
        self._window = window
        # Chat-bound dev tool pieces (None → dev tools not added)
        self._link_store = link_store
        self._config = config
        self._conn_factory = conn_factory
        self._spawn_start = spawn_start
        self._spawn_phase_b = spawn_phase_b

    def for_chat(self, surface: str, chat_id: str):
        from ai_dev_system.assistant.agent import Assistant
        session_id = self._session_store.load_or_create(surface, chat_id)

        if self._link_store is not None:
            # Option A: build a per-chat runtime that includes chat-bound dev tools
            runtime = self._build_chat_runtime(surface, chat_id)
        else:
            # No dev tools (REPL / tests without pipeline); reuse shared runtime
            runtime = self._runtime

        return Assistant(
            runtime=runtime, memory_store=self._memory_store,
            session_store=self._session_store, budget=self._budget,
            base_prompt=self._base_prompt, session_id=session_id,
            window=self._window, cap_usd=self._cap_usd,
        )

    def _build_chat_runtime(self, surface: str, chat_id: str):
        """Build a fresh SdkAgentRuntime for this chat that includes dev tools."""
        from ai_dev_system.harness.tools.registry import ToolRegistry
        from ai_dev_system.harness.tools.builtin import now_tool
        from ai_dev_system.harness.tools.memory_tool import make_memory_tool
        from ai_dev_system.harness.permissions import make_permission_callback
        from ai_dev_system.harness.runtime import SdkAgentRuntime
        from ai_dev_system.harness.tools.dev_pipeline import make_dev_pipeline_tools

        reg = ToolRegistry()
        reg.register(now_tool, "now")
        reg.register(make_memory_tool(self._memory_store), "memory")

        dev_tools = make_dev_pipeline_tools(
            surface=surface,
            chat_id=chat_id,
            conn_factory=self._conn_factory,
            config=self._config,
            link_store=self._link_store,
            spawn_start=self._spawn_start,
            spawn_phase_b=self._spawn_phase_b,
        )
        for t in dev_tools:
            # SdkMcpTool exposes .name; fall back to __name__ for other wrappers
            tool_name = getattr(t, "name", None) or getattr(t, "__name__", str(id(t)))
            reg.register(t, tool_name)

        # Reuse the same permission_callback and model from the base runtime
        base = self._runtime
        permission_callback = getattr(base, "_permission_callback", make_permission_callback())
        model = getattr(base, "_model", None)
        max_turns = getattr(base, "_max_turns", 20)
        client_factory = getattr(base, "_client_factory", None)

        kwargs = dict(
            registry=reg,
            permission_callback=permission_callback,
            model=model,
            max_turns=max_turns,
        )
        if client_factory is not None:
            kwargs["client_factory"] = client_factory

        return SdkAgentRuntime(**kwargs)


def build_assistant_factory(
    model: str | None,
    *,
    link_store=None,
    config=None,
    conn_factory=None,
    spawn_start=None,
    spawn_phase_b=None,
) -> AssistantFactory:
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

    cfg = config or Config.from_env()
    shared_conn = get_connection(cfg.database_url)
    apply_schema(shared_conn)

    if conn_factory is None:
        def conn_factory():  # noqa: F811
            return shared_conn

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
        link_store=link_store,
        config=cfg if link_store is not None else None,
        conn_factory=conn_factory if link_store is not None else None,
        spawn_start=spawn_start,
        spawn_phase_b=spawn_phase_b,
    )
