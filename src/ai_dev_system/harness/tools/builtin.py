"""Trivial built-in tools. `now` is the proof tool: it shows the model can
invoke an in-process tool through the SDK loop and we can dispatch it locally."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from claude_agent_sdk import tool


@tool("now", "Return the current time in ISO-8601 (UTC).", {})
async def now_tool(args: dict[str, Any]) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": datetime.now(timezone.utc).isoformat()}]}
