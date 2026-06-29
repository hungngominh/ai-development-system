"""Project-tier learned-rule loader (<repo>/.ai-dev/rules)."""
from __future__ import annotations

from pathlib import Path

import yaml

from ai_dev_system.rules.project_rules import project_rules_dir, load_project_file_rules


def _write_rule(repo: Path, name: str, task_types, file_rules):
    d = repo / ".ai-dev" / "rules"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.yaml").write_text(
        yaml.safe_dump(
            {"name": name, "applies_to": {"task_types": task_types, "tags": []},
             "file_rules": file_rules, "skill_rules": []},
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_project_rules_dir():
    assert project_rules_dir("/repo") == Path("/repo", ".ai-dev", "rules")


def test_no_dir_returns_empty(tmp_path):
    assert load_project_file_rules(str(tmp_path), {"type": "coding"}) == []


def test_matches_by_type(tmp_path):
    _write_rule(tmp_path, "learned-coding", ["coding"], ["Validate inputs"])
    assert load_project_file_rules(str(tmp_path), {"type": "coding"}) == ["Validate inputs"]


def test_no_match_other_type(tmp_path):
    _write_rule(tmp_path, "learned-coding", ["coding"], ["Validate inputs"])
    assert load_project_file_rules(str(tmp_path), {"type": "docs"}) == []


def test_disabled_env_returns_empty(tmp_path, monkeypatch):
    _write_rule(tmp_path, "learned-coding", ["coding"], ["Validate inputs"])
    monkeypatch.setenv("AI_DEV_PROJECT_RULES", "0")
    assert load_project_file_rules(str(tmp_path), {"type": "coding"}) == []


def test_empty_repo_path_returns_empty():
    assert load_project_file_rules("", {"type": "coding"}) == []
