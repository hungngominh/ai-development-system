"""RunStatusWatcher — polls run_links for gate/terminal status transitions and
pushes a single notification per (run_id, state) via the platform's reply().

Dedup is enforced by RunLinkStore.already_notified / mark_notified which write
into run_notifications (UNIQUE run_id, state).  One bad row never kills the sweep.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

DEFAULT_PUSH_STATES = ("PAUSED_AT_GATE_1", "COMPLETED", "FAILED", "ABORTED")

_GATE_STATES = frozenset(["PAUSED_AT_GATE_1"])


def _format_message(run_id: str, status: str) -> str:
    short = run_id[:8]
    if status in _GATE_STATES:
        return f"\U0001f514 Run {short} tới Gate 1 — trả lời để duyệt"
    if status == "COMPLETED":
        return f"✅ Run {short}: {status}"
    # FAILED / ABORTED / other terminal
    return f"❌ Run {short}: {status}"


class RunStatusWatcher:
    """Checks all active run links once and pushes platform notifications for
    gate/terminal state transitions not yet notified."""

    def __init__(
        self,
        conn_factory,
        link_store,
        platforms_by_name: dict,
        *,
        push_states: tuple = DEFAULT_PUSH_STATES,
    ) -> None:
        self._conn_factory = conn_factory
        self._link_store = link_store
        self._platforms_by_name = platforms_by_name
        self._push_states = frozenset(push_states)

    def check_once(self) -> int:
        """Sweep all active links; push notifications for new gate/terminal states.

        Returns the count of notifications actually sent in this sweep.
        """
        pushed = 0
        for link in self._link_store.active():
            try:
                pushed += self._check_link(link)
            except Exception:
                logger.exception(
                    "notifier: error checking link run_id=%s surface=%s chat_id=%s",
                    link.run_id, link.surface, link.chat_id,
                )
        return pushed

    def _check_link(self, link) -> int:
        run_id = link.run_id
        conn = self._conn_factory()
        row = conn.execute(
            "SELECT status FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if row is None:
            return 0

        status: str = row["status"] if hasattr(row, "__getitem__") else row[0]
        if status not in self._push_states:
            return 0

        if self._link_store.already_notified(run_id, status):
            return 0

        platform = self._platforms_by_name.get(link.surface)
        if platform is None:
            return 0

        msg = _format_message(run_id, status)
        platform.reply(int(link.chat_id), msg)
        self._link_store.mark_notified(run_id, status)
        return 1
