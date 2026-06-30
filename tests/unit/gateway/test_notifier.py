"""Tests for RunStatusWatcher — push dedup per (run_id, state).

All tests are offline/deterministic: SQLite file-backed DB (shares schema with
run_links + runs tables) + fake platform that records reply() calls.
"""
from __future__ import annotations

import pytest

from ai_dev_system.db.connection import get_connection
from ai_dev_system.db.migrator import apply_schema
from ai_dev_system.assistant.run_links import RunLinkStore
from ai_dev_system.gateway.notifier import RunStatusWatcher


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakePlatform:
    """Records reply() calls instead of sending to Telegram."""
    name = "telegram"

    def __init__(self):
        self.calls: list[tuple[int, str]] = []

    def reply(self, chat_id: int, text: str) -> None:
        self.calls.append((chat_id, text))


def _make_store(file_db_url):
    """Return (conn_factory, RunLinkStore) sharing the same file-backed DB."""
    def conn_factory():
        return get_connection(file_db_url)
    link_store = RunLinkStore(conn_factory)
    return conn_factory, link_store


def _seed_run(conn_factory, run_id: str, status: str) -> None:
    """Insert a minimal runs row with the given status."""
    conn = conn_factory()
    conn.execute(
        "INSERT INTO runs (run_id, project_id, status, title, current_artifacts, metadata) "
        "VALUES (?, 'proj1', ?, 'Test Run', '{}', '{}')",
        (run_id, status),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Test: PAUSED_AT_GATE_1 pushes once, deduplicates on second call
# ---------------------------------------------------------------------------

def test_gate1_pushes_once_then_dedup(file_db_url):
    conn_factory, link_store = _make_store(file_db_url)
    run_id = "aaaa1111-0000-0000-0000-000000000001"

    link_store.link(run_id, "telegram", "42")
    _seed_run(conn_factory, run_id, "PAUSED_AT_GATE_1")

    platform = _FakePlatform()
    watcher = RunStatusWatcher(conn_factory, link_store, {"telegram": platform})

    # First check: should push once
    count = watcher.check_once()
    assert count == 1
    assert len(platform.calls) == 1
    chat_id, msg = platform.calls[0]
    assert chat_id == 42
    assert "Gate 1" in msg or "gate" in msg.lower() or run_id[:8] in msg

    # Second check: already notified → 0 pushes
    count2 = watcher.check_once()
    assert count2 == 0
    assert len(platform.calls) == 1  # still only one


# ---------------------------------------------------------------------------
# Test: COMPLETED pushes once with check mark
# ---------------------------------------------------------------------------

def test_completed_pushes_once(file_db_url):
    conn_factory, link_store = _make_store(file_db_url)
    run_id = "bbbb2222-0000-0000-0000-000000000002"

    link_store.link(run_id, "telegram", "99")
    _seed_run(conn_factory, run_id, "COMPLETED")

    platform = _FakePlatform()
    watcher = RunStatusWatcher(conn_factory, link_store, {"telegram": platform})

    count = watcher.check_once()
    assert count == 1
    _chat_id, msg = platform.calls[0]
    assert "COMPLETED" in msg or "✅" in msg


# ---------------------------------------------------------------------------
# Test: RUNNING_* status does NOT push
# ---------------------------------------------------------------------------

def test_running_does_not_push(file_db_url):
    conn_factory, link_store = _make_store(file_db_url)
    run_id = "cccc3333-0000-0000-0000-000000000003"

    link_store.link(run_id, "telegram", "77")
    _seed_run(conn_factory, run_id, "RUNNING_PHASE_1B")

    platform = _FakePlatform()
    watcher = RunStatusWatcher(conn_factory, link_store, {"telegram": platform})

    count = watcher.check_once()
    assert count == 0
    assert platform.calls == []


# ---------------------------------------------------------------------------
# Test: unknown surface (no matching platform) → 0 pushes, no crash
# ---------------------------------------------------------------------------

def test_unknown_surface_no_crash(file_db_url):
    conn_factory, link_store = _make_store(file_db_url)
    run_id = "dddd4444-0000-0000-0000-000000000004"

    # Link to "discord" surface but platforms dict only has "telegram"
    link_store.link(run_id, "discord", "55")
    _seed_run(conn_factory, run_id, "PAUSED_AT_GATE_1")

    platform = _FakePlatform()
    watcher = RunStatusWatcher(conn_factory, link_store, {"telegram": platform})

    count = watcher.check_once()
    assert count == 0
    assert platform.calls == []


# ---------------------------------------------------------------------------
# Test: FAILED and ABORTED also push
# ---------------------------------------------------------------------------

def test_failed_pushes(file_db_url):
    conn_factory, link_store = _make_store(file_db_url)
    run_id = "eeee5555-0000-0000-0000-000000000005"

    link_store.link(run_id, "telegram", "11")
    _seed_run(conn_factory, run_id, "FAILED")

    platform = _FakePlatform()
    watcher = RunStatusWatcher(conn_factory, link_store, {"telegram": platform})

    count = watcher.check_once()
    assert count == 1
    _chat_id, msg = platform.calls[0]
    assert "FAILED" in msg or "❌" in msg


def test_aborted_pushes(file_db_url):
    conn_factory, link_store = _make_store(file_db_url)
    run_id = "ffff6666-0000-0000-0000-000000000006"

    link_store.link(run_id, "telegram", "22")
    _seed_run(conn_factory, run_id, "ABORTED")

    platform = _FakePlatform()
    watcher = RunStatusWatcher(conn_factory, link_store, {"telegram": platform})

    count = watcher.check_once()
    assert count == 1


# ---------------------------------------------------------------------------
# Test: bad row (run_id not in runs) does not crash the sweep
# ---------------------------------------------------------------------------

def test_missing_run_row_no_crash(file_db_url):
    """A run_links row whose run_id doesn't exist in runs → skip, no crash."""
    conn_factory, link_store = _make_store(file_db_url)
    run_id = "ghost000-0000-0000-0000-000000000007"

    link_store.link(run_id, "telegram", "33")
    # Intentionally do NOT seed a runs row for run_id

    platform = _FakePlatform()
    watcher = RunStatusWatcher(conn_factory, link_store, {"telegram": platform})

    count = watcher.check_once()
    assert count == 0
    assert platform.calls == []
