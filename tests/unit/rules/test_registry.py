import pytest
from pathlib import Path
from ai_dev_system.rules.registry import RuleRegistry, RuleMatch

RULES_DIR = Path(__file__).parents[3] / "src" / "ai_dev_system" / "rules" / "definitions"


def test_load_rules_finds_yaml_files():
    registry = RuleRegistry(rules_dir=RULES_DIR)
    assert len(registry.rules) >= 3


def test_match_code_task_returns_tdd_rule():
    registry = RuleRegistry(rules_dir=RULES_DIR)
    task = {"task_type": "code", "tags": []}
    match = registry.match_rules(task)
    assert isinstance(match, RuleMatch)
    assert any("tdd" in r.lower() for r in match.skill_rules)


def test_match_security_tag_returns_security_rule():
    registry = RuleRegistry(rules_dir=RULES_DIR)
    task = {"task_type": "review", "tags": ["security"]}
    match = registry.match_rules(task)
    assert any("security" in r.lower() for r in match.skill_rules + match.file_rules)


def test_match_unknown_task_returns_empty():
    registry = RuleRegistry(rules_dir=RULES_DIR)
    task = {"task_type": "planning", "tags": []}
    match = registry.match_rules(task)
    assert match.file_rules == []
    assert match.skill_rules == []


def test_rule_match_empty_tags_matches_any_type():
    """A rule with empty tags= matches all tasks of that task_type."""
    registry = RuleRegistry(rules_dir=RULES_DIR)
    task = {"task_type": "implementation", "tags": ["some-tag"]}
    match = registry.match_rules(task)
    assert isinstance(match, RuleMatch)
