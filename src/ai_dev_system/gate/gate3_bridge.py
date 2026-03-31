# src/ai_dev_system/gate/gate3_bridge.py
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ai_dev_system.db.repos.events import EventRepo
from ai_dev_system.db.repos.runs import RunRepo


@dataclass
class Gate3Decision:
    criterion_id: str
    action: Literal["SKIP", "ABORT"]   # only FAIL criteria need a decision; PASS is implicit


@dataclass
class Gate3Result:
    run_id: str
    has_remediation: bool
    remediation_graph: dict | None   # TaskGraph JSON when fail criteria → remediation
    aborted: bool


def finalize_gate3(
    run_id: str,
    decisions: list[Gate3Decision],
    storage_root: str,
    conn,
) -> Gate3Result:
    """
    Apply Gate 3 decisions to the current VERIFICATION_REPORT.

    Decision logic:
      - PASS criteria (not in decisions list) → accepted automatically
      - SKIP decision → criterion skipped, not counted as fail
      - ABORT decision → run.status = ABORTED immediately
      - Remaining FAIL criteria (not skipped, not aborted):
          - attempt < 3 → generate RemediationGraph → run.status = RUNNING_PHASE_V
          - attempt ≥ 3 → run.status = PAUSED_AT_GATE_3B (soft limit)
      - All pass/skip → run.status = COMPLETED
    """
    run_repo = RunRepo(conn)
    event_repo = EventRepo(conn)

    # --- Load current VERIFICATION_REPORT ---
    artifact_row = conn.execute(
        """
        SELECT content_ref FROM artifacts
        WHERE run_id = %s AND artifact_type = 'VERIFICATION_REPORT' AND status = 'ACTIVE'
        ORDER BY version DESC LIMIT 1
        """,
        (run_id,),
    ).fetchone()
    if not artifact_row:
        raise ValueError(f"No active VERIFICATION_REPORT found for run {run_id}")

    report_path = os.path.join(artifact_row["content_ref"], "verification_report.json")
    with open(report_path, encoding="utf-8") as f:
        report = json.load(f)

    # --- Attempt counter (before adding the one we just read) ---
    count_row = conn.execute(
        """
        SELECT COUNT(*) AS count FROM artifacts
        WHERE run_id = %s AND artifact_type = 'VERIFICATION_REPORT'
          AND status IN ('ACTIVE', 'SUPERSEDED')
        """,
        (run_id,),
    ).fetchone()
    attempt_count = count_row["count"] if count_row else 1

    # --- Build decision index ---
    decision_map = {d.criterion_id: d.action for d in decisions}

    # --- Check for ABORT ---
    for d in decisions:
        if d.action == "ABORT":
            run_repo.update_status(run_id, "ABORTED")
            event_repo.insert(run_id, "VERIFICATION_COMPLETED", "system",
                              payload={"outcome": "ABORTED"})
            return Gate3Result(run_id=run_id, has_remediation=False,
                               remediation_graph=None, aborted=True)

    # --- Classify criteria ---
    fail_criteria = [
        c for c in report["criteria"]
        if c["verdict"] == "FAIL" and decision_map.get(c["criterion_id"]) != "SKIP"
    ]

    if not fail_criteria:
        # All pass or all skipped → COMPLETED
        run_repo.update_status(run_id, "COMPLETED")
        event_repo.insert(run_id, "VERIFICATION_COMPLETED", "system",
                          payload={"outcome": "COMPLETED"})
        return Gate3Result(run_id=run_id, has_remediation=False,
                           remediation_graph=None, aborted=False)

    # --- Remaining fails: check attempt limit ---
    if attempt_count >= 3:
        run_repo.update_status(run_id, "PAUSED_AT_GATE_3B")
        event_repo.insert(run_id, "VERIFICATION_COMPLETED", "system",
                          payload={"outcome": "PAUSED_AT_GATE_3B", "attempt": attempt_count})
        return Gate3Result(run_id=run_id, has_remediation=False,
                           remediation_graph=None, aborted=False)

    # --- Generate remediation graph ---
    remediation_graph = _generate_remediation_graph(fail_criteria)
    event_repo.insert(run_id, "REMEDIATION_CREATED", "system",
                      payload={"fail_count": len(fail_criteria)})

    run_repo.update_status(run_id, "RUNNING_PHASE_V")

    return Gate3Result(run_id=run_id, has_remediation=True,
                       remediation_graph=remediation_graph, aborted=False)


def _generate_remediation_graph(fail_criteria: list[dict]) -> dict:
    """
    Minimal remediation graph: one task per failing criterion.
    In production, an LLM would generate this. For v1, stubs are sufficient.
    """
    tasks = []
    for i, c in enumerate(fail_criteria, start=1):
        tasks.append({
            "id": f"REMEDIATE-{c['criterion_id']}",
            "execution_type": "atomic",
            "phase": "remediation",
            "type": "fix",
            "agent_type": "Implementer",
            "objective": f"Fix criterion {c['criterion_id']}: {c['criterion_text']}",
            "description": f"Reasoning from last attempt: {c.get('reasoning', '')}",
            "done_definition": f"Criterion {c['criterion_id']} must now pass verification",
            "verification_steps": [],
            "deps": [f"REMEDIATE-{fail_criteria[i-2]['criterion_id']}"] if i > 1 else [],
            "required_inputs": [],
            "expected_outputs": [],
        })
    return {
        "graph_version": 1,
        "remediation": True,
        "tasks": tasks,
    }
