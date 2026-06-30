import pytest

from ai_dev_system.cli import telegram_setup as ts


def test_extract_chat_id_from_message():
    updates = [
        {"update_id": 1, "message": {"chat": {"id": 5913726934},
                                     "from": {"username": "ngomi"}, "text": "hi"}}
    ]
    assert ts.extract_chat_id(updates) == (5913726934, "ngomi")


def test_extract_chat_id_returns_none_when_no_message():
    assert ts.extract_chat_id([]) is None
    assert ts.extract_chat_id([{"update_id": 1}]) is None


def test_upsert_into_empty_bots_line():
    env = "LLM_PROVIDER=claude_code\nAI_DEV_TELEGRAM_BOTS=[]\n"
    out = ts.upsert_bot_in_env(env, "my-app", "TOK", [42])
    assert 'AI_DEV_TELEGRAM_BOTS=[{"label": "my-app", "token": "TOK", "chat_ids": [42]}]' in out
    assert "LLM_PROVIDER=claude_code" in out  # other lines preserved


def test_upsert_appends_to_existing_bot():
    env = 'AI_DEV_TELEGRAM_BOTS=[{"label": "a", "token": "T1", "chat_ids": [1]}]\n'
    out = ts.upsert_bot_in_env(env, "b", "T2", [2])
    assert '"label": "a"' in out and '"label": "b"' in out
    # still one line for the key
    assert sum(1 for ln in out.splitlines() if ln.startswith("AI_DEV_TELEGRAM_BOTS=")) == 1


def test_upsert_adds_key_when_missing():
    env = "LLM_PROVIDER=claude_code\n"
    out = ts.upsert_bot_in_env(env, "a", "T1", [1])
    assert "AI_DEV_TELEGRAM_BOTS=" in out
    assert '"label": "a"' in out


def test_upsert_duplicate_label_raises():
    env = 'AI_DEV_TELEGRAM_BOTS=[{"label": "a", "token": "T1", "chat_ids": [1]}]\n'
    with pytest.raises(ValueError, match="a"):
        ts.upsert_bot_in_env(env, "a", "T2", [2])
