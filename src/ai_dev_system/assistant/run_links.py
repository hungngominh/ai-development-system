"""run_links: maps a pipeline run_id to the chat that started it, so the
notifier can push gate/terminal transitions back to the right surface.
run_notifications dedupes so each (run_id, state) is pushed exactly once."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RunLink:
    run_id: str
    surface: str
    chat_id: str
    session_id: str | None = None
    kind: str = "newproject"


class RunLinkStore:
    def __init__(self, conn_factory) -> None:
        self._conn_factory = conn_factory

    def link(self, run_id, surface, chat_id, *, session_id=None, kind="newproject") -> None:
        conn = self._conn_factory()
        conn.execute(
            "INSERT INTO run_links (run_id, surface, chat_id, session_id, kind) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT(run_id) DO UPDATE SET surface=excluded.surface, "
            "chat_id=excluded.chat_id, session_id=excluded.session_id, kind=excluded.kind",
            (run_id, surface, str(chat_id), session_id, kind),
        )
        conn.commit()

    def lookup(self, run_id) -> RunLink | None:
        conn = self._conn_factory()
        row = conn.execute(
            "SELECT run_id, surface, chat_id, session_id, kind FROM run_links WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        return RunLink(run_id=row["run_id"], surface=row["surface"], chat_id=row["chat_id"],
                       session_id=row["session_id"], kind=row["kind"])

    def active(self) -> list[RunLink]:
        conn = self._conn_factory()
        rows = conn.execute(
            "SELECT run_id, surface, chat_id, session_id, kind FROM run_links"
        ).fetchall()
        return [RunLink(run_id=r["run_id"], surface=r["surface"], chat_id=r["chat_id"],
                        session_id=r["session_id"], kind=r["kind"]) for r in rows]

    def already_notified(self, run_id, state) -> bool:
        conn = self._conn_factory()
        row = conn.execute(
            "SELECT 1 FROM run_notifications WHERE run_id=? AND state=?", (run_id, state)
        ).fetchone()
        return row is not None

    def mark_notified(self, run_id, state) -> None:
        conn = self._conn_factory()
        conn.execute(
            "INSERT OR IGNORE INTO run_notifications (run_id, state) VALUES (?,?)",
            (run_id, state),
        )
        conn.commit()
