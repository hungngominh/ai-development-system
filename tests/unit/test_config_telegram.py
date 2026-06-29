import pytest
from ai_dev_system.config import Config


@pytest.fixture(autouse=True)
def _clear(monkeypatch):
    monkeypatch.delenv("AI_DEV_TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("AI_DEV_TELEGRAM_ALLOWED_CHAT_IDS", raising=False)


def test_defaults_when_unset():
    c = Config.from_env()
    assert c.telegram_token is None
    assert c.telegram_allowed_chat_ids == ()


def test_parses_token_and_ids(monkeypatch):
    monkeypatch.setenv("AI_DEV_TELEGRAM_TOKEN", "123:abc")
    monkeypatch.setenv("AI_DEV_TELEGRAM_ALLOWED_CHAT_IDS", "111, 222  333")
    c = Config.from_env()
    assert c.telegram_token == "123:abc"
    assert c.telegram_allowed_chat_ids == (111, 222, 333)
