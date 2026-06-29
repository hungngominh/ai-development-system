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
