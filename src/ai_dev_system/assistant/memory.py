"""Long-term memory: MEMORY.md (agent facts) + USER.md (operator model).

Both files live on disk so they are human-editable and travel with the operator;
they are injected into the system prompt at the start of every turn."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_TARGETS = {"MEMORY": "MEMORY.md", "USER": "USER.md"}
_ACTIONS = {"add", "replace", "remove"}


def assistant_home() -> Path:
    home = os.environ.get("AI_DEV_ASSISTANT_HOME")
    path = Path(home) if home else Path.home() / ".ai-dev-system" / "assistant"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class Memory:
    agent: str  # MEMORY.md contents
    user: str   # USER.md contents


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class MemoryStore:
    def __init__(self, home: Path) -> None:
        self._home = Path(home)
        self._home.mkdir(parents=True, exist_ok=True)

    def _path(self, target: str) -> Path:
        if target not in _TARGETS:
            raise ValueError(f"unknown memory target {target!r} (want MEMORY|USER)")
        return self._home / _TARGETS[target]

    def load(self) -> Memory:
        def _read(name: str) -> str:
            p = self._home / name
            return p.read_text(encoding="utf-8") if p.exists() else ""
        return Memory(agent=_read("MEMORY.md"), user=_read("USER.md"))

    def write(self, target: str, action: str, text: str) -> None:
        if action not in _ACTIONS:
            raise ValueError(f"unknown memory action {action!r}")
        path = self._path(target)
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        if action == "replace":
            new = text.rstrip() + "\n"
        elif action == "add":
            new = (current.rstrip() + "\n" if current.strip() else "") + text.rstrip() + "\n"
        else:  # remove
            kept = [ln for ln in current.splitlines() if ln.strip() != text.strip()]
            new = ("\n".join(kept).rstrip() + "\n") if kept else ""
        _atomic_write(path, new)
