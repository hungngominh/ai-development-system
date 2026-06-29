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
        return create_sdk_mcp_server(name=SERVER_NAME, version="0.1.0", tools=list(self._tools))
