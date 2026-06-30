from __future__ import annotations

from ai_dev_system.db.connection import get_connection
from ai_dev_system.db.migrator import apply_schema
from ai_dev_system.assistant.run_links import RunLinkStore, RunLink, PendingLink


def _factory(file_db_url):
    return lambda: get_connection(file_db_url)


def test_link_and_lookup_round_trip(file_db_url):
    cf = _factory(file_db_url)
    apply_schema(cf())
    s = RunLinkStore(cf)
    s.link("run1", "telegram", "111", session_id="sess1")
    got = s.lookup("run1")
    assert got == RunLink(run_id="run1", surface="telegram", chat_id="111",
                          session_id="sess1", kind="newproject")


def test_lookup_missing_returns_none(file_db_url):
    cf = _factory(file_db_url)
    apply_schema(cf())
    assert RunLinkStore(cf).lookup("nope") is None


def test_link_upsert_overwrites(file_db_url):
    cf = _factory(file_db_url)
    apply_schema(cf())
    s = RunLinkStore(cf)
    s.link("run1", "telegram", "111")
    s.link("run1", "telegram", "222")
    assert s.lookup("run1").chat_id == "222"


def test_active_lists_links(file_db_url):
    cf = _factory(file_db_url)
    apply_schema(cf())
    s = RunLinkStore(cf)
    s.link("a", "telegram", "1"); s.link("b", "telegram", "2")
    assert {l.run_id for l in s.active()} == {"a", "b"}


def test_notify_dedup(file_db_url):
    cf = _factory(file_db_url)
    apply_schema(cf())
    s = RunLinkStore(cf)
    s.link("run1", "telegram", "111")
    assert s.already_notified("run1", "PAUSED_AT_GATE_1") is False
    s.mark_notified("run1", "PAUSED_AT_GATE_1")
    assert s.already_notified("run1", "PAUSED_AT_GATE_1") is True
    s.mark_notified("run1", "PAUSED_AT_GATE_1")  # idempotent, no raise
    assert s.already_notified("run1", "COMPLETED") is False


# ---------------------------------------------------------------------------
# Pending-link tests (Critical #1)
# ---------------------------------------------------------------------------

def test_add_pending_then_pending_lists_it(file_db_url):
    """add_pending → pending() returns a PendingLink for that project_id."""
    cf = _factory(file_db_url)
    apply_schema(cf())
    s = RunLinkStore(cf)

    s.add_pending("proj-abc", "telegram", "99")
    pending = s.pending()
    assert len(pending) == 1
    p = pending[0]
    assert isinstance(p, PendingLink)
    assert p.project_id == "proj-abc"
    assert p.surface == "telegram"
    assert p.chat_id == "99"


def test_resolve_pending_creates_run_link_and_clears_pending(file_db_url):
    """resolve_pending → creates run_links row and removes from pending_run_links."""
    cf = _factory(file_db_url)
    apply_schema(cf())
    s = RunLinkStore(cf)

    s.add_pending("proj-abc", "telegram", "99")
    assert len(s.pending()) == 1

    s.resolve_pending("proj-abc", "run-xyz")

    # pending is gone
    assert s.pending() == []
    # run link is created
    link = s.lookup("run-xyz")
    assert link is not None
    assert link.surface == "telegram"
    assert link.chat_id == "99"


def test_resolve_pending_noop_when_no_pending_row(file_db_url):
    """resolve_pending with no matching pending row is a no-op — no error."""
    cf = _factory(file_db_url)
    apply_schema(cf())
    s = RunLinkStore(cf)

    # Should not raise
    s.resolve_pending("nonexistent-project", "run-xyz")
    assert s.pending() == []


def test_latest_for_chat_returns_most_recent_run_id(file_db_url):
    """latest_for_chat returns the most recently linked run_id for a surface+chat_id."""
    cf = _factory(file_db_url)
    apply_schema(cf())
    s = RunLinkStore(cf)

    s.link("run-old", "telegram", "42")
    s.link("run-new", "telegram", "42")
    # latest should be run-new (highest created_at / last inserted)
    result = s.latest_for_chat("telegram", "42")
    assert result == "run-new"


def test_latest_for_chat_returns_none_when_no_links(file_db_url):
    """latest_for_chat returns None when no links exist for the surface+chat."""
    cf = _factory(file_db_url)
    apply_schema(cf())
    s = RunLinkStore(cf)
    assert s.latest_for_chat("telegram", "42") is None


def test_active_excludes_terminal_notified_links(file_db_url):
    """active() excludes links whose run has been terminal-notified (COMPLETED/FAILED/ABORTED)."""
    cf = _factory(file_db_url)
    apply_schema(cf())
    s = RunLinkStore(cf)

    s.link("run-active", "telegram", "1")
    s.link("run-done", "telegram", "2")
    s.link("run-failed", "telegram", "3")

    # Mark run-done as COMPLETED-notified
    s.mark_notified("run-done", "COMPLETED")
    # Mark run-failed as FAILED-notified
    s.mark_notified("run-failed", "FAILED")

    active_ids = {l.run_id for l in s.active()}
    assert "run-active" in active_ids, "non-terminal link should be active"
    assert "run-done" not in active_ids, "COMPLETED-notified link should be excluded"
    assert "run-failed" not in active_ids, "FAILED-notified link should be excluded"


def test_active_keeps_gate1_notified_links(file_db_url):
    """active() keeps PAUSED_AT_GATE_1-notified links (not terminal)."""
    cf = _factory(file_db_url)
    apply_schema(cf())
    s = RunLinkStore(cf)

    s.link("run-at-gate", "telegram", "7")
    s.mark_notified("run-at-gate", "PAUSED_AT_GATE_1")

    active_ids = {l.run_id for l in s.active()}
    assert "run-at-gate" in active_ids, "gate1-notified link must remain active (not terminal)"


def test_run_link_equality_unchanged_by_pending_link_addition(file_db_url):
    """PendingLink is separate; existing RunLink equality test is unaffected."""
    cf = _factory(file_db_url)
    apply_schema(cf())
    s = RunLinkStore(cf)
    s.link("run1", "telegram", "111", session_id="sess1")
    got = s.lookup("run1")
    # Original equality test still passes
    assert got == RunLink(run_id="run1", surface="telegram", chat_id="111",
                          session_id="sess1", kind="newproject")
