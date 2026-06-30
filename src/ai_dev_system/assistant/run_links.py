"""run_links: maps a pipeline run_id to the chat that started it, so the
notifier can push gate/terminal transitions back to the right surface.
run_notifications dedupes so each (run_id, state) is pushed exactly once.

v9 adds pending_run_links: a chat can start a project before the debate row
exists.  The notifier resolves project_id → run_id on each sweep and promotes
the pending entry to a full run_links row."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RunLink:
    run_id: str
    surface: str
    chat_id: str
    session_id: str | None = None
    kind: str = "newproject"


@dataclass
class PendingLink:
    """Lightweight pending entry — project_id not yet linked to a run_id."""
    project_id: str
    surface: str
    chat_id: str


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
        """Return links not yet terminal-notified (excludes COMPLETED/FAILED/ABORTED)."""
        conn = self._conn_factory()
        rows = conn.execute(
            """
            SELECT rl.run_id, rl.surface, rl.chat_id, rl.session_id, rl.kind
            FROM run_links rl
            WHERE NOT EXISTS (
                SELECT 1 FROM run_notifications rn
                WHERE rn.run_id = rl.run_id
                  AND rn.state IN ('COMPLETED', 'FAILED', 'ABORTED')
            )
            """
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

    # ---------------------------------------------------------------------- #
    # Pending-link methods (v9)                                               #
    # ---------------------------------------------------------------------- #

    def add_pending(self, project_id: str, surface: str, chat_id: str) -> None:
        """Insert or replace a pending run-link entry for a chat-started project."""
        conn = self._conn_factory()
        conn.execute(
            "INSERT OR REPLACE INTO pending_run_links (project_id, surface, chat_id) "
            "VALUES (?,?,?)",
            (project_id, surface, str(chat_id)),
        )
        conn.commit()

    def pending(self) -> list[PendingLink]:
        """Return all pending run-link entries."""
        conn = self._conn_factory()
        rows = conn.execute(
            "SELECT project_id, surface, chat_id FROM pending_run_links"
        ).fetchall()
        return [PendingLink(project_id=r["project_id"], surface=r["surface"],
                            chat_id=r["chat_id"]) for r in rows]

    def resolve_pending(self, project_id: str, run_id: str) -> None:
        """Atomically promote pending → run_links, then delete the pending row.

        No-op if no pending entry exists for project_id.
        """
        conn = self._conn_factory()
        row = conn.execute(
            "SELECT surface, chat_id FROM pending_run_links WHERE project_id=?",
            (project_id,),
        ).fetchone()
        if row is None:
            return
        surface = row["surface"]
        chat_id = row["chat_id"]
        conn.execute(
            "INSERT INTO run_links (run_id, surface, chat_id, session_id, kind) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT(run_id) DO UPDATE SET surface=excluded.surface, "
            "chat_id=excluded.chat_id, session_id=excluded.session_id, kind=excluded.kind",
            (run_id, surface, chat_id, None, "newproject"),
        )
        conn.execute(
            "DELETE FROM pending_run_links WHERE project_id=?",
            (project_id,),
        )
        conn.commit()

    def latest_for_chat(self, surface: str, chat_id: str) -> str | None:
        """Return the most-recently linked run_id for this surface+chat_id, or None."""
        conn = self._conn_factory()
        row = conn.execute(
            "SELECT run_id FROM run_links WHERE surface=? AND chat_id=? "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (surface, str(chat_id)),
        ).fetchone()
        if row is None:
            return None
        return row["run_id"]
