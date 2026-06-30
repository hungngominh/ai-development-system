from types import SimpleNamespace
from ai_dev_system.gateway.registry import PlatformRegistry
from ai_dev_system.config import TelegramBotConfig


def _cfg(token=None, ids=()):
    return SimpleNamespace(telegram_token=token, telegram_allowed_chat_ids=ids)


def _cfg_with_bots(bots=(), token=None, ids=()):
    return SimpleNamespace(telegram_bots=bots, telegram_token=token, telegram_allowed_chat_ids=ids)


def test_disabled_when_no_token():
    reg = PlatformRegistry.from_config(_cfg(token=None))
    assert reg.enabled() is False
    assert reg.adapters() == []


def test_enabled_with_token():
    reg = PlatformRegistry.from_config(_cfg(token="123:abc", ids=(111,)), transport=lambda *a: b"{}")
    assert reg.enabled() is True
    assert [a.name for a in reg.adapters()] == ["telegram"]


# --- Task 3: multi-bot tests ---

def test_multi_bot_builds_one_adapter_per_bot():
    """telegram_bots=(projA, projB) → 2 adapters with those names, enabled."""
    bots = (
        TelegramBotConfig("projA", "TA", (1,)),
        TelegramBotConfig("projB", "TB", (2,)),
    )
    cfg = _cfg_with_bots(bots=bots, token=None)
    reg = PlatformRegistry.from_config(cfg, transport=lambda *a: b"{}")
    assert reg.enabled() is True
    by_name = {a.name: a for a in reg.adapters()}
    assert set(by_name) == {"projA", "projB"}
    # Per-bot isolation: each adapter carries ITS OWN token + allowlist (no cross-wiring).
    assert by_name["projA"]._token == "TA" and by_name["projA"]._allowed == {1}
    assert by_name["projB"]._token == "TB" and by_name["projB"]._allowed == {2}


def test_single_token_fallback_when_no_telegram_bots():
    """No telegram_bots but telegram_token set → 1 adapter named 'telegram' (back-compat)."""
    cfg = _cfg_with_bots(bots=(), token="T", ids=())
    reg = PlatformRegistry.from_config(cfg, transport=lambda *a: b"{}")
    assert reg.enabled() is True
    assert len(reg.adapters()) == 1
    assert reg.adapters()[0].name == "telegram"


def test_neither_token_nor_bots_gives_zero_adapters():
    """No telegram_bots and no telegram_token → 0 adapters, disabled."""
    cfg = _cfg_with_bots(bots=(), token=None)
    reg = PlatformRegistry.from_config(cfg)
    assert reg.enabled() is False
    assert reg.adapters() == []
