"""Test that AssistantFactory.for_chat builds dev tools bound to (surface, chat_id).

Chat-binding option chosen: Option A (per-chat runtime).
- for_chat builds a new runtime whose registry includes the chat-bound dev tools.
- The shared pieces (memory, session, budget) remain shared.
- When dev_newproject_start is called via the per-chat registry/runtime, the
  resulting run_links row carries the correct (surface, chat_id).
"""
from __future__ import annotations

import asyncio
import json

import pytest

from ai_dev_system.db.connection import get_connection
from ai_dev_system.db.migrator import apply_schema
from ai_dev_system.assistant.run_links import RunLinkStore


def _make_conn_factory(file_db_url):
    def conn_factory():
        return get_connection(file_db_url)
    return conn_factory


def test_for_chat_dev_tool_links_to_correct_surface_and_chat(file_db_url, tmp_path):
    """A run started via the chat-bound dev_newproject_start must be linked to
    the (surface, chat_id) that for_chat was called with."""
    from ai_dev_system.assistant.session import SessionStore
    from ai_dev_system.assistant.memory import MemoryStore, assistant_home
    from ai_dev_system.assistant.budget import BudgetTracker
    from ai_dev_system.assistant.factory import AssistantFactory
    from ai_dev_system.harness.tools.registry import ToolRegistry
    from ai_dev_system.harness.tools.builtin import now_tool
    from ai_dev_system.harness.tools.memory_tool import make_memory_tool
    from ai_dev_system.harness.permissions import make_permission_callback
    from ai_dev_system.harness.runtime import SdkAgentRuntime
    from ai_dev_system.config import Config

    conn_factory = _make_conn_factory(file_db_url)
    link_store = RunLinkStore(conn_factory)

    # Minimal config for dev tools
    cfg = Config(
        storage_root=str(tmp_path / "storage"),
        database_url=file_db_url,
        poll_interval_s=0.05,
        heartbeat_interval_s=1.0,
        heartbeat_timeout_s=2.0,
        task_timeout_s=30.0,
    )

    store = MemoryStore(tmp_path / "home")
    base_reg = ToolRegistry()
    base_reg.register(now_tool, "now")
    base_reg.register(make_memory_tool(store), "memory")
    base_runtime = SdkAgentRuntime(
        registry=base_reg, permission_callback=make_permission_callback(), model=None,
    )

    # Seed a runs row so dev_newproject_start can find it immediately
    run_id = "test-run-00000000-0000-0000-0000-000000000001"
    from ai_dev_system.cli.start_project import make_project_id, name_to_slug
    project_id = make_project_id(name_to_slug("MyTestProject"))
    conn = conn_factory()
    conn.execute(
        "INSERT INTO runs (run_id, project_id, status, title, current_artifacts, metadata) "
        "VALUES (?, ?, 'RUNNING_PHASE_1A', 'MyTestProject', '{}', '{}')",
        (run_id, project_id),
    )
    conn.commit()

    # Track spawned argv
    spawned_argvs: list[list[str]] = []

    def fake_spawn(argv, **kwargs):
        spawned_argvs.append(argv)

    factory = AssistantFactory(
        runtime=base_runtime,
        memory_store=store,
        session_store=SessionStore(conn_factory),
        budget=BudgetTracker(conn_factory),
        base_prompt="BASE",
        link_store=link_store,
        config=cfg,
        conn_factory=conn_factory,
        spawn_start=fake_spawn,
    )

    # for_chat("telegram", "42") should build a runtime with dev tools bound to these
    asst = factory.for_chat("telegram", "42")

    # The per-chat runtime should include dev tools in its registry
    tool_names = asst._runtime._registry.allowed_tool_names()
    assert any("dev_newproject_start" in name for name in tool_names), (
        f"Expected dev_newproject_start in per-chat tools, got: {tool_names}"
    )

    # Call the dev_newproject_start tool directly to verify chat binding
    dev_tools = asst._runtime._registry.tools()
    start_tool_fn = None
    for t in dev_tools:
        # SdkMcpTool has a .name attribute holding the tool name
        if getattr(t, "name", None) == "dev_newproject_start":
            start_tool_fn = t
            break
        if hasattr(t, "__name__") and t.__name__ == "dev_newproject_start":
            start_tool_fn = t
            break

    assert start_tool_fn is not None, "dev_newproject_start tool not found in per-chat runtime"

    # SdkMcpTool wraps the async handler; call via .handler
    handler = start_tool_fn.handler
    result = asyncio.run(handler({"project_name": "MyTestProject", "idea": "test idea"}))

    # Check that a run_links row was created with the correct (surface, chat_id)
    link = link_store.lookup(run_id)
    assert link is not None, f"run_links row not found for run_id={run_id!r}"
    assert link.surface == "telegram", f"expected surface=telegram, got {link.surface!r}"
    assert link.chat_id == "42", f"expected chat_id=42, got {link.chat_id!r}"


