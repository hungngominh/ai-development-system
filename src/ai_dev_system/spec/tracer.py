# src/ai_dev_system/spec/tracer.py
"""Spec Generation v2 — Trace Map Builder (SP8).

Parses inline source markers from generated spec sections:
  [brief:field_name]          — references a brief field
  [decision:decision_id]      — references a locked decision
  [answer:Q<id>]              — references a debate Q&A result

Builds a JSON-serializable trace_map dict that records which source markers
appear on which lines in each section, enabling traceability between spec
claims and their originating inputs.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

_BRIEF_MARKER = re.compile(r"\[brief:([\w_-]+)\]")
_DECISION_MARKER = re.compile(r"\[decision:([\w_-]+)\]")
_ANSWER_MARKER = re.compile(r"\[answer:(Q[\w_-]+)\]", re.IGNORECASE)


@dataclass
class MarkerEntry:
    type: str       # "brief_field" | "decision" | "question_answer"
    id: str         # field name, decision ID, or QID
    line: int       # 1-indexed line number in the section content


@dataclass
class SectionTrace:
    section: str
    markers: list[MarkerEntry] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "markers": [{"type": m.type, "id": m.id, "line": m.line} for m in self.markers],
            "marker_count": len(self.markers),
        }


def extract_markers(content: str) -> list[MarkerEntry]:
    """Find all source markers in a section's markdown content."""
    entries: list[MarkerEntry] = []
    for line_no, line in enumerate(content.splitlines(), start=1):
        for m in _BRIEF_MARKER.finditer(line):
            entries.append(MarkerEntry(type="brief_field", id=m.group(1), line=line_no))
        for m in _DECISION_MARKER.finditer(line):
            entries.append(MarkerEntry(type="decision", id=m.group(1), line=line_no))
        for m in _ANSWER_MARKER.finditer(line):
            entries.append(MarkerEntry(type="question_answer", id=m.group(1).upper(), line=line_no))
    return entries


def build_trace_map(
    drafts: dict,   # section_name -> SectionDraft
    brief: dict,
    decisions: list,
    questions: list,
) -> dict:
    """Build a JSON-serializable trace map from all section drafts.

    Returns a dict with schema version and per-section marker lists.
    Skipped sections (degraded) are omitted.
    """
    section_traces: dict[str, dict] = {}
    for section, draft in drafts.items():
        if getattr(draft, "degraded", False):
            continue
        markers = extract_markers(draft.content)
        section_traces[section] = SectionTrace(section=section, markers=markers).to_dict()

    # Identify brief fields and decisions referenced vs. expected
    all_brief_fields = set(brief.keys()) - {"brief_version"}
    referenced_fields: set[str] = set()
    referenced_decisions: set[str] = set()
    referenced_answers: set[str] = set()
    for st in section_traces.values():
        for m in st["markers"]:
            if m["type"] == "brief_field":
                referenced_fields.add(m["id"])
            elif m["type"] == "decision":
                referenced_decisions.add(m["id"])
            elif m["type"] == "question_answer":
                referenced_answers.add(m["id"])

    return {
        "schema": 1,
        "section_traces": section_traces,
        "summary": {
            "total_markers": sum(st["marker_count"] for st in section_traces.values()),
            "referenced_brief_fields": sorted(referenced_fields),
            "unreferenced_brief_fields": sorted(all_brief_fields - referenced_fields),
            "referenced_decisions": sorted(referenced_decisions),
            "referenced_answers": sorted(referenced_answers),
        },
    }


def write_trace_map(trace_map: dict, output_dir: Path) -> Path:
    """Write trace_map.json to output_dir. Returns the path."""
    path = output_dir / "trace_map.json"
    path.write_text(json.dumps(trace_map, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
