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


def test_build_assistant_factory_shares_one_connection(tmp_path, monkeypatch):
    """build_assistant_factory must open exactly one shared DB connection.
    Store ops (session load_or_create, budget record) must NOT open additional connections.
    Patch get_connection at the source module so every call via local import is counted."""
    import ai_dev_system.db.connection as _connmod
    from ai_dev_system.db.connection import get_connection as _real_get_connection
    monkeypatch.setenv("AI_DEV_ASSISTANT_HOME", str(tmp_path / "home"))
    db_path = tmp_path / "ctl.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    opened = []

    def counting_get_connection(url):
        conn = _real_get_connection(url)
        opened.append(conn)
        return conn

    monkeypatch.setattr(_connmod, "get_connection", counting_get_connection)

    from ai_dev_system.assistant.factory import build_assistant_factory
    f = build_assistant_factory(model=None)
    connections_after_build = len(opened)

    # calling for_chat exercises session_store.load_or_create + budget — must not open more
    f.for_chat("telegram", "100")
    f.for_chat("telegram", "200")

    assert len(opened) == connections_after_build, (
        f"store ops opened {len(opened) - connections_after_build} extra connections "
        f"(total opened={len(opened)}, after_build={connections_after_build})"
    )
    assert connections_after_build == 1, (
        f"build_assistant_factory opened {connections_after_build} connections, expected 1"
    )
