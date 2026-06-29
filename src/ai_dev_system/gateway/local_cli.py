"""Local terminal REPL — the first (dependency-free) surface for the assistant."""
from __future__ import annotations

from ai_dev_system.harness.runtime import AgentRuntime

BANNER = "ai-dev assistant — type 'exit' to quit."
_STOP = {"exit", "quit"}


def run_repl(runtime: AgentRuntime, system_prompt: str, *, input_fn=input, output_fn=print) -> None:
    output_fn(BANNER)
    while True:
        try:
            line = input_fn("you> ")
        except EOFError:
            break
        text = line.strip()
        if text.lower() in _STOP:
            break
        if not text:
            continue
        result = runtime.run_turn(system_prompt, text)
        for ev in result.events:
            if ev.kind == "tool_use":
                output_fn(f"  [tool] {ev.data['name']}")
        output_fn(f"assistant> {result.final_text}")
