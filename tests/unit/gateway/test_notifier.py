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


# ---------------------------------------------------------------------------
# Test: pending-link resolution (Critical #1)
# ---------------------------------------------------------------------------

def test_pending_link_resolves_then_pushes_on_same_sweep(file_db_url):
    """pending link + runs row at PAUSED_AT_GATE_1 → check_once resolves + pushes."""
    conn_factory, link_store = _make_store(file_db_url)
    run_id = "pend1111-0000-0000-0000-000000000001"
    project_id = "proj-pending-test-1"

    # Add a pending link (NOT a resolved run_links entry yet)
    link_store.add_pending(project_id, "telegram", "55")
    _seed_run_with_project(conn_factory, run_id, project_id, "PAUSED_AT_GATE_1")

    platform = _FakePlatform()
    watcher = RunStatusWatcher(conn_factory, link_store, {"telegram": platform})

    count = watcher.check_once()

    # Pending should be resolved (no pending entries left)
    assert link_store.pending() == [], "pending entry should be resolved after check_once"
    # A run_links entry should now exist
    assert link_store.lookup(run_id) is not None, "run_links row should be created after resolve"
    # Push should have happened
    assert count == 1, f"Expected 1 push, got {count}"
    assert len(platform.calls) == 1


def test_pending_link_no_runs_row_no_crash_no_push(file_db_url):
    """pending link but no runs row yet → no resolve, no push, no crash."""
    conn_factory, link_store = _make_store(file_db_url)
    project_id = "proj-pending-test-2"

    link_store.add_pending(project_id, "telegram", "66")
    # Do NOT seed a runs row

    platform = _FakePlatform()
    watcher = RunStatusWatcher(conn_factory, link_store, {"telegram": platform})

    count = watcher.check_once()

    # Still pending
    assert len(link_store.pending()) == 1, "pending entry should remain if no runs row"
    assert count == 0
    assert platform.calls == []


# ---------------------------------------------------------------------------
# Test: PAUSED_AT_GATE_2 pushes once, deduplicates on second call
# ---------------------------------------------------------------------------

def test_gate2_pushes_once_then_dedup(file_db_url):
    conn_factory, link_store = _make_store(file_db_url)
    run_id = "gggg7777-0000-0000-0000-000000000007"

    link_store.link(run_id, "telegram", "42")
    _seed_run(conn_factory, run_id, "PAUSED_AT_GATE_2")

    platform = _FakePlatform()
    watcher = RunStatusWatcher(conn_factory, link_store, {"telegram": platform})

    # First check: should push once
    count = watcher.check_once()
    assert count == 1
    assert len(platform.calls) == 1
    chat_id, msg = platform.calls[0]
    assert chat_id == 42
    assert "Gate 2" in msg
    assert run_id[:8] in msg

    # Second check: already notified → 0 pushes
    count2 = watcher.check_once()
    assert count2 == 0
    assert len(platform.calls) == 1  # still only one


# ---------------------------------------------------------------------------
# Test: GATE_1 → GATE_2 transition fires BOTH pushes (distinct dedup states)
# ---------------------------------------------------------------------------

def test_gate1_then_gate2_both_push(file_db_url):
    """A run that transitions PAUSED_AT_GATE_1 → PAUSED_AT_GATE_2 over two
    sweeps should push twice total (one per state) because dedup is per
    (run_id, state)."""
    conn_factory, link_store = _make_store(file_db_url)
    run_id = "hhhh8888-0000-0000-0000-000000000008"

    link_store.link(run_id, "telegram", "42")
    _seed_run(conn_factory, run_id, "PAUSED_AT_GATE_1")

    platform = _FakePlatform()
    watcher = RunStatusWatcher(conn_factory, link_store, {"telegram": platform})

    # First sweep: Gate 1 push fires
    count1 = watcher.check_once()
    assert count1 == 1
    assert len(platform.calls) == 1
    assert "Gate 1" in platform.calls[0][1]

    # Simulate run progressing to PAUSED_AT_GATE_2
    conn = conn_factory()
    conn.execute(
        "UPDATE runs SET status='PAUSED_AT_GATE_2' WHERE run_id=?", (run_id,)
    )
    conn.commit()

    # Second sweep: Gate 2 push fires (different state → different dedup row)
    count2 = watcher.check_once()
    assert count2 == 1
    assert len(platform.calls) == 2
    assert "Gate 2" in platform.calls[1][1]

    # Third sweep: no new state → 0 pushes
    count3 = watcher.check_once()
    assert count3 == 0
    assert len(platform.calls) == 2  # still only 2 total


def _seed_run_with_project(conn_factory, run_id: str, project_id: str, status: str) -> None:
    """Insert a runs row with a specific project_id."""
    conn = conn_factory()
    conn.execute(
        "INSERT INTO runs (run_id, project_id, status, title, current_artifacts, metadata) "
        "VALUES (?, ?, ?, 'Test Run', '{}', '{}')",
        (run_id, project_id, status),
    )
    conn.commit()
