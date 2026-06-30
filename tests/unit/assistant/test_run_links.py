from __future__ import annotations

from ai_dev_system.db.connection import get_connection
from ai_dev_system.db.migrator import apply_schema
from ai_dev_system.assistant.run_links import RunLinkStore, RunLink


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
