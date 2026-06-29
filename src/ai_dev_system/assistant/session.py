"""Durable conversational sessions: one persistent transcript per (surface, chat_id),
keyed by session_id. The transcript IS the crash-resume state (turn-level)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Turn:
    role: str
    content: str


class SessionStore:
    def __init__(self, conn_factory) -> None:
        self._conn_factory = conn_factory

    def load_or_create(self, surface: str, chat_id: str) -> str:
        conn = self._conn_factory()
        row = conn.execute(
            "SELECT session_id FROM assistant_sessions WHERE surface=? AND chat_id=?",
            (surface, chat_id),
        ).fetchone()
        if row is not None:
            return row["session_id"]
        sid = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO assistant_sessions (session_id, surface, chat_id, status) "
            "VALUES (?,?,?, 'active')",
            (sid, surface, chat_id),
        )
        conn.commit()
        return sid

    def append(self, session_id: str, role: str, content: str, *,
               input_tokens=None, output_tokens=None, cost_usd=None) -> None:
        conn = self._conn_factory()
        conn.execute(
            "INSERT INTO assistant_messages "
            "(session_id, role, content, input_tokens, output_tokens, cost_usd) "
            "VALUES (?,?,?,?,?,?)",
            (session_id, role, content, input_tokens, output_tokens, cost_usd),
        )
        conn.execute(
            "UPDATE assistant_sessions SET updated_at=datetime('now') WHERE session_id=?",
            (session_id,),
        )
        conn.commit()

    def recent(self, session_id: str, limit: int) -> list[Turn]:
        conn = self._conn_factory()
        rows = conn.execute(
            "SELECT role, content FROM assistant_messages WHERE session_id=? "
            "ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [Turn(role=r["role"], content=r["content"]) for r in reversed(rows)]

    def set_status(self, session_id: str, status: str) -> None:
        conn = self._conn_factory()
        conn.execute(
            "UPDATE assistant_sessions SET status=? WHERE session_id=?", (status, session_id)
        )
        conn.commit()

    def get_status(self, session_id: str) -> str:
        conn = self._conn_factory()
        row = conn.execute(
            "SELECT status FROM assistant_sessions WHERE session_id=?", (session_id,)
        ).fetchone()
        return row["status"] if row else ""


# --- crash-shutdown marker ---------------------------------------------------

def clean_shutdown_path(home) -> Path:
    return Path(home) / ".clean_shutdown"


def mark_clean_shutdown(home) -> None:
    clean_shutdown_path(home).write_text("ok", encoding="utf-8")


def consume_clean_shutdown(home) -> bool:
    """True if a clean-shutdown marker existed (then deletes it); False otherwise."""
    p = clean_shutdown_path(home)
    if p.exists():
        p.unlink()
        return True
    return False
