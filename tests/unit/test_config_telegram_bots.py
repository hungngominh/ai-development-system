from __future__ import annotations

import json

import pytest

from ai_dev_system.config import Config, TelegramBotConfig


@pytest.fixture(autouse=True)
def _clear(monkeypatch):
    for k in ("AI_DEV_TELEGRAM_BOTS", "AI_DEV_TELEGRAM_TOKEN", "AI_DEV_TELEGRAM_ALLOWED_CHAT_IDS"):
        monkeypatch.delenv(k, raising=False)


def test_no_telegram_config_no_bots(monkeypatch):
    assert Config.from_env().telegram_bots == ()


def test_single_token_backcompat_one_bot(monkeypatch):
    monkeypatch.setenv("AI_DEV_TELEGRAM_TOKEN", "TOK1")
    monkeypatch.setenv("AI_DEV_TELEGRAM_ALLOWED_CHAT_IDS", "111, 222")
    bots = Config.from_env().telegram_bots
    assert bots == (TelegramBotConfig(label="telegram", token="TOK1",
                                      allowed_chat_ids=(111, 222)),)


def test_multi_bot_json(monkeypatch):
    monkeypatch.setenv("AI_DEV_TELEGRAM_BOTS", json.dumps([
        {"label": "projA", "token": "TA", "chat_ids": [111]},
        {"label": "projB", "token": "TB", "chat_ids": [222, 333]},
    ]))
    bots = Config.from_env().telegram_bots
    assert [b.label for b in bots] == ["projA", "projB"]
    assert bots[0] == TelegramBotConfig(label="projA", token="TA", allowed_chat_ids=(111,))
    assert bots[1].allowed_chat_ids == (222, 333)


def test_malformed_bots_json_falls_back_to_single_token(monkeypatch):
    monkeypatch.setenv("AI_DEV_TELEGRAM_BOTS", "{ not json")
    monkeypatch.setenv("AI_DEV_TELEGRAM_TOKEN", "TOK1")
    bots = Config.from_env().telegram_bots
    assert len(bots) == 1 and bots[0].label == "telegram" and bots[0].token == "TOK1"


def test_bots_json_takes_precedence_over_single_token(monkeypatch):
    monkeypatch.setenv("AI_DEV_TELEGRAM_TOKEN", "LEGACY")
    monkeypatch.setenv("AI_DEV_TELEGRAM_BOTS", json.dumps([{"label": "p", "token": "T", "chat_ids": []}]))
    bots = Config.from_env().telegram_bots
    assert [b.label for b in bots] == ["p"]


def test_entry_missing_token_or_label_skipped(monkeypatch):
    monkeypatch.setenv("AI_DEV_TELEGRAM_BOTS", json.dumps([
        {"label": "ok", "token": "T", "chat_ids": [1]},
        {"label": "", "token": "T2"},          # no label → skipped
        {"label": "x"},                          # no token → skipped
    ]))
    bots = Config.from_env().telegram_bots
    assert [b.label for b in bots] == ["ok"]
