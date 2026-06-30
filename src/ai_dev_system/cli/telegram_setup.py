"""Logic for `ai-dev telegram setup`: detect chat_id via getUpdates and merge a
bot entry into AI_DEV_TELEGRAM_BOTS in a .env file. Pure helpers are network- and
IO-free so they can be unit-tested directly."""
from __future__ import annotations

import json

BOTS_KEY = "AI_DEV_TELEGRAM_BOTS"


def extract_chat_id(updates: list) -> tuple[int, str] | None:
    """Return (chat_id, username) from the first message update, or None."""
    for upd in updates:
        msg = upd.get("message") or {}
        chat = msg.get("chat") or {}
        cid = chat.get("id")
        if cid is not None:
            uname = (msg.get("from") or {}).get("username", "") or ""
            return int(cid), uname
    return None


def upsert_bot_in_env(env_text: str, label: str, token: str, chat_ids) -> str:
    """Add a bot to the AI_DEV_TELEGRAM_BOTS line (single-line JSON), preserving
    all other lines. Raise ValueError on duplicate label."""
    lines = env_text.splitlines()
    line_idx = None
    bots: list = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(f"{BOTS_KEY}=") and not stripped.startswith("#"):
            line_idx = i
            raw = stripped[len(BOTS_KEY) + 1:].strip()
            if raw:
                try:
                    bots = json.loads(raw)
                except ValueError:
                    bots = []
            break

    if any(isinstance(b, dict) and b.get("label") == label for b in bots):
        raise ValueError(f"Bot với label '{label}' đã tồn tại. Dùng tên khác.")

    bots.append({"label": label, "token": token, "chat_ids": list(chat_ids)})
    new_line = f"{BOTS_KEY}={json.dumps(bots, ensure_ascii=False)}"

    if line_idx is not None:
        lines[line_idx] = new_line
    else:
        lines.append(new_line)
    return "\n".join(lines) + "\n"
