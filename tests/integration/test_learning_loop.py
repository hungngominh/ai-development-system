"""Integration: failure-learning loop closes through DB events + RuleRegistry."""
from __future__ import annotations

import json

from ai_dev_system.db.helpers import new_uuid
from ai_dev_system.rules.learning import learn_from_failure
from ai_dev_system.rules.registry import RuleRegistry
from ai_dev_system.verification.pipeline import _learn_from_verification
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


# ── wiring #1a: verification HAS_FAIL → learn_from_failure ────────────────────

def _success_impl_task(conn, run_id: str, task_type: str = "code",
                       tags=("validation",)) -> str:
    """Insert a SUCCESS implementation task_run with a context_snapshot."""
    task_run_id = new_uuid()
    ctx = {"task_id": "TASK-ADHOC", "phase": "implementation",
           "type": task_type, "tags": list(tags)}
    conn.execute(
        """
        INSERT INTO task_runs (
            task_run_id, run_id, task_id, attempt_number, status,
            agent_type, input_artifact_ids, resolved_dependencies, promoted_outputs,
            context_snapshot, started_at
        ) VALUES (?, ?, 'TASK-ADHOC', 1, 'SUCCESS',
                  'RepoBranchAgent', '[]', '[]', '[]', ?, CURRENT_TIMESTAMP)
        """,
        (task_run_id, run_id, json.dumps(ctx)),
    )
    return task_run_id


def test_verification_hasfail_wiring_mints_rule(conn, seed_run, tmp_path):
    tr = _success_impl_task(conn, seed_run, task_type="code", tags=["validation"])
    report = _has_fail_report()

    _learn_from_verification(seed_run, report, conn, rules_dir=tmp_path)

    # A learned rule was written and scoped to the impl task type.
    match = RuleRegistry(tmp_path).match_rules({"task_type": "code", "tags": []})
    assert any("input validation" in r for r in match.file_rules)
    # Provenance event traces back to the failing impl task_run.
    rows = conn.execute(
        "SELECT task_run_id FROM events WHERE event_type = 'RULE_LEARNED'"
    ).fetchall()
    assert len(rows) == 1 and rows[0]["task_run_id"] == tr


def test_verification_all_pass_wiring_is_noop(conn, seed_run, tmp_path):
    _success_impl_task(conn, seed_run)
    passing = VerificationReport(
        run_id=seed_run, attempt=1, criteria=[], overall="ALL_PASS",
        task_summary={}, generated_at="2026-06-27T00:00:00+00:00",
    )
    _learn_from_verification(seed_run, passing, conn, rules_dir=tmp_path)
    assert list(tmp_path.glob("*.yaml")) == []


def test_verification_wiring_skips_non_implementation_tasks(conn, seed_run, tmp_path):
    # A SUCCESS task that is NOT an implementation phase must not be scoped.
    task_run_id = new_uuid()
    ctx = {"task_id": "TASK-DESIGN", "phase": "design_solution", "type": "design", "tags": []}
    conn.execute(
        """
        INSERT INTO task_runs (
            task_run_id, run_id, task_id, attempt_number, status,
            agent_type, input_artifact_ids, resolved_dependencies, promoted_outputs,
            context_snapshot
        ) VALUES (?, ?, 'TASK-DESIGN', 1, 'SUCCESS',
                  'Architect', '[]', '[]', '[]', ?)
        """,
        (task_run_id, seed_run, json.dumps(ctx)),
    )
    _learn_from_verification(seed_run, _has_fail_report(), conn, rules_dir=tmp_path)
    assert list(tmp_path.glob("*.yaml")) == []
