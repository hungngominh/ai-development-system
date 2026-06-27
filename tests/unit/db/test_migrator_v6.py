"""v6 migration: events.event_type CHECK gains 'RULE_LEARNED' on old DBs."""
from __future__ import annotations

import sqlite3

import pytest

from ai_dev_system.db.connection import get_connection
from ai_dev_system.db.migrator import _apply_v6_events_check, apply_schema

# An events table as it looked BEFORE 'RULE_LEARNED' was allowed.
_OLD_EVENTS = """
CREATE TABLE events (
    event_id        TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL,
    task_run_id     TEXT,
    correlation_id  TEXT,
    event_type      TEXT NOT NULL,
    occurred_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actor           TEXT NOT NULL DEFAULT 'system',
    payload         TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(payload)),
    CHECK (event_type IN ('RUN_CREATED', 'TASK_FAILED'))
);
"""


def test_v6_rebuild_allows_rule_learned_and_preserves_data():
    conn = get_connection("sqlite:///:memory:")
    conn.execute("PRAGMA foreign_keys = OFF")  # standalone events table, no runs FK
    conn.executescript(_OLD_EVENTS)
    conn.execute(
        "INSERT INTO events (event_id, run_id, event_type) VALUES ('e1', 'r1', 'RUN_CREATED')"
    )
    conn.commit()

    # Old CHECK rejects RULE_LEARNED.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO events (event_id, run_id, event_type) VALUES ('e2', 'r1', 'RULE_LEARNED')"
        )
    conn.rollback()

    _apply_v6_events_check(conn)

    # After migration the value is permitted and old data survives.
    conn.execute(
        "INSERT INTO events (event_id, run_id, event_type) VALUES ('e3', 'r1', 'RULE_LEARNED')"
    )
    conn.commit()
    ids = [r["event_id"] for r in conn.execute(
        "SELECT event_id FROM events ORDER BY event_id"
    ).fetchall()]
    assert ids == ["e1", "e3"]
    conn.close()


def test_v6_is_noop_when_already_allowed():
    # A fresh, fully-migrated DB already permits RULE_LEARNED; v6 must not rebuild
    # (so a frozen list can never clobber event types added after v6).
    conn = get_connection("sqlite:///:memory:")
    apply_schema(conn)
    before = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='events'"
    ).fetchone()["sql"]

    _apply_v6_events_check(conn)

    after = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='events'"
    ).fetchone()["sql"]
    assert before == after  # unchanged — no rebuild
    # And the freshly-migrated DB accepts RULE_LEARNED already.
    assert "RULE_LEARNED" in after
    conn.close()
