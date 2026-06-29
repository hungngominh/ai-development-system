from ai_dev_system.assistant.session import (
    SessionStore, Turn, mark_clean_shutdown, consume_clean_shutdown, clean_shutdown_path,
)


def test_load_or_create_is_stable(conn):
    store = SessionStore(lambda: conn)
    sid1 = store.load_or_create("local", "cli")
    sid2 = store.load_or_create("local", "cli")
    assert sid1 == sid2  # same (surface, chat_id) → same session


def test_append_and_recent_chronological(conn):
    store = SessionStore(lambda: conn)
    sid = store.load_or_create("local", "cli")
    store.append(sid, "user", "hi")
    store.append(sid, "assistant", "hello")
    store.append(sid, "user", "bye")
    turns = store.recent(sid, limit=2)
    assert [t.content for t in turns] == ["hello", "bye"]
    assert all(isinstance(t, Turn) for t in turns)


def test_status_roundtrip(conn):
    store = SessionStore(lambda: conn)
    sid = store.load_or_create("local", "cli")
    assert store.get_status(sid) == "active"
    store.set_status(sid, "resume_pending")
    assert store.get_status(sid) == "resume_pending"


def test_clean_shutdown_marker(tmp_path):
    assert consume_clean_shutdown(tmp_path) is False
    mark_clean_shutdown(tmp_path)
    assert clean_shutdown_path(tmp_path).exists()
    assert consume_clean_shutdown(tmp_path) is True   # existed → True, now deleted
    assert consume_clean_shutdown(tmp_path) is False  # gone


def test_append_visible_on_fresh_connection(file_db_url):
    from ai_dev_system.db.connection import get_connection
    store = SessionStore(lambda: get_connection(file_db_url))  # fresh connection each call
    sid = store.load_or_create("local", "cli")
    store.append(sid, "user", "persisted?")
    turns = store.recent(sid, 10)   # opens a DIFFERENT connection
    assert [t.content for t in turns] == ["persisted?"]


def test_mark_recent_resume_pending_flags_only_recent_active(conn):
    store = SessionStore(lambda: conn)
    # recent active -> flagged
    conn.execute("INSERT INTO assistant_sessions (session_id, surface, chat_id, status, updated_at) "
                 "VALUES ('a','telegram','1','active', datetime('now'))")
    # stale active -> NOT flagged (2h old)
    conn.execute("INSERT INTO assistant_sessions (session_id, surface, chat_id, status, updated_at) "
                 "VALUES ('b','telegram','2','active', datetime('now','-120 minutes'))")
    # recent suspended -> NOT flagged
    conn.execute("INSERT INTO assistant_sessions (session_id, surface, chat_id, status, updated_at) "
                 "VALUES ('c','telegram','3','suspended', datetime('now'))")
    conn.commit()
    n = store.mark_recent_resume_pending(window_minutes=60)
    assert n == 1
    assert store.get_status("a") == "resume_pending"
    assert store.get_status("b") == "active"
    assert store.get_status("c") == "suspended"
