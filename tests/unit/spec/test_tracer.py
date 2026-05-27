"""Unit tests for SP8 — Trace Map Builder (tracer.py).

Tests cover:
- extract_markers: finds [brief:field] markers
- extract_markers: finds [decision:id] markers
- extract_markers: finds [answer:Q1] markers (case-insensitive)
- extract_markers: QID normalised to uppercase
- extract_markers: correct line numbers
- extract_markers: no markers → empty list
- extract_markers: multiple markers on same line
- build_trace_map: sections without markers → marker_count=0
- build_trace_map: degraded sections skipped
- build_trace_map: summary counts referenced fields
- build_trace_map: unreferenced_brief_fields computed correctly
- write_trace_map: writes valid JSON to output_dir/trace_map.json
- pipeline integration: trace_map_path set when require_trace_map=True
- pipeline integration: trace_map_path is None when require_trace_map=False
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ai_dev_system.spec.tracer import build_trace_map, extract_markers, write_trace_map
from ai_dev_system.spec.generators.base import SectionDraft


# ---- helpers ----

def _draft(section: str, content: str, degraded: bool = False) -> SectionDraft:
    return SectionDraft(section=section, content=content, degraded=degraded)


def _brief(**kwargs) -> dict:
    base = {"problem_statement": "Build forum", "scope_in": ["voting"], "scope_out": []}
    base.update(kwargs)
    return base


# ---- extract_markers ----


def test_extract_brief_marker():
    markers = extract_markers("This uses [brief:scope_in] to define scope.")
    assert len(markers) == 1
    assert markers[0].type == "brief_field"
    assert markers[0].id == "scope_in"
    assert markers[0].line == 1


def test_extract_decision_marker():
    markers = extract_markers("Line1\nUse [decision:db_choice] here.\nLine3")
    assert len(markers) == 1
    assert markers[0].type == "decision"
    assert markers[0].id == "db_choice"
    assert markers[0].line == 2


def test_extract_answer_marker():
    markers = extract_markers("Based on [answer:Q3_auth] we chose JWT.")
    assert len(markers) == 1
    assert markers[0].type == "question_answer"
    assert markers[0].id == "Q3_AUTH"


def test_answer_marker_uppercase_normalised():
    markers = extract_markers("[answer:q5_cache]")
    assert markers[0].id == "Q5_CACHE"


def test_multiple_markers_same_line():
    content = "Uses [brief:scope_in] and [decision:d1] together."
    markers = extract_markers(content)
    assert len(markers) == 2


def test_no_markers_returns_empty():
    markers = extract_markers("This section has no markers.")
    assert markers == []


def test_line_numbers_correct():
    content = "Line1\nLine2 [brief:problem_statement]\nLine3 [decision:x]"
    markers = extract_markers(content)
    lines = {m.id: m.line for m in markers}
    assert lines["problem_statement"] == 2
    assert lines["x"] == 3


# ---- build_trace_map ----


def test_build_trace_map_basic():
    drafts = {
        "functional": _draft("functional", "Uses [brief:scope_in] and [decision:d1]."),
        "proposal": _draft("proposal", "No markers here."),
    }
    tm = build_trace_map(drafts, _brief(), [], [])
    assert tm["schema"] == 1
    assert "functional" in tm["section_traces"]
    assert "proposal" in tm["section_traces"]
    assert tm["section_traces"]["functional"]["marker_count"] == 2
    assert tm["section_traces"]["proposal"]["marker_count"] == 0


def test_build_trace_map_skips_degraded():
    drafts = {
        "functional": _draft("functional", "[brief:scope_in]"),
        "design": _draft("design", "[brief:problem_statement]", degraded=True),
    }
    tm = build_trace_map(drafts, _brief(), [], [])
    assert "functional" in tm["section_traces"]
    assert "design" not in tm["section_traces"]


def test_build_trace_map_summary_referenced_fields():
    drafts = {"functional": _draft("functional", "[brief:scope_in] [brief:problem_statement]")}
    brief = _brief(scope_in=["voting"], problem_statement="Forum")
    tm = build_trace_map(drafts, brief, [], [])
    summary = tm["summary"]
    assert "scope_in" in summary["referenced_brief_fields"]
    assert "problem_statement" in summary["referenced_brief_fields"]


def test_build_trace_map_summary_unreferenced():
    drafts = {"functional": _draft("functional", "[brief:scope_in]")}
    brief = _brief(scope_in=["voting"], problem_statement="Forum", scope_out=[])
    tm = build_trace_map(drafts, brief, [], [])
    unreferenced = tm["summary"]["unreferenced_brief_fields"]
    assert "problem_statement" in unreferenced
    assert "scope_in" not in unreferenced


def test_build_trace_map_total_markers():
    drafts = {
        "functional": _draft("functional", "[brief:scope_in] [decision:d1]"),
        "proposal": _draft("proposal", "[answer:Q1]"),
    }
    tm = build_trace_map(drafts, _brief(), [], [])
    assert tm["summary"]["total_markers"] == 3


# ---- write_trace_map ----


def test_write_trace_map_creates_file():
    tm = {"schema": 1, "section_traces": {}, "summary": {}}
    with tempfile.TemporaryDirectory() as tmpdir:
        path = write_trace_map(tm, Path(tmpdir))
        assert path.name == "trace_map.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["schema"] == 1


# ---- pipeline integration ----


def test_pipeline_writes_trace_map_when_required(tmp_path):
    from ai_dev_system.spec.pipeline import run_spec_pipeline, SpecPipelineConfig

    llm = MagicMock()
    llm.complete.return_value = (
        "# Proposal\n[brief:problem_statement] describes the problem."
    )

    brief = {
        "brief_version": 2,
        "problem_statement": "Build a forum",
        "who_feels_pain": "developers",
        "success_metric": "1k users in 3 months",
        "scope_in": ["forum", "voting"],
        "scope_out": [],
        "must_integrate_with": [],
        "existing_auth": None,
        "deployment_target": "cloud",
        "performance_sla": None,
        "constraints": [],
        "assumptions": [],
    }
    cfg = SpecPipelineConfig(parallel_sections=False, require_trace_map=True, max_repair_calls=0)
    bundle = run_spec_pipeline(brief, {}, tmp_path, llm, config=cfg)
    assert bundle.trace_map_path is not None
    assert bundle.trace_map_path.exists()
    data = json.loads(bundle.trace_map_path.read_text())
    assert "section_traces" in data


def test_pipeline_no_trace_map_by_default(tmp_path):
    from ai_dev_system.spec.pipeline import run_spec_pipeline, SpecPipelineConfig

    llm = MagicMock()
    llm.complete.return_value = "# Section\nContent."

    brief = {
        "brief_version": 2,
        "problem_statement": "Build a forum",
        "who_feels_pain": "developers",
        "success_metric": "1k users",
        "scope_in": ["forum"],
        "scope_out": [],
        "must_integrate_with": [],
        "existing_auth": None,
        "deployment_target": "cloud",
        "performance_sla": None,
        "constraints": [],
        "assumptions": [],
    }
    cfg = SpecPipelineConfig(parallel_sections=False, max_repair_calls=0)
    bundle = run_spec_pipeline(brief, {}, tmp_path, llm, config=cfg)
    assert bundle.trace_map_path is None
