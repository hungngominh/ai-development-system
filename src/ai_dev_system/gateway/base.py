"""Gateway surface contracts: an Inbound message and the Platform protocol."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Inbound:
    surface: str
    chat_id: int
    text: str


class Platform(Protocol):
    name: str
    def poll(self, timeout_s: int) -> list[Inbound]: ...
    def reply(self, chat_id: int, text: str) -> None: ...
