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
