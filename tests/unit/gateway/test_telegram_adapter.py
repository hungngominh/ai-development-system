from ai_dev_system.gateway.base import Inbound
from ai_dev_system.gateway.platforms.telegram import TelegramAdapter


def _transport_seq(batches):
    it = iter(batches)

    def _t(url, data, timeout):
        import json
        try:
            result = next(it)
        except StopIteration:
            result = []
        return json.dumps({"ok": True, "result": result}).encode()

    return _t


def _adapter(transport, allowed=(111,)):
    sent = []
    a = TelegramAdapter(token="TOK", allowed_chat_ids=allowed, transport=transport,
                        sender=lambda token, chat_id, text, transport=None: sent.append((chat_id, text)))
    return a, sent


def test_poll_returns_inbound_for_allowed_text():
    upd = [{"update_id": 7, "message": {"chat": {"id": 111}, "from": {"id": 111}, "text": "hi"}}]
    a, _ = _adapter(_transport_seq([upd]))
    out = a.poll(timeout_s=0)
    assert out == [Inbound(surface="telegram", chat_id=111, text="hi")]


def test_poll_drops_disallowed_chat_and_nontext():
    upd = [
        {"update_id": 1, "message": {"chat": {"id": 999}, "text": "blocked"}},   # not allowed
        {"update_id": 2, "message": {"chat": {"id": 111}}},                       # no text
        {"update_id": 3, "message": {"chat": {"id": 111}, "text": "ok"}},         # allowed
    ]
    a, _ = _adapter(_transport_seq([upd]))
    out = a.poll(timeout_s=0)
    assert out == [Inbound(surface="telegram", chat_id=111, text="ok")]
    assert a._offset == 4  # offset advanced past ALL updates incl. the dropped ones (no wedge)


def test_offset_advances_no_replay():
    upd = [{"update_id": 5, "message": {"chat": {"id": 111}, "text": "one"}}]
    a, _ = _adapter(_transport_seq([upd, []]))
    first = a.poll(timeout_s=0)
    second = a.poll(timeout_s=0)
    assert [m.text for m in first] == ["one"]
    assert second == []                 # offset advanced past update 5
    assert a._offset == 6


def test_empty_allowlist_denies_all():
    upd = [{"update_id": 1, "message": {"chat": {"id": 111}, "text": "hi"}}]
    a, _ = _adapter(_transport_seq([upd]), allowed=())
    assert a.poll(timeout_s=0) == []


def test_reply_sends():
    a, sent = _adapter(_transport_seq([]))
    a.reply(111, "pong")
    assert sent == [(111, "pong")]
