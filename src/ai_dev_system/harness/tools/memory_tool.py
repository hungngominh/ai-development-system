"""The `memory` tool: lets the assistant durably record facts about itself
(MEMORY.md) or the operator (USER.md) mid-conversation."""
from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from ai_dev_system.assistant.memory import MemoryStore

_SCHEMA = {"target": str, "action": str, "text": str}


def make_memory_tool(store: MemoryStore):
    @tool(
        "memory",
        "Record durable memory. target=MEMORY (facts/conventions) or USER "
        "(operator preferences/style); action=add|replace|remove; text=the line.",
        _SCHEMA,
    )
    async def memory_tool(args: dict[str, Any]) -> dict[str, Any]:
        try:
            store.write(args["target"], args["action"], args["text"])
            msg = f"Saved to {args['target']} ({args['action']})."
        except (ValueError, KeyError) as exc:
            msg = f"memory error: {exc}"
        return {"content": [{"type": "text", "text": msg}]}

    return memory_tool
