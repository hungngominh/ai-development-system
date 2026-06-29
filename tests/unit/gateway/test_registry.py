from types import SimpleNamespace
from ai_dev_system.gateway.registry import PlatformRegistry


def _cfg(token=None, ids=()):
    return SimpleNamespace(telegram_token=token, telegram_allowed_chat_ids=ids)


def test_disabled_when_no_token():
    reg = PlatformRegistry.from_config(_cfg(token=None))
    assert reg.enabled() is False
    assert reg.adapters() == []


def test_enabled_with_token():
    reg = PlatformRegistry.from_config(_cfg(token="123:abc", ids=(111,)), transport=lambda *a: b"{}")
    assert reg.enabled() is True
    assert [a.name for a in reg.adapters()] == ["telegram"]
