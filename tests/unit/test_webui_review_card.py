"""webui renders the review-gate result card."""
from __future__ import annotations

from ai_dev_system.webui import _render_review_card


def test_clean_card_shows_pass():
    html = _render_review_card({
        "review_status": "clean", "tests_ran": True, "tests_passed": True,
        "findings": [], "rounds_fixed": 0,
    })
    assert "sạch" in html
    assert "PASS" in html


def test_flagged_card_lists_findings_and_warns():
    html = _render_review_card({
        "review_status": "flagged", "tests_ran": True, "tests_passed": False,
        "findings": [{"severity": "high", "file": "a.py", "line": 7, "issue": "no caller"}],
        "rounds_fixed": 2,
    })
    assert "chưa giải quyết" in html
    assert "FAIL" in html
    assert "a.py:7" in html
    assert "HIGH" in html
    assert "2 vòng" in html


def test_no_review_returns_empty():
    assert _render_review_card(None) == ""
    assert _render_review_card({}) == ""


def test_no_tests_card():
    html = _render_review_card({
        "review_status": "clean", "tests_ran": False, "tests_passed": False,
        "findings": [], "rounds_fixed": 0,
    })
    assert "không" in html.lower()
