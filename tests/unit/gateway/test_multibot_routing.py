"""Integration test: multi-bot routing proves a run linked to surface A
notifies ONLY via bot A, and a run linked to surface B notifies ONLY via bot B.

All offline/deterministic — injected sender recorders, no network, file-backed SQLite.
"""
from __future__ import annotations

import pytest

from ai_dev_system.db.connection import get_connection
from ai_dev_system.db.migrator import apply_schema
from ai_dev_system.assistant.run_links import RunLinkStore
from ai_dev_system.gateway.notifier import RunStatusWatcher
from ai_dev_system.gateway.platforms.telegram import TelegramAdapter


# ---------------------------------------------------------------------------
# Helpers — mirror exactly the patterns in test_notifier.py
# ---------------------------------------------------------------------------

def _make_store(file_db_url):
    """Return (conn_factory, RunLinkStore) sharing the same file-backed DB."""
    def conn_factory():
        return get_connection(file_db_url)
    link_store = RunLinkStore(conn_factory)
    return conn_factory, link_store


def _seed_run(conn_factory, run_id: str, status: str) -> None:
    """Insert a minimal runs row — same column shape as test_notifier.py."""
    conn = conn_factory()
    conn.execute(
        "INSERT INTO runs (run_id, project_id, status, title, current_artifacts, metadata) "
        "VALUES (?, 'proj1', ?, 'Test Run', '{}', '{}')",
        (run_id, status),
    )
    conn.commit()


def _make_sender_recorder():
    """Return (sent_list, sender_fn) pair — sender appends (chat_id, text) to sent_list."""
    calls: list[tuple[int | str, str]] = []

    def sender(token, chat_id, text, transport=None):
        calls.append((chat_id, text))

    return calls, sender


def _make_adapter(name: str, sent: list, fake_transport=object()) -> TelegramAdapter:
    """Build a TelegramAdapter with an injected recorder sender and a canned transport."""
    _, sender = _make_sender_recorder()
    # We need to reuse the list reference from the caller, so we build a closure here.
    calls = sent

    def recording_sender(token, chat_id, text, transport=None):
        calls.append((chat_id, text))

    return TelegramAdapter(
        name=name,
        token=f"fake-token-{name}",
        allowed_chat_ids=(),          # allowlist unused; poll not exercised in this test
        transport=fake_transport,
        sender=recording_sender,
    )


# ---------------------------------------------------------------------------
# Test 1: run linked to bot A → bot A notifies, bot B stays silent
# ---------------------------------------------------------------------------

def test_run_on_bot_A_notifies_via_A_only(file_db_url):
    """A run_links row with surface='A' must push through bot A and NOT bot B."""
    conn_factory, link_store = _make_store(file_db_url)

    run_id = "aaaa1111-0000-0000-0000-000000000011"

    # Seed: run linked to surface "A", chat 111
    link_store.link(run_id, "A", "111")
    _seed_run(conn_factory, run_id, "PAUSED_AT_GATE_1")

    # Build two adapters with independent recorder lists
    sent_A: list = []
    sent_B: list = []
    adapter_A = _make_adapter("A", sent_A)
    adapter_B = _make_adapter("B", sent_B)

    platforms_by_name = {adapter_A.name: adapter_A, adapter_B.name: adapter_B}
    watcher = RunStatusWatcher(conn_factory, link_store, platforms_by_name)

    count = watcher.check_once()

    # Bot A: exactly one push to chat 111
    assert count == 1, f"Expected 1 push total, got {count}"
    assert len(sent_A) == 1, f"Bot A should have exactly 1 push; got {len(sent_A)}"
    assert sent_A[0][0] == 111, f"Bot A chat_id should be 111; got {sent_A[0][0]}"

    # Bot B: zero pushes
    assert len(sent_B) == 0, f"Bot B should have 0 pushes; got {len(sent_B)}"


# ---------------------------------------------------------------------------
# Test 2: bi-directional independence — run A→botA, run B→botB
# ---------------------------------------------------------------------------

def test_two_runs_route_independently(file_db_url):
    """With two runs each linked to a different surface, each bot pushes exactly once
    to its own chat and zero times to the other's chat."""
    conn_factory, link_store = _make_store(file_db_url)

    run_id_a = "aaaa2222-0000-0000-0000-000000000021"
    run_id_b = "bbbb2222-0000-0000-0000-000000000022"

    # Seed: run A linked to surface "A" chat 111, run B linked to surface "B" chat 222
    link_store.link(run_id_a, "A", "111")
    link_store.link(run_id_b, "B", "222")
    _seed_run(conn_factory, run_id_a, "PAUSED_AT_GATE_1")
    _seed_run(conn_factory, run_id_b, "PAUSED_AT_GATE_1")

    sent_A: list = []
    sent_B: list = []
    adapter_A = _make_adapter("A", sent_A)
    adapter_B = _make_adapter("B", sent_B)

    platforms_by_name = {adapter_A.name: adapter_A, adapter_B.name: adapter_B}
    watcher = RunStatusWatcher(conn_factory, link_store, platforms_by_name)

    count = watcher.check_once()

    # Two total pushes (one per run)
    assert count == 2, f"Expected 2 pushes total, got {count}"

    # Bot A: exactly 1 push to chat 111
    assert len(sent_A) == 1, f"Bot A should have 1 push; got {len(sent_A)}"
    assert sent_A[0][0] == 111, f"Bot A chat_id should be 111; got {sent_A[0][0]}"

    # Bot B: exactly 1 push to chat 222
    assert len(sent_B) == 1, f"Bot B should have 1 push; got {len(sent_B)}"
    assert sent_B[0][0] == 222, f"Bot B chat_id should be 222; got {sent_B[0][0]}"

    # No cross-contamination: A never sent to chat 222, B never sent to chat 111
    assert all(chat != 222 for chat, _ in sent_A), "Bot A must not push to chat 222"
    assert all(chat != 111 for chat, _ in sent_B), "Bot B must not push to chat 111"
