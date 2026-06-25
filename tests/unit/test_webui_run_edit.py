"""Unit tests for run edit and approve in the web dashboard.

Covers spec test cases:
1. POST /run-edit with valid title + metadata → DB row updated, redirect to /run
2. POST /run-edit with invalid JSON metadata → 400, DB not modified
3. POST /run-edit with empty run_id → 400
4. POST /run-approve from PAUSED_AT_GATE_2 → status updated to RUNNING_PHASE_3, redirect
5. POST /run-approve from COMPLETED → 400 blocked
6. GET /run?id=<paused_run> → HTML contains approve button and edit form
7. GET /run?id=<completed_run> → HTML does NOT contain approve button
8. POST /run-edit with empty title → 400 validation error
9. POST /run-edit run_id not found → error card rendered
"""

from __future__ import annotations

import json
import uuid

import pytest

from ai_dev_system import webui
from ai_dev_system.db.connection import get_connection


# ---- helpers ----


def _make_run(conn, *, status="PAUSED_AT_GATE_2", title="Test Run"):
    run_id = uuid.uuid4().hex
    project_id = uuid.uuid4().hex
    conn.execute(
        """
        INSERT INTO runs (run_id, project_id, status, title, current_artifacts, metadata)
        VALUES (?, ?, ?, ?, '{}', '{}')
        """,
        (run_id, project_id, status, title),
    )
    conn.commit()
    return run_id


def _fetch_run(conn, run_id):
    return conn.execute(
        "SELECT run_id, status, title, metadata FROM runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()


# ---- test 1: valid edit → DB updated, redirect ----


def test_run_edit_valid_updates_db(monkeypatch, file_config, tmp_path):
    conn = get_connection(file_config.database_url)
    run_id = _make_run(conn, status="PAUSED_AT_GATE_2", title="Old Title")
    conn.close()

    monkeypatch.setattr(webui, "_config", lambda: file_config)

    redirect = webui._do_run_edit(run_id, "New Title", '{"key": "val"}')

    assert redirect == f"/run?id={run_id}"

    conn2 = get_connection(file_config.database_url)
    row = _fetch_run(conn2, run_id)
    conn2.close()

    assert row["title"] == "New Title"
    parsed = json.loads(row["metadata"])
    assert parsed["key"] == "val"


# ---- test 2: invalid JSON metadata → 400, DB not modified ----


def test_run_edit_invalid_json_metadata_returns_error(monkeypatch, file_config):
    conn = get_connection(file_config.database_url)
    run_id = _make_run(conn, title="Original")
    conn.close()

    monkeypatch.setattr(webui, "_config", lambda: file_config)

    result = webui._do_run_edit(run_id, "New Title", "not-json{{{")

    # Must return error HTML (bytes), not a redirect string
    assert isinstance(result, bytes)
    page = result.decode("utf-8")
    assert "400" in page or "JSON" in page or "lỗi" in page.lower() or "invalid" in page.lower()

    # DB must not have been modified
    conn2 = get_connection(file_config.database_url)
    row = _fetch_run(conn2, run_id)
    conn2.close()
    assert row["title"] == "Original"


# ---- test 3: empty run_id → 400 ----


def test_run_edit_empty_run_id_returns_400(monkeypatch, file_config):
    monkeypatch.setattr(webui, "_config", lambda: file_config)

    result = webui._do_run_edit("", "Some Title", "{}")

    assert isinstance(result, bytes)
    page = result.decode("utf-8")
    assert "400" in page or "run id" in page.lower() or "thiếu" in page.lower()


# ---- test 4: approve from PAUSED_AT_GATE_2 → RUNNING_PHASE_3, redirect ----


def test_run_approve_paused_gate2_advances_to_phase3(monkeypatch, file_config):
    conn = get_connection(file_config.database_url)
    run_id = _make_run(conn, status="PAUSED_AT_GATE_2")
    conn.close()

    monkeypatch.setattr(webui, "_config", lambda: file_config)

    result = webui._do_run_approve(run_id)

    assert isinstance(result, str)
    assert f"id={run_id}" in result

    conn2 = get_connection(file_config.database_url)
    row = _fetch_run(conn2, run_id)
    conn2.close()

    assert row["status"] == "RUNNING_PHASE_3"


# ---- test 5: approve from COMPLETED → 400 blocked ----


def test_run_approve_completed_is_blocked(monkeypatch, file_config):
    conn = get_connection(file_config.database_url)
    run_id = _make_run(conn, status="COMPLETED")
    conn.close()

    monkeypatch.setattr(webui, "_config", lambda: file_config)

    result = webui._do_run_approve(run_id)

    assert isinstance(result, bytes)
    page = result.decode("utf-8")
    assert "400" in page or "duyệt" in page.lower() or "trạng thái" in page.lower() or "không" in page.lower()

    conn2 = get_connection(file_config.database_url)
    row = _fetch_run(conn2, run_id)
    conn2.close()
    assert row["status"] == "COMPLETED"


# ---- test 6: GET /run?id=<paused_run> → edit form + approve button ----


def test_run_detail_paused_shows_edit_form_and_approve_button(monkeypatch, file_config):
    conn = get_connection(file_config.database_url)
    run_id = _make_run(conn, status="PAUSED_AT_GATE_2", title="Paused Run")
    conn.close()

    monkeypatch.setattr(webui, "_config", lambda: file_config)

    page = webui._run_detail(run_id).decode("utf-8")

    # Edit form fields must be present
    assert "run-edit" in page
    assert 'name="title"' in page
    assert 'name="metadata"' in page

    # Approve button must be present for paused runs
    assert "run-approve" in page


# ---- test 7: GET /run?id=<completed_run> → no approve button ----


def test_run_detail_completed_hides_approve_button(monkeypatch, file_config):
    conn = get_connection(file_config.database_url)
    run_id = _make_run(conn, status="COMPLETED", title="Done Run")
    conn.close()

    monkeypatch.setattr(webui, "_config", lambda: file_config)

    page = webui._run_detail(run_id).decode("utf-8")

    # Approve button must NOT be present for terminal runs
    assert "run-approve" not in page


# ---- test 8: empty title → 400 ----


def test_run_edit_empty_title_returns_400(monkeypatch, file_config):
    conn = get_connection(file_config.database_url)
    run_id = _make_run(conn, title="Original Title")
    conn.close()

    monkeypatch.setattr(webui, "_config", lambda: file_config)

    result = webui._do_run_edit(run_id, "   ", "{}")

    assert isinstance(result, bytes)
    page = result.decode("utf-8")
    assert "400" in page or "title" in page.lower() or "tiêu đề" in page.lower()

    # DB must not have been modified
    conn2 = get_connection(file_config.database_url)
    row = _fetch_run(conn2, run_id)
    conn2.close()
    assert row["title"] == "Original Title"


# ---- test 9: run_id not found → error card ----


def test_run_edit_run_not_found_returns_error(monkeypatch, file_config):
    monkeypatch.setattr(webui, "_config", lambda: file_config)

    result = webui._do_run_edit("nonexistentrunid123", "Some Title", "{}")

    assert isinstance(result, bytes)
    page = result.decode("utf-8")
    assert "không tìm thấy" in page.lower() or "not found" in page.lower() or "run" in page.lower()
