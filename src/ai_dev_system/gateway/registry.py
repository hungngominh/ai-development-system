"""Builds the set of enabled gateway platforms from Config (a platform is enabled
iff its credential is set)."""
from __future__ import annotations


class PlatformRegistry:
    def __init__(self, adapters) -> None:
        self._adapters = list(adapters)

    def enabled(self) -> bool:
        return bool(self._adapters)

    def adapters(self) -> list:
        return list(self._adapters)

    @classmethod
    def from_config(cls, cfg, *, transport=None, sender=None) -> "PlatformRegistry":
        from ai_dev_system.gateway.platforms.telegram import TelegramAdapter
        adapters = []
        bots = getattr(cfg, "telegram_bots", ()) or ()
        for bot in bots:
            adapters.append(TelegramAdapter(
                name=bot.label, token=bot.token, allowed_chat_ids=bot.allowed_chat_ids,
                transport=transport, sender=sender,
            ))
        if not adapters and getattr(cfg, "telegram_token", None):
            adapters.append(TelegramAdapter(
                token=cfg.telegram_token,
                allowed_chat_ids=getattr(cfg, "telegram_allowed_chat_ids", ()),
                transport=transport, sender=sender,
            ))
        return cls(adapters)
