# src/ai_dev_system/rules/registry.py
import logging
from dataclasses import dataclass, field
from pathlib import Path
import yaml

logger = logging.getLogger(__name__)


@dataclass
class RuleMatch:
    file_rules: list[str] = field(default_factory=list)
    skill_rules: list[str] = field(default_factory=list)


class RuleRegistry:
    def __init__(self, rules_dir: Path | str):
        self.rules_dir = Path(rules_dir)
        self.rules = self._load_rules()

    def _load_rules(self) -> list[dict]:
        """Load every rule YAML, skipping any that are corrupt.

        A single malformed file (e.g. a half-written learned rule) must not
        crash registry construction for ALL tasks — it is logged and skipped.
        The learning loop writes atomically to avoid producing such files, but
        this guard makes the worker resilient regardless.
        """
        rules = []
        for yaml_file in sorted(self.rules_dir.glob("*.yaml")):
            try:
                with open(yaml_file, encoding="utf-8") as f:
                    rule = yaml.safe_load(f)
            except (OSError, yaml.YAMLError):
                logger.warning("Skipping unreadable rule file %s", yaml_file, exc_info=True)
                continue
            if not isinstance(rule, dict):
                logger.warning("Skipping non-mapping rule file %s", yaml_file)
                continue
            rules.append(rule)
        return rules

    def match_rules(self, task: dict) -> RuleMatch:
        """Return file_rules + skill_rules for this task.

        A rule matches if:
          - task.task_type is in rule.applies_to.task_types, OR
          - any task tag is in rule.applies_to.tags (non-empty tags only)
        Empty tags in rule = match all tasks of matching type.
        """
        task_type = task.get("task_type", "")
        task_tags = set(task.get("tags", []))

        file_rules: list[str] = []
        skill_rules: list[str] = []

        for rule in self.rules:
            applies = rule.get("applies_to", {})
            rule_types = set(applies.get("task_types", []))
            rule_tags = set(applies.get("tags", []))

            type_match = task_type in rule_types
            tag_match = bool(rule_tags and task_tags & rule_tags)

            if type_match or tag_match:
                file_rules.extend(rule.get("file_rules", []))
                skill_rules.extend(rule.get("skill_rules", []))

        return RuleMatch(file_rules=file_rules, skill_rules=skill_rules)
