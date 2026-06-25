"""Unit tests for run edit and approve features in the web dashboard.

Covers spec test cases:
1. POST /run-edit with valid title + metadata → DB row updated, redirect to /run
2. POST /run-edit with invalid JSON metadata → 400, DB not modified
3. POST /run-edit with empty run_id → 400
4. POST /run-edit with empty title → 400, DB not modified
5. POST /run-approve from PAUSED_AT_GATE_2 → status updated to RUNNING_PHASE_3, redirect
6. POST /run-approve from COMPLETED → 400 blocked
7. GET /run?id=<paused_run> → HTML contains approve button and edit form
8. GET /run?id=<completed_run> → HTML does NOT contain approve button
9. POST /run-approve with missing run_id → 400
10. POST /run-edit with unknown run_id → 400 error card
"""

from __future__ import annotations

import json
import uuid

import pytest

from ai_dev_system import webui
from ai_dev_system.db.connection import get_connection


# ---- helpers ----


def _make_run(conn, project_id, *, status="PAUSED_AT_GATE_2", title="Test Run",
              metadata="{}"):
    run_id = uuid.uuid4().hex
    conn.execute(
        """
        INSERT INTO runs (run_id, project_id, status, title, current_artifacts, metadata,
                          pipeline_version, legacy)
        VALUES (?, ?, ?, ?, '{"debate_report_id": null}', ?, 2, 0)
        """,
        (run_id, project_id, status, title, metadata),
    )
    conn.commit()
    return run_id


def _setup_run(file_config, *, status="PAUSED_AT_GATE_2", title="Test Run",
               metadata="{}"):
    """Insert run row. Returns run_id."""
    conn = get_connection(file_config.database_url)
    project_id = uuid.uuid4().hex
    run_id = _make_run(conn, project_id, status=status, title=title, metadata=metadata)
    conn.close()
    return run_id


# ---- test 1: edit with valid fields updates DB and redirects ----


