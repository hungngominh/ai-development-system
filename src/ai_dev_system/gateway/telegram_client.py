"""Minimal stdlib Telegram Bot API client (getUpdates long-poll + sendMessage).
The HTTP call goes through an injectable `transport` so tests never hit the network."""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

_API = "https://api.telegram.org"
_MAX_LEN = 4096


class TelegramError(Exception):
    pass


def _default_transport(url: str, data: bytes, timeout: float) -> bytes:
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https host
        return resp.read()


def _call(token, method, params, *, transport=None, poll_timeout: float = 0) -> dict | None:
    transport = transport or _default_transport
    url = f"{_API}/bot{token}/{method}"
    data = urllib.parse.urlencode(params).encode("utf-8")
    # socket timeout must exceed the long-poll timeout so urlopen doesn't tear it down.
    sock_timeout = (poll_timeout + 10) if poll_timeout else 30
    try:
        raw = transport(url, data, sock_timeout)
    except socket.timeout:
        return None
    except urllib.error.HTTPError as exc:  # 4xx/5xx still carry an ok=false JSON body
        raw = exc.read()
    payload = json.loads(raw.decode("utf-8"))
    if not payload.get("ok"):
        raise TelegramError(payload.get("description", "telegram error"))
    return payload.get("result")


def get_updates(token, offset=None, timeout: int = 50, *, transport=None) -> list[Any]:
    params: dict[str, Any] = {"timeout": timeout, "allowed_updates": json.dumps(["message"])}
    if offset is not None:
        params["offset"] = offset
    result = _call(token, "getUpdates", params, transport=transport, poll_timeout=timeout)
    return result or []


def send_message(token, chat_id, text, *, transport=None) -> None:
    text = text or "(empty)"
    for i in range(0, len(text), _MAX_LEN):
        chunk = text[i:i + _MAX_LEN]
        _call(token, "sendMessage", {"chat_id": chat_id, "text": chunk}, transport=transport)
