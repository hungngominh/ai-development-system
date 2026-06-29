"""The owned permission gate passed to the SDK as `can_use_tool`.

v1 policy is intentionally narrow: allow our own in-process tools and read-only
built-ins, deny the rest. CONFIRM (destructive-with-confirmation) lands in a
later plan once there is a surface to ask the operator on."""
from __future__ import annotations

from typing import Any

from claude_agent_sdk import CanUseTool, PermissionResultAllow, PermissionResultDeny

SAFE_PREFIX = "mcp__ai_dev__"
READ_ONLY = {"Read", "Grep", "Glob"}


def make_permission_callback(extra_allowed: set[str] | None = None) -> CanUseTool:
    allowed = set(extra_allowed or ())

    async def can_use_tool(tool_name: str, input_data: dict[str, Any], context: Any):
        if tool_name.startswith(SAFE_PREFIX) or tool_name in READ_ONLY or tool_name in allowed:
            return PermissionResultAllow(updated_input=input_data)
        return PermissionResultDeny(message=f"Tool '{tool_name}' is not allowed by the v1 policy.")

    return can_use_tool