def test_for_chat_without_link_store_still_works(tmp_path, conn):
    """When link_store=None, for_chat returns an Assistant with only base tools (no dev tools).
    Existing tests / REPL callers must not break."""
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
    base_reg = ToolRegistry()
    base_reg.register(now_tool, "now")
    base_reg.register(make_memory_tool(store), "memory")
    base_runtime = SdkAgentRuntime(
        registry=base_reg, permission_callback=make_permission_callback(), model=None,
    )

    factory = AssistantFactory(
        runtime=base_runtime,
        memory_store=store,
        session_store=SessionStore(lambda: conn),
        budget=BudgetTracker(lambda: conn),
        base_prompt="BASE",
        # No link_store, config, conn_factory → dev tools not added
    )

    asst = factory.for_chat("local", "cli")
    tool_names = asst._runtime._registry.allowed_tool_names()
    # Should only have base tools; no dev tools
    assert not any("dev_newproject_start" in name for name in tool_names), (
        f"Unexpected dev tools when link_store is None: {tool_names}"
    )
    # Should still have base tools
    assert any("now" in name for name in tool_names)
    assert any("memory" in name for name in tool_names)


def test_for_chat_runtime_is_per_chat_not_shared(file_db_url, tmp_path):
    """Each for_chat call must produce a separate runtime (per-chat) so tool
    registries don't bleed between chats."""
    from ai_dev_system.assistant.session import SessionStore
    from ai_dev_system.assistant.memory import MemoryStore
    from ai_dev_system.assistant.budget import BudgetTracker
    from ai_dev_system.assistant.factory import AssistantFactory
    from ai_dev_system.harness.tools.registry import ToolRegistry
    from ai_dev_system.harness.tools.builtin import now_tool
    from ai_dev_system.harness.tools.memory_tool import make_memory_tool
    from ai_dev_system.harness.permissions import make_permission_callback
    from ai_dev_system.harness.runtime import SdkAgentRuntime
    from ai_dev_system.config import Config

    conn_factory = _make_conn_factory(file_db_url)
    link_store = RunLinkStore(conn_factory)
    cfg = Config(
        storage_root=str(tmp_path / "storage"),
        database_url=file_db_url,
        poll_interval_s=0.05,
        heartbeat_interval_s=1.0,
        heartbeat_timeout_s=2.0,
        task_timeout_s=30.0,
    )

    store = MemoryStore(tmp_path / "home")
    base_reg = ToolRegistry()
    base_reg.register(now_tool, "now")
    base_reg.register(make_memory_tool(store), "memory")
    base_runtime = SdkAgentRuntime(
        registry=base_reg, permission_callback=make_permission_callback(), model=None,
    )

    factory = AssistantFactory(
        runtime=base_runtime,
        memory_store=store,
        session_store=SessionStore(conn_factory),
        budget=BudgetTracker(conn_factory),
        base_prompt="BASE",
        link_store=link_store,
        config=cfg,
        conn_factory=conn_factory,
    )

    asst1 = factory.for_chat("telegram", "111")
    asst2 = factory.for_chat("telegram", "222")

    # Runtimes must be different per-chat instances
    assert asst1._runtime is not asst2._runtime, "per-chat runtimes must not be shared"
    # But sessions differ between chats (existing behavior)
    assert asst1._session_id != asst2._session_id
