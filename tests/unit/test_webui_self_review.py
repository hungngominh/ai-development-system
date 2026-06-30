"""Tests for spec self-review findings surfaced on the task-spec page.

Mirrors test_webui_task_plan.py fixture style: monkeypatch webui._config,
write spec JSON under storage_root/task_specs/<id>.json with status:"done".
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import ai_dev_system.webui as webui


@pytest.fixture
def _cfg(tmp_path, monkeypatch):
    class _C:
        storage_root = str(tmp_path)
        database_url = "sqlite:///:memory:"

    monkeypatch.setattr(webui, "_config", lambda: _C())
    return _C()


def _write_spec(tmp_path, spec_id: str, findings=None):
    """Write a minimal task spec JSON with optional findings list."""
    out = Path(tmp_path) / "task_specs"
    out.mkdir(parents=True, exist_ok=True)
    data = {
        "status": "done",
        "idea": "add X",
        "repo": "/repo",
        "task": {"id": "TASK-1", "title": "Add X", "objective": "do X"},
        "facets": {
            "test_cases": {"status": "filled", "content": "some test", "reason": ""},
        },
        "approved": False,
    }
    if findings is not None:
        data["findings"] = findings
    (out / f"{spec_id}.json").write_text(json.dumps(data), encoding="utf-8")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_findings_card_shown_when_present(_cfg, tmp_path):
    """Spec JSON with a findings list → page shows 'Spec self-review' card with dimension+message."""
    spec_id = "selfrev001aaa"
    _write_spec(tmp_path, spec_id, findings=[
        {
            "section": "global",
            "dimension": "scope_decomposition",
            "severity": "error",
            "message": "This task is too large and should be split",
            "fix": "Split into two tasks",
        }
    ])
    html_body = webui._task_spec_page(spec_id).decode("utf-8")
    assert "Spec self-review" in html_body
    assert "scope_decomposition" in html_body
    assert "This task is too large and should be split" in html_body


def test_findings_card_shows_severity_and_section(_cfg, tmp_path):
    """Findings card shows severity and section info for each finding."""
    spec_id = "selfrev002bbb"
    _write_spec(tmp_path, spec_id, findings=[
        {
            "section": "proposal",
            "dimension": "placeholder",
            "severity": "warning",
            "message": "TBD left in proposal",
            "fix": "Fill in the placeholder",
        }
    ])
    html_body = webui._task_spec_page(spec_id).decode("utf-8")
    assert "Spec self-review" in html_body
    assert "placeholder" in html_body
    assert "warning" in html_body
    assert "proposal" in html_body
    assert "TBD left in proposal" in html_body


def test_findings_card_shows_fix_hint(_cfg, tmp_path):
    """Fix hint is rendered when present."""
    spec_id = "selfrev003ccc"
    _write_spec(tmp_path, spec_id, findings=[
        {
            "section": "global",
            "dimension": "ambiguity",
            "severity": "warning",
            "message": "Ambiguous wording",
            "fix": "Clarify the requirement",
        }
    ])
    html_body = webui._task_spec_page(spec_id).decode("utf-8")
    assert "Clarify the requirement" in html_body


def test_no_findings_key_no_card(_cfg, tmp_path):
    """Spec JSON with no 'findings' key → page renders normally, no self-review card."""
    spec_id = "selfrev004ddd"
    _write_spec(tmp_path, spec_id, findings=None)  # no findings key at all
    html_body = webui._task_spec_page(spec_id).decode("utf-8")
    assert "Spec self-review" not in html_body


def test_empty_findings_list_no_card(_cfg, tmp_path):
    """Spec JSON with findings:[] → no self-review card."""
    spec_id = "selfrev005eee"
    _write_spec(tmp_path, spec_id, findings=[])
    html_body = webui._task_spec_page(spec_id).decode("utf-8")
    assert "Spec self-review" not in html_body


def test_html_special_chars_escaped(_cfg, tmp_path):
    """Finding text with HTML special chars must be escaped — no raw <script> in output."""
    spec_id = "selfrev006fff"
    _write_spec(tmp_path, spec_id, findings=[
        {
            "section": "global",
            "dimension": "placeholder",
            "severity": "error",
            "message": "<script>alert('xss')</script> & bad < chars",
            "fix": "Fix <this> & that",
        }
    ])
    html_body = webui._task_spec_page(spec_id).decode("utf-8")
    # raw script tag must NOT appear
    assert "<script>alert('xss')</script>" not in html_body
    # escaped versions must appear
    assert "&lt;script&gt;" in html_body
    assert "&amp;" in html_body


def test_multiple_findings_all_shown(_cfg, tmp_path):
    """All findings are rendered when there are multiple."""
    spec_id = "selfrev007ggg"
    _write_spec(tmp_path, spec_id, findings=[
        {
            "section": "proposal",
            "dimension": "placeholder",
            "severity": "error",
            "message": "First finding placeholder",
            "fix": "",
        },
        {
            "section": "global",
            "dimension": "internal_consistency",
            "severity": "warning",
            "message": "Second finding inconsistency",
            "fix": "Resolve contradiction",
        },
    ])
    html_body = webui._task_spec_page(spec_id).decode("utf-8")
    assert "First finding placeholder" in html_body
    assert "Second finding inconsistency" in html_body
    assert "internal_consistency" in html_body


def test_finding_without_fix_renders_ok(_cfg, tmp_path):
    """Finding with empty fix string renders without error."""
    spec_id = "selfrev008hhh"
    _write_spec(tmp_path, spec_id, findings=[
        {
            "section": "global",
            "dimension": "scope_decomposition",
            "severity": "error",
            "message": "Task is too big",
            "fix": "",
        }
    ])
    html_body = webui._task_spec_page(spec_id).decode("utf-8")
    assert "Spec self-review" in html_body
    assert "Task is too big" in html_body
