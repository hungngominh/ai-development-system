"""webui reject-with-reason wires into the failure-learning loop."""
from __future__ import annotations

import ai_dev_system.webui as webui
from ai_dev_system.config import Config
from ai_dev_system.db.connection import get_connection
from ai_dev_system.db.helpers import new_uuid
from ai_dev_system.db.migrator import apply_schema
from ai_dev_system.rules.registry import RuleRegistry


def _seed_failed_task(url: str) -> tuple[str, str]:
    conn = get_connection(url)
    apply_schema(conn)
    run_id = new_uuid()
    conn.execute(
        "INSERT INTO runs (run_id, project_id, status, title, current_artifacts, metadata) "
        "VALUES (?, 'adhoc', 'RUNNING_EXECUTION', 't', '{}', '{}')",
        (run_id,),
    )
    tr = new_uuid()
    conn.execute(
        "INSERT INTO task_runs (task_run_id, run_id, task_id, attempt_number, status, "
        "agent_type, input_artifact_ids, resolved_dependencies, promoted_outputs, started_at) "
        "VALUES (?, ?, 'TASK-ADHOC', 1, 'FAILED', 'RepoBranchAgent', '[]', '[]', '[]', "
        "CURRENT_TIMESTAMP)",
        (tr, run_id),
    )
    conn.commit()
    conn.close()
    return run_id, tr


def test_reject_with_reason_mints_rule(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'c.db'}"
    run_id, tr = _seed_failed_task(url)
    monkeypatch.setattr(webui, "_config",
                        lambda: Config(storage_root=str(tmp_path), database_url=url))

    name = webui._learn_from_rejection(
        "spec1", run_id, {"type": "coding", "tags": []},
        "endpoint ignores auth check", rules_dir=tmp_path,
    )

    assert name and name.startswith("learned-")
    match = RuleRegistry(tmp_path).match_rules({"task_type": "coding", "tags": []})
    assert any("ignores auth" in r for r in match.file_rules)

    # Provenance event committed and traced to the task_run.
    conn = get_connection(url)
    rows = conn.execute(
        "SELECT task_run_id FROM events WHERE event_type = 'RULE_LEARNED'"
    ).fetchall()
    conn.close()
    assert len(rows) == 1 and rows[0]["task_run_id"] == tr


def test_reject_without_reason_learns_nothing(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'c.db'}"
    run_id, _ = _seed_failed_task(url)
    monkeypatch.setattr(webui, "_config",
                        lambda: Config(storage_root=str(tmp_path), database_url=url))

    result = webui._learn_from_rejection(
        "spec1", run_id, {"type": "coding", "tags": []}, "", rules_dir=tmp_path,
    )
    assert result is None
    assert list(tmp_path.glob("learned-*.yaml")) == []
