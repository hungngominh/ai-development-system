"""The owned agent runtime: builds SDK options, runs the loop, reduces messages.

`reduce_messages` is a pure function (duck-typed over the SDK message shapes) so
the loop's output handling is unit-testable without the SDK or network."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol


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
