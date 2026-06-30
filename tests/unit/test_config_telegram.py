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


def test_bot_parses_repo_path_and_base_branch(monkeypatch):
    from ai_dev_system.config import Config
    monkeypatch.setenv(
        "AI_DEV_TELEGRAM_BOTS",
        '[{"label":"my-app","token":"T","chat_ids":[1],'
        '"repo_path":"/repos/my-app","base_branch":"main"}]',
    )
    cfg = Config.from_env()
    bot = cfg.telegram_bots[0]
    assert bot.repo_path == "/repos/my-app"
    assert bot.base_branch == "main"


def test_bot_without_repo_fields_defaults_empty(monkeypatch):
    from ai_dev_system.config import Config
    monkeypatch.setenv(
        "AI_DEV_TELEGRAM_BOTS", '[{"label":"x","token":"T","chat_ids":[1]}]'
    )
    cfg = Config.from_env()
    assert cfg.telegram_bots[0].repo_path == ""
    assert cfg.telegram_bots[0].base_branch == ""
