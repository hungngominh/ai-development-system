"""Integration: failure-learning loop closes through DB events + RuleRegistry."""
from __future__ import annotations

from ai_dev_system.rules.learning import learn_from_failure
from ai_dev_system.rules.registry import RuleRegistry
from ai_dev_system.verification.report import CriterionResult, VerificationReport


def _has_fail_report() -> VerificationReport:
    crit = CriterionResult(
        criterion_id="C1",
        criterion_text="must validate input",
        verdict="FAIL",
        confidence=0.95,
        evidence=["accepted negative quantity"],
        reasoning="input validation missing for negative quantity",
    )
    return VerificationReport(
        run_id="run-1", attempt=1, criteria=[crit],
        overall="HAS_FAIL", task_summary={}, generated_at="2026-06-27T00:00:00+00:00",
    )


def test_learn_emits_rule_learned_event_and_closes_loop(conn, seed_run, seed_task_run, tmp_path):
    task = {"task_run_id": seed_task_run, "task_type": "code", "tags": ["validation"]}

    result = learn_from_failure(
        conn=conn, run_id=seed_run, task=task,
        rules_dir=tmp_path, source="verification", report=_has_fail_report(),
    )
    assert result is not None and result.created

    # Audit event recorded, traced to the failing task_run.
    rows = conn.execute(
        "SELECT event_type, task_run_id, payload FROM events "
        "WHERE event_type = 'RULE_LEARNED'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["task_run_id"] == seed_task_run

    # A sibling task of the same type now receives the corrective lesson.
    match = RuleRegistry(tmp_path).match_rules({"task_type": "code", "tags": []})
    assert any("input validation" in r for r in match.file_rules)


def test_malformed_learned_file_does_not_crash_registry(conn, seed_run, tmp_path):
    # Write a corrupt YAML directly into the rules dir.
    (tmp_path / "learned-broken.yaml").write_text("name: x\n  bad: [unclosed", encoding="utf-8")
    # A valid hand-authored rule alongside it.
    (tmp_path / "ok.yaml").write_text(
        "name: ok\napplies_to:\n  task_types: [code]\n  tags: []\n"
        "file_rules: [be careful]\nskill_rules: []\n",
        encoding="utf-8",
    )
    # Registry construction must not raise; it skips the broken file.
    registry = RuleRegistry(tmp_path)
    match = registry.match_rules({"task_type": "code", "tags": []})
    assert "be careful" in match.file_rules