def test_run_edit_valid_updates_db_and_redirects(monkeypatch, file_config):
    run_id = _setup_run(file_config)
    monkeypatch.setattr(webui, "_config", lambda: file_config)

    form = {
        "id": [run_id],
        "title": ["New Title"],
        "metadata": ['{"key": "value"}'],
    }
    result = webui._do_run_edit(run_id, form)

    # Should return a redirect URL string
    assert isinstance(result, str), f"Expected redirect str, got {type(result)}"
    assert run_id in result

    # DB should be updated
    conn = get_connection(file_config.database_url)
    row = conn.execute(
        "SELECT title, metadata FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    conn.close()

    assert row["title"] == "New Title"
    assert json.loads(row["metadata"]) == {"key": "value"}


# ---- test 2: edit with invalid JSON metadata → error bytes, DB not modified ----


def test_run_edit_invalid_json_metadata_returns_error(monkeypatch, file_config):
    run_id = _setup_run(file_config, title="Original Title", metadata='{"original": true}')
    monkeypatch.setattr(webui, "_config", lambda: file_config)

    form = {
        "id": [run_id],
        "title": ["New Title"],
        "metadata": ["{not valid json}"],
    }
    result = webui._do_run_edit(run_id, form)

    # Should return error bytes
    assert isinstance(result, bytes), f"Expected error bytes, got {type(result)}"
    html_str = result.decode("utf-8")
    assert "400" in html_str or "JSON" in html_str or "metadata" in html_str.lower()

    # DB should not be modified
    conn = get_connection(file_config.database_url)
    row = conn.execute(
        "SELECT title, metadata FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    conn.close()

    assert row["title"] == "Original Title"


# ---- test 3: edit with empty run_id → error bytes ----


def test_run_edit_empty_run_id_returns_error(monkeypatch, file_config):
    monkeypatch.setattr(webui, "_config", lambda: file_config)

    form = {
        "id": [""],
        "title": ["Some Title"],
        "metadata": ["{}"],
    }
    result = webui._do_run_edit("", form)

    assert isinstance(result, bytes)
    html_str = result.decode("utf-8")
    assert "run id" in html_str.lower() or "thiếu" in html_str.lower()


# ---- test 4: edit with empty title → error bytes, DB not modified ----


def test_run_edit_empty_title_returns_error(monkeypatch, file_config):
    run_id = _setup_run(file_config, title="Original Title")
    monkeypatch.setattr(webui, "_config", lambda: file_config)

    form = {
        "id": [run_id],
        "title": [""],
        "metadata": ["{}"],
    }
    result = webui._do_run_edit(run_id, form)

    assert isinstance(result, bytes)

    # DB should not be modified
    conn = get_connection(file_config.database_url)
    row = conn.execute(
        "SELECT title FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    conn.close()
    assert row["title"] == "Original Title"


# ---- test 5: approve from PAUSED_AT_GATE_2 advances to RUNNING_PHASE_3 ----


def test_run_approve_paused_gate2_advances_status(monkeypatch, file_config):
    run_id = _setup_run(file_config, status="PAUSED_AT_GATE_2")
    monkeypatch.setattr(webui, "_config", lambda: file_config)

    result = webui._do_run_approve(run_id)

    assert isinstance(result, str), f"Expected redirect str, got {type(result)}"
    assert run_id in result

    conn = get_connection(file_config.database_url)
    row = conn.execute(
        "SELECT status FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    conn.close()
    assert row["status"] == "RUNNING_PHASE_3"


# ---- test 6: approve from COMPLETED is blocked ----


def test_run_approve_completed_is_blocked(monkeypatch, file_config):
    run_id = _setup_run(file_config, status="COMPLETED")
    monkeypatch.setattr(webui, "_config", lambda: file_config)

    result = webui._do_run_approve(run_id)

    assert isinstance(result, bytes), f"Expected error bytes, got {type(result)}"
    html_str = result.decode("utf-8")
    assert "duyệt" in html_str.lower() or "trạng thái" in html_str.lower() or "paused" in html_str.lower()

    # Status must not have changed
    conn = get_connection(file_config.database_url)
    row = conn.execute(
        "SELECT status FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    conn.close()
    assert row["status"] == "COMPLETED"


# ---- test 7: GET /run for paused run renders approve button and edit form ----


def test_run_detail_paused_renders_approve_and_edit(monkeypatch, file_config):
    run_id = _setup_run(file_config, status="PAUSED_AT_GATE_2", title="My Run")
    monkeypatch.setattr(webui, "_config", lambda: file_config)

    page = webui._run_detail(run_id).decode("utf-8")

    assert "run-approve" in page or "run_approve" in page
    assert "run-edit" in page or "run_edit" in page
    assert 'name="title"' in page or "title" in page


# ---- test 8: GET /run for completed run has no approve button ----


def test_run_detail_completed_no_approve_button(monkeypatch, file_config):
    run_id = _setup_run(file_config, status="COMPLETED", title="Done Run")
    monkeypatch.setattr(webui, "_config", lambda: file_config)

    page = webui._run_detail(run_id).decode("utf-8")

    assert "run-approve" not in page


# ---- test 9: approve with missing run_id → error bytes ----


def test_run_approve_missing_run_id_returns_error(monkeypatch, file_config):
    monkeypatch.setattr(webui, "_config", lambda: file_config)

    result = webui._do_run_approve("")

    assert isinstance(result, bytes)
    html_str = result.decode("utf-8")
    assert "run id" in html_str.lower() or "thiếu" in html_str.lower()


# ---- test 10: edit with unknown run_id → error bytes ----


def test_run_edit_unknown_run_id_returns_error(monkeypatch, file_config):
    monkeypatch.setattr(webui, "_config", lambda: file_config)

    run_id = uuid.uuid4().hex  # not in DB
    form = {
        "id": [run_id],
        "title": ["Some Title"],
        "metadata": ["{}"],
    }
    result = webui._do_run_edit(run_id, form)

    assert isinstance(result, bytes)
    html_str = result.decode("utf-8")
    assert "tìm thấy" in html_str.lower() or "không" in html_str.lower()
