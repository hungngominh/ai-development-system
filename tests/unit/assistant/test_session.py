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
