import asyncio
from datetime import datetime, timedelta

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
    assert parsed.utcoffset() == timedelta(0)
