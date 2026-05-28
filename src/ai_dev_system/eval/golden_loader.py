"""Load golden idea YAML + expected files. Used by metric modules and runners.

Layout (per eval spec):
    src/ai_dev_system/eval/golden/
    ├── ideas/<id>.yaml
    └── expected/<id>/
        ├── decisions_required.yaml
        ├── decisions_forbidden.yaml
        └── notes.md
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


GOLDEN_ROOT = Path(__file__).parent / "golden"


@dataclass
class DecisionPattern:
    """A pattern that should/shouldn't match generated questions."""

    decision_id: str
    why: str
    domain_expected: list[str] = field(default_factory=list)
    patterns: list[re.Pattern] = field(default_factory=list)

    def matches_any(self, question_text: str) -> bool:
        return any(p.search(question_text) for p in self.patterns)


@dataclass
class GoldenIdea:
    """Parsed golden idea: raw input + expected behavior."""

    id: str
    raw_idea: str
    intake_script: dict[str, Any]
    profile: dict[str, Any]
    required_decisions: list[DecisionPattern] = field(default_factory=list)
    forbidden_decisions: list[DecisionPattern] = field(default_factory=list)
    expected_behavior_notes: str = ""

    @property
    def idea_path(self) -> Path:
        return GOLDEN_ROOT / "ideas" / f"{self.id}.yaml"

    @property
    def expected_dir(self) -> Path:
        return GOLDEN_ROOT / "expected" / self.id


def _compile_patterns(raw_decisions: list[dict], key: str) -> list[DecisionPattern]:
    """Compile regex patterns from decisions_required/forbidden YAML entries."""
    out = []
    for entry in raw_decisions:
        patterns_raw = entry.get(key, [])
        compiled = []
        for p in patterns_raw:
            try:
                compiled.append(re.compile(p))
            except re.error as e:
                raise ValueError(
                    f"Bad regex in decision '{entry.get('id')}': {p!r}: {e}"
                ) from e
        out.append(DecisionPattern(
            decision_id=entry["id"],
            why=entry.get("why", ""),
            domain_expected=entry.get("domain_expected", []),
            patterns=compiled,
        ))
    return out


def load_idea(idea_id: str, root: Path | None = None) -> GoldenIdea:
    """Load a single golden idea by ID. Raises FileNotFoundError if missing."""
    root = root or GOLDEN_ROOT

    idea_path = root / "ideas" / f"{idea_id}.yaml"
    if not idea_path.exists():
        raise FileNotFoundError(f"Golden idea not found: {idea_path}")

    with idea_path.open(encoding="utf-8") as f:
        idea_yaml = yaml.safe_load(f)

    expected_dir = root / "expected" / idea_id
    required_path = expected_dir / "decisions_required.yaml"
    forbidden_path = expected_dir / "decisions_forbidden.yaml"

    required = []
    forbidden = []
    if required_path.exists():
        with required_path.open(encoding="utf-8") as f:
            req_yaml = yaml.safe_load(f) or {}
            required = _compile_patterns(
                req_yaml.get("required_decisions", []),
                key="accept_question_patterns",
            )
    if forbidden_path.exists():
        with forbidden_path.open(encoding="utf-8") as f:
            forb_yaml = yaml.safe_load(f) or {}
            forbidden = _compile_patterns(
                forb_yaml.get("forbidden_decisions", []),
                key="reject_question_patterns",
            )

    return GoldenIdea(
        id=idea_yaml["id"],
        raw_idea=idea_yaml["raw_idea"],
        intake_script=idea_yaml.get("intake_script", {}),
        profile=idea_yaml.get("profile", {}),
        required_decisions=required,
        forbidden_decisions=forbidden,
        expected_behavior_notes=idea_yaml.get("expected_behavior_notes", ""),
    )


def list_idea_ids(root: Path | None = None) -> list[str]:
    """Return sorted list of available golden idea IDs."""
    root = root or GOLDEN_ROOT
    ideas_dir = root / "ideas"
    if not ideas_dir.exists():
        return []
    return sorted(p.stem for p in ideas_dir.glob("*.yaml"))


def load_all_ideas(root: Path | None = None) -> list[GoldenIdea]:
    """Load every golden idea found in the root."""
    return [load_idea(i, root) for i in list_idea_ids(root)]
