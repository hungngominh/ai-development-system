from ai_dev_system.db.connection import get_connection
from ai_dev_system.db.migrator import apply_schema


def _tables(conn):
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r["name"] for r in rows}


def test_assistant_tables_created():
    conn = get_connection("sqlite:///:memory:")
    apply_schema(conn)
    assert "assistant_sessions" in _tables(conn)
    assert "assistant_messages" in _tables(conn)
    conn.close()


def test_assistant_session_unique_surface_chat():
    conn = get_connection("sqlite:///:memory:")
    apply_schema(conn)
    conn.execute(
        "INSERT INTO assistant_sessions (session_id, surface, chat_id, status) VALUES (?,?,?,?)",
        ("s1", "local", "cli", "active"),
    )
    import sqlite3
    try:
        conn.execute(
            "INSERT INTO assistant_sessions (session_id, surface, chat_id, status) VALUES (?,?,?,?)",
            ("s2", "local", "cli", "active"),
        )
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised, "duplicate (surface, chat_id) must violate UNIQUE"
    conn.close()


def test_assistant_message_role_check():
    conn = get_connection("sqlite:///:memory:")
    apply_schema(conn)
    conn.execute(
        "INSERT INTO assistant_sessions (session_id, surface, chat_id, status) VALUES (?,?,?,?)",
        ("s1", "local", "cli", "active"),
    )
    import sqlite3
    try:
        conn.execute(
            "INSERT INTO assistant_messages (session_id, role, content) VALUES (?,?,?)",
            ("s1", "system", "x"),
        )
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised, "role must be constrained to user/assistant"
    conn.close()
