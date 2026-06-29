from __future__ import annotations

import json

import pytest

from ai_dev_system.task_graph.single_task_plan import (
    build_task_graph, branch_name_for, plan_path, plan_single_task,
    load_plan, approve_plan,
)


def _task():
    return {"id": "TASK-ADHOC", "objective": "Add X", "description": "desc",
            "done_definition": "done", "type": "coding"}


def _spec():
    return {"idea": "add X", "repo": "/repo",
            "task": _task(),
            "facets": {"test_cases": {"status": "filled", "content": "t", "reason": ""}}}


def test_branch_name_for_uses_first_8():
    assert branch_name_for("abcdef0123456789") == "ai-dev/task-abcdef01"


def test_build_task_graph_tdd_on_two_tasks_with_dep(monkeypatch):
    monkeypatch.delenv("EXEC_TDD_GATE", raising=False)
    g = build_task_graph(_task(), {"x": 1}, "ai-dev/task-abc")
    ids = [t["id"] for t in g["tasks"]]
    assert ids == ["TASK-ADHOC-TEST", "TASK-ADHOC-IMPL"]
    impl = g["tasks"][1]
    assert impl["deps"] == ["TASK-ADHOC-TEST"]
    assert g["tasks"][0]["agent_type"] == "TestAuthorAgent"
    assert impl["agent_type"] == "RepoBranchAgent"


def test_build_task_graph_tdd_off_single_task(monkeypatch):
    monkeypatch.setenv("EXEC_TDD_GATE", "0")
    g = build_task_graph(_task(), {"x": 1}, "ai-dev/task-abc")
    assert [t["id"] for t in g["tasks"]] == ["TASK-ADHOC"]
    assert g["tasks"][0]["agent_type"] == "RepoBranchAgent"


def test_plan_single_task_persists_unapproved_with_graph(tmp_path, monkeypatch):
    monkeypatch.delenv("EXEC_TDD_GATE", raising=False)
    plan = plan_single_task(_spec(), "spec1234abcd", storage_root=str(tmp_path))
    assert plan["approved"] is False
    assert plan["branch"] == "ai-dev/task-spec1234"
    assert [t["id"] for t in plan["graph"]["tasks"]] == ["TASK-ADHOC-TEST", "TASK-ADHOC-IMPL"]
    on_disk = json.loads(plan_path(str(tmp_path), "spec1234abcd").read_text(encoding="utf-8"))
    assert on_disk == plan


def test_load_plan_missing_returns_none(tmp_path):
    assert load_plan(str(tmp_path), "nope") is None


def test_approve_plan_round_trip(tmp_path):
    plan_single_task(_spec(), "spec1234abcd", storage_root=str(tmp_path))
    assert approve_plan(str(tmp_path), "spec1234abcd") is True
    assert load_plan(str(tmp_path), "spec1234abcd")["approved"] is True


def test_approve_plan_no_file_returns_false(tmp_path):
    assert approve_plan(str(tmp_path), "missing") is False
