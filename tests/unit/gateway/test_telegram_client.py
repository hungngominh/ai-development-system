import json
from ai_dev_system.gateway import telegram_client as tc


def _transport_returning(payloads):
    calls = []
    it = iter(payloads)

    def _t(url, data, timeout):
        calls.append((url, data, timeout))
        return json.dumps(next(it)).encode("utf-8")

    return _t, calls


def test_get_updates_passes_offset_and_returns_result():
    payload = {"ok": True, "result": [{"update_id": 5, "message": {"text": "hi"}}]}
    transport, calls = _transport_returning([payload])
    out = tc.get_updates("TOK", offset=5, timeout=10, transport=transport)
    assert out == payload["result"]
    url, data, _ = calls[0]
    assert url.endswith("/botTOK/getUpdates")
    assert b"offset=5" in data and b"timeout=10" in data


def test_send_message_splits_at_4096():
    transport, calls = _transport_returning([{"ok": True, "result": {}}, {"ok": True, "result": {}}])
    tc.send_message("TOK", 42, "x" * 5000, transport=transport)
    assert len(calls) == 2  # 5000 chars -> two chunks (4096 + 904)


def test_call_returns_none_on_timeout():
    import socket

    def _t(url, data, timeout):
        raise socket.timeout()

    assert tc._call("TOK", "getUpdates", {}, transport=_t) is None


def test_call_raises_on_not_ok():
    import pytest

    def _t(url, data, timeout):
        return json.dumps({"ok": False, "description": "Unauthorized"}).encode()

    with pytest.raises(tc.TelegramError):
        tc._call("TOK", "getUpdates", {}, transport=_t)
