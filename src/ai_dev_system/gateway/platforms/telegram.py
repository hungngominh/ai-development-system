"""Telegram surface: long-poll getUpdates -> Inbound, reply -> sendMessage.
Owns its own chat-id allowlist (empty allowlist = deny-all)."""
from __future__ import annotations

from typing import Any

from ai_dev_system.gateway.base import Inbound
from ai_dev_system.gateway import telegram_client


class TelegramAdapter:
    name = "telegram"

    def __init__(self, *, token: str, allowed_chat_ids, transport=None, sender=None) -> None:
        self._token = token
        self._allowed = set(allowed_chat_ids or ())
        self._transport = transport
        self._sender = sender or telegram_client.send_message
        self._offset: int | None = None

    def is_allowed(self, chat_id: int) -> bool:
        return chat_id in self._allowed  # empty set -> deny-all

    def poll(self, timeout_s: int) -> list[Inbound]:
        updates = telegram_client.get_updates(
            self._token, offset=self._offset, timeout=timeout_s, transport=self._transport,
        )
        inbound: list[Inbound] = []
        for upd in updates:
            uid = upd.get("update_id")
            if uid is not None:
                self._offset = uid + 1  # advance to ACK
            msg: dict[str, Any] = upd.get("message") or {}
            chat_id = (msg.get("chat") or {}).get("id")
            text = msg.get("text")
            if chat_id is None or not text:
                continue
            if not self.is_allowed(chat_id):
                continue
            inbound.append(Inbound(surface=self.name, chat_id=chat_id, text=text))
        return inbound

    def reply(self, chat_id: int, text: str) -> None:
        self._sender(self._token, chat_id, text, transport=self._transport)
