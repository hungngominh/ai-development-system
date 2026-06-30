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
        seen: set[str] = set()
        for bot in bots:
            # The label IS the routing surface — a duplicate would silently collapse
            # in build_gateway's platforms_by_name (last-wins) and misroute one
            # project's gate/terminal pushes through the other bot's token. Fail loud
            # so the operator fixes the typo instead of leaking notifications.
            if bot.label in seen:
                raise ValueError(
                    f"duplicate Telegram bot label {bot.label!r} in AI_DEV_TELEGRAM_BOTS — "
                    "each bot needs a unique label (it is the per-project routing surface)."
                )
            seen.add(bot.label)
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
