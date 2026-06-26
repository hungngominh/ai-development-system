"""Unit tests for run edit and approve in the web dashboard.

Covers spec test cases:
1. POST /run-edit with valid title + metadata → DB row updated, redirect to /run
2. POST /run-edit with invalid JSON metadata → 400, DB not modified
3. POST /run-edit with empty run_id → 400
4. POST /run-approve from PAUSED_AT_GATE_2 → status updated to RUNNING_PHASE_3, redirect
5. POST /run-approve from COMPLETED → 400 blocked
6. GET /run?id=<paused_run> → HTML contains approve button and edit form
7. GET /run?id=<completed_run> → HTML does NOT contain approve button
"""

from __future__ import annotations

import json
import uuid

import pytest

from ai_dev_system import webui
from ai_dev_system.db.connection import get_connection


# ---- helpers ----


def _make_run(conn, project_id, *, status="PAUSED_AT_GATE_2", title="Test Run", metadata="{}"):
    run_id = uuid.uuid4().hex
    conn.execute(
        """
        INSERT INTO runs (run_id, project_id, status, title, current_artifacts, metadata)
        VALUES (?, ?, ?, ?, '{}', ?)
        """,
        (run_id, project_id, status, title, metadata),
    )
    conn.commit()
    return run_id


# ---- test 1: POST /run-edit with valid data → DB updated, returns redirect URL ----


def test_run_edit_valid_updates_db_and_redirects(monkeypatch, file_config):
    conn = get_connection(file_config.database_url)
    project_id = uuid.uuid4().hex
    run_id = _make_run(conn, project_id, title="Old Title", metadata='{"k": 1}')
    conn.close()

    monkeypatch.setattr(webui, "_config", lambda: file_config)

    result = webui._do_run_edit(run_id, "New Title", '{"key": "value"}')

    # Should return redirect URL string
    assert isinstance(result, str)
    assert run_id in result

    # DB should be updated
    conn = get_connection(file_config.database_url)
    row = conn.execute(
        "SELECT title, metadata FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    conn.close()

    assert row["title"] == "New Title"
    assert json.loads(row["metadata"]) == {"key": "value"}


# ---- test 2: POST /run-edit with invalid JSON metadata → error bytes, DB not modified ----


def test_run_edit_invalid_json_metadata_returns_error(monkeypatch, file_config):
    conn = get_connection(file_config.database_url)
    project_id = uuid.uuid4().hex
    run_id = _make_run(conn, project_id, title="Original", metadata='{"original": true}')
    conn.close()

    monkeypatch.setattr(webui, "_config", lambda: file_config)

    result = webui._do_run_edit(run_id, "New Title", "not valid json {{{")

    # Should return error bytes, not a redirect URL
    assert isinstance(result, bytes)
    html_str = result.decode("utf-8")
    assert any(
        kw in html_str.lower()
        for kw in ("json", "không hợp lệ", "invalid", "lỗi")
    )

    # DB should NOT be modified
    conn = get_connection(file_config.database_url)
    row = conn.execute("SELECT title FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    conn.close()
    assert row["title"] == "Original"


# ---- test 3: POST /run-edit with empty run_id → error bytes ----


def test_run_edit_empty_run_id_returns_error(monkeypatch, file_config):
    monkeypatch.setattr(webui, "_config", lambda: file_config)

    result = webui._do_run_edit("", "Some Title", "{}")

    assert isinstance(result, bytes)
    html_str = result.decode("utf-8")
    assert "run" in html_str.lower() or "thiếu" in html_str.lower() or "id" in html_str.lower()


# ---- test 4: POST /run-approve from PAUSED_AT_GATE_2 → RUNNING_PHASE_3, redirect ----


def test_run_approve_paused_gate2_advances_to_phase3(monkeypatch, file_config):
    conn = get_connection(file_config.database_url)
    project_id = uuid.uuid4().hex
    run_id = _make_run(conn, project_id, status="PAUSED_AT_GATE_2")
    conn.close()

    monkeypatch.setattr(webui, "_config", lambda: file_config)

    result = webui._do_run_approve(run_id)

    # Should return redirect URL
    assert isinstance(result, str)
    assert run_id in result

    # Status should be updated
    conn = get_connection(file_config.database_url)
    row = conn.execute("SELECT status FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    conn.close()
    assert row["status"] == "RUNNING_PHASE_3"


# ---- test 5: POST /run-approve from COMPLETED → error bytes, status unchanged ----


def test_run_approve_completed_is_blocked(monkeypatch, file_config):
    conn = get_connection(file_config.database_url)
    project_id = uuid.uuid4().hex
    run_id = _make_run(conn, project_id, status="COMPLETED")
    conn.close()

    monkeypatch.setattr(webui, "_config", lambda: file_config)

    result = webui._do_run_approve(run_id)

    # Should return error bytes, not redirect
    assert isinstance(result, bytes)
    html_str = result.decode("utf-8")
    assert any(
        kw in html_str.lower()
        for kw in ("duyệt", "trạng thái", "không thể", "blocked", "không ở")
    )

    # Status must NOT have changed
    conn = get_connection(file_config.database_url)
    row = conn.execute("SELECT status FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    conn.close()
    assert row["status"] == "COMPLETED"


# ---- test 6: GET /run?id=<paused_run> → HTML contains approve button and edit form ----


def test_run_detail_paused_shows_approve_and_edit(monkeypatch, file_config):
    conn = get_connection(file_config.database_url)
    project_id = uuid.uuid4().hex
    run_id = _make_run(conn, project_id, status="PAUSED_AT_GATE_2")
    conn.close()

    monkeypatch.setattr(webui, "_config", lambda: file_config)

    page = webui._run_detail(run_id).decode("utf-8")

    # Edit form should be present
    assert "run-edit" in page
    assert 'name="title"' in page or "name='title'" in page
    assert 'name="metadata"' in page or "name='metadata'" in page

    # Approve button/form should be present for approvable state
    assert "run-approve" in page


# ---- test 7: GET /run?id=<completed_run> → edit form present, no approve button ----


def test_run_detail_completed_no_approve_button(monkeypatch, file_config):
    conn = get_connection(file_config.database_url)
    project_id = uuid.uuid4().hex
    run_id = _make_run(conn, project_id, status="COMPLETED")
    conn.close()

    monkeypatch.setattr(webui, "_config", lambda: file_config)

    page = webui._run_detail(run_id).decode("utf-8")

    # No approve button for terminal state
    assert "run-approve" not in page

    # Edit form should still be present
    assert "run-edit" in page
