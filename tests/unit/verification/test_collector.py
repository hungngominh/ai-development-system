import os
import json
import uuid
from unittest.mock import MagicMock
from ai_dev_system.verification.collector import collect_evidence
from ai_dev_system.verification.report import TaskSummaryEntry


def _make_conn(task_runs: list[dict], artifacts: dict[str, dict]) -> MagicMock:
    """Build a mock conn that responds to the two queries in collect_evidence."""
    conn = MagicMock()

    def execute_side_effect(query, params=None):
        cursor = MagicMock()
        q = query.strip().lower()
        if "from task_runs" in q:
            cursor.fetchall.return_value = task_runs
        elif "from artifacts" in q:
            artifact_id = params[0] if params else None
            row = artifacts.get(str(artifact_id))
            cursor.fetchone.return_value = row
        return cursor

    conn.execute.side_effect = execute_side_effect
    return conn


def test_collect_evidence_empty_run():
    conn = _make_conn(task_runs=[], artifacts={})
    summaries, evidence = collect_evidence("run-1", conn)
    assert summaries == {}
    assert evidence == []


def test_collect_evidence_success_task_no_artifact(tmp_path):
    task_id = "TASK-1"
    task_run = {
        "task_id": task_id,
        "status": "SUCCESS",
        "output_artifact_id": None,
    }
    conn = _make_conn(task_runs=[task_run], artifacts={})
    summaries, evidence = collect_evidence("run-1", conn)
    assert task_id in summaries
    entry = summaries[task_id]
    assert entry.done_definition_met is True
    assert entry.output_artifact_id is None
    assert entry.verification_step_results == []


def test_collect_evidence_reads_output_file(tmp_path):
    artifact_id = str(uuid.uuid4())
    task_id = "TASK-2"

    # Write a fake output file
    artifact_dir = tmp_path / "artifact"
    artifact_dir.mkdir()
    output_file = artifact_dir / "output.txt"
    output_file.write_text("task output: all tests passed")

    task_run = {
        "task_id": task_id,
        "status": "SUCCESS",
        "output_artifact_id": artifact_id,
    }
    artifact_row = {"content_ref": str(artifact_dir)}
    conn = _make_conn(
        task_runs=[task_run],
        artifacts={artifact_id: artifact_row},
    )
    summaries, evidence = collect_evidence("run-1", conn)
    assert task_id in summaries
    assert summaries[task_id].output_artifact_id == artifact_id
    # Evidence should contain text from the output file
    assert any("all tests passed" in e for e in evidence)


def test_collect_evidence_multiple_tasks(tmp_path):
    id1 = str(uuid.uuid4())
    id2 = str(uuid.uuid4())

    dir1 = tmp_path / "a1"; dir1.mkdir()
    (dir1 / "out.txt").write_text("task1 result")
    dir2 = tmp_path / "a2"; dir2.mkdir()
    (dir2 / "out.txt").write_text("task2 result")

    task_runs = [
        {"task_id": "T1", "status": "SUCCESS", "output_artifact_id": id1},
        {"task_id": "T2", "status": "SUCCESS", "output_artifact_id": id2},
    ]
    artifacts = {
        id1: {"content_ref": str(dir1)},
        id2: {"content_ref": str(dir2)},
    }
    conn = _make_conn(task_runs=task_runs, artifacts=artifacts)
    summaries, evidence = collect_evidence("run-1", conn)
    assert len(summaries) == 2
    assert len(evidence) == 2
