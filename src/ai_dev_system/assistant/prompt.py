"""Assembles the system prompt (base persona + memory) and renders the recent-history
window into the user turn. Multi-turn context is carried here, from durable storage,
so the harness stays stateless per turn."""
from __future__ import annotations

from ai_dev_system.assistant.memory import Memory
from ai_dev_system.assistant.session import Turn


def build_system_prompt(base: str, mem: Memory) -> str:
    parts = [base.rstrip()]
    if mem.agent.strip():
        parts.append("## What you remember\n" + mem.agent.strip())
    if mem.user.strip():
        parts.append("## About the operator\n" + mem.user.strip())
    return "\n\n".join(parts)


def render_user_turn(history: list[Turn], message: str) -> str:
    if not history:
        return message
    lines = []
    for t in history:
        label = "User" if t.role == "user" else "Assistant"
        lines.append(f"{label}: {t.content}")
    return (
        "Conversation so far:\n" + "\n".join(lines)
        + "\n\nNow reply to this message:\n" + message
    )
