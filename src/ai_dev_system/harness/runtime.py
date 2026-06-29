"""The owned agent runtime: builds SDK options, runs the loop, reduces messages.

`reduce_messages` is a pure function (duck-typed over the SDK message shapes) so
the loop's output handling is unit-testable without the SDK or network."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol

from claude_agent_sdk import ClaudeAgentOptions, query as _sdk_query

from ai_dev_system.harness.tools.registry import ToolRegistry, SERVER_NAME


@dataclass(frozen=True)
class TurnEvent:
    kind: str          # "text" | "tool_use"
    data: dict[str, Any]


@dataclass
class TurnResult:
    final_text: str
    events: list[TurnEvent]
    usage: dict[str, Any]
    cost_usd: float | None
    session_id: str | None


def reduce_messages(messages: Iterable[Any]) -> TurnResult:
    texts: list[str] = []
    events: list[TurnEvent] = []
    usage: dict[str, Any] = {}
    cost_usd: float | None = None
    session_id: str | None = None
    result_text: str | None = None

    for msg in messages:
        if hasattr(msg, "total_cost_usd"):  # ResultMessage
            cost_usd = getattr(msg, "total_cost_usd", None)
            usage = getattr(msg, "usage", None) or usage
            session_id = getattr(msg, "session_id", None)
            result_text = getattr(msg, "result", None)
            continue
        if hasattr(msg, "content"):  # AssistantMessage
            for block in msg.content:
                if hasattr(block, "text"):
                    texts.append(block.text)
                    events.append(TurnEvent("text", {"text": block.text}))
                elif hasattr(block, "name") and hasattr(block, "input"):
                    events.append(TurnEvent("tool_use", {"name": block.name, "input": block.input}))

    final_text = result_text if result_text else "\n".join(texts)
    return TurnResult(final_text, events, usage, cost_usd, session_id)


class AgentRuntime(Protocol):
    def run_turn(self, system_prompt: str, user_text: str) -> TurnResult: ...


@dataclass
class FakeAgentRuntime:
    scripted: TurnResult
    calls: list[tuple[str, str]] = field(default_factory=list)

    def run_turn(self, system_prompt: str, user_text: str) -> TurnResult:
        self.calls.append((system_prompt, user_text))
        return self.scripted


class SdkAgentRuntime:
    """Owns the loop via the Claude Agent SDK. The SDK orchestrates the per-turn
    tool-use loop and invokes our in-process tools; we own the tools, the
    permission gate, the system prompt, and the result reduction."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        permission_callback,
        model: str | None = None,
        max_turns: int = 20,
        query_fn=None,
    ) -> None:
        self._registry = registry
        self._permission_callback = permission_callback
        self._model = model
        self._max_turns = max_turns
        self._query_fn = query_fn or _sdk_query

    def run_turn(self, system_prompt: str, user_text: str) -> TurnResult:
        """Run one turn synchronously. Must be called from a synchronous context — uses asyncio.run(...) internally and must NOT be called from within a running event loop."""
        return asyncio.run(self._run_async(system_prompt, user_text))

    async def _run_async(self, system_prompt: str, user_text: str) -> TurnResult:
        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            mcp_servers={SERVER_NAME: self._registry.build_server()},
            allowed_tools=self._registry.allowed_tool_names(),
            can_use_tool=self._permission_callback,
            model=self._model,
            max_turns=self._max_turns,
        )
        messages = [m async for m in self._query_fn(prompt=user_text, options=options)]
        return reduce_messages(messages)
