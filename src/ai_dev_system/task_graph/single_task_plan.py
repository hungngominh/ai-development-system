"""Single-task PLAN step: build the TASK-TEST → TASK-IMPL graph from an approved
spec and persist it as a REVIEWABLE artifact before execution. The executor then
runs the *approved* plan instead of rebuilding the graph at exec time.

Plan file: <storage_root>/task_specs/<spec_id>-plan.json
  {"spec_id","branch","tdd_gate","graph":{"tasks":[...]},"approved":bool,"created_at"}

Deterministic: no LLM, no network, no git — safe to run synchronously in a request.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


def _tdd_gate_enabled() -> bool:
    """TDD-first split is ON unless EXEC_TDD_GATE is explicitly falsy."""
    v = os.environ.get("EXEC_TDD_GATE")
    if v is None:
        return True
    return v.strip().lower() not in ("0", "false", "no", "off", "")


def branch_name_for(spec_id: str) -> str:
    return f"ai-dev/task-{spec_id[:8]}"


def build_task_graph(task: dict, facets: dict, branch_name: str) -> dict:
    """Single-task graph. TDD gate on → TASK-TEST → TASK-IMPL (ordered by deps);
    off → the legacy single impl task."""
    base_id = task.get("id") or "TASK-ADHOC"
    objective = task.get("objective") or ""
    description = task.get("description") or ""
    impl_done = task.get("done_definition") or f"Code committed to branch {branch_name}"

    _gate = _tdd_gate_enabled()
    impl_task = {
        "id": f"{base_id}-IMPL" if _gate else base_id,
        "execution_type": "atomic",
        "agent_type": "RepoBranchAgent",
        "phase": "implementation",
        "type": task.get("type") or "coding",
        "objective": objective,
        "description": description,
        "done_definition": impl_done,
        "verification_steps": [],
        "required_inputs": [],
        "expected_outputs": ["implementation_diff"],
        "deps": [],
        "facets": facets,
        "tdd_tests_authored": _gate,
    }
    if not _gate:
        return {"tasks": [impl_task]}

    test_task = {
        "id": f"{base_id}-TEST",
        "execution_type": "atomic",
        "agent_type": "TestAuthorAgent",
        "phase": "test",
        "type": "test",
        "objective": objective,
        "description": description,
        "done_definition": "Failing tests committed from the acceptance source",
        "verification_steps": [],
        "required_inputs": [],
        "expected_outputs": ["test_files"],
        "deps": [],
        "facets": facets,
    }
    impl_task["deps"] = [test_task["id"]]
    return {"tasks": [test_task, impl_task]}


def plan_path(storage_root: str, spec_id: str) -> Path:
    return Path(storage_root) / "task_specs" / f"{spec_id}-plan.json"


def plan_single_task(spec: dict, spec_id: str, *, storage_root: str) -> dict:
    """Build + persist the reviewable plan for an approved spec. approved=False
    until the operator approves it via approve_plan()."""
    task = spec.get("task") or {}
    facets = spec.get("facets") or {}
    branch = branch_name_for(spec_id)
    plan = {
        "spec_id": spec_id,
        "branch": branch,
        "tdd_gate": _tdd_gate_enabled(),
        "graph": build_task_graph(task, facets, branch),
        "approved": False,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    path = plan_path(storage_root, spec_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    return plan


def load_plan(storage_root: str, spec_id: str) -> dict | None:
    path = plan_path(storage_root, spec_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a corrupt plan reads as "no plan" (gate stays closed)
        return None


def approve_plan(storage_root: str, spec_id: str) -> bool:
    """Mark the persisted plan approved. Returns False if no plan file exists."""
    plan = load_plan(storage_root, spec_id)
    if plan is None:
        return False
    plan["approved"] = True
    plan_path(storage_root, spec_id).write_text(
        json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return True
