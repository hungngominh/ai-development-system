"""Verify that runner.py spawns multiple workers and they execute tasks concurrently."""
from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path

import pytest
from ai_dev_system.agents.base import AgentResult
from ai_dev_system.config import Config
from ai_dev_system.db.connection import get_connection
from ai_dev_system.engine.runner import run_execution


def _make_db(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'test.db'}"


def _bootstrap_run(conn, run_id: str) -> None:
    """Insert a minimal run row in RUNNING_EXECUTION state."""
    conn.execute(
        """
        INSERT INTO runs (run_id, project_id, status, title, current_artifacts, metadata)
        VALUES (?, 'test-proj', 'RUNNING_EXECUTION', 'Parallel test', '{}', '{}')
        """,
        (run_id,),
    )
    conn.commit()


def _create_task_graph_artifact(conn, run_id: str, tasks: list[dict], storage_root: str) -> str:
    import hashlib, json
    artifact_id = uuid.uuid4().hex
    from pathlib import Path as P
    artifact_dir = P(storage_root) / "task_execs" / run_id / "task_graph"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    content = json.dumps({"tasks": tasks}, indent=2, ensure_ascii=False)
    (artifact_dir / "task_graph.json").write_text(content, encoding="utf-8")
    checksum = "sha256:" + hashlib.sha256(content.encode()).hexdigest()
    conn.execute(
        """
        INSERT INTO artifacts
            (artifact_id, run_id, artifact_type, version, status, created_by,
             input_artifact_ids, content_ref, content_checksum, content_size, annotations)
        VALUES (?, ?, 'TASK_GRAPH_APPROVED', 1, 'ACTIVE', 'system',
                '[]', ?, ?, ?, '{}')
        """,
        (artifact_id, run_id, str(artifact_dir), checksum, len(content)),
    )
    conn.commit()
    return artifact_id


class SlowStubAgent:
    """Agent that records concurrency: multiple calls overlap if workers are parallel."""

    def __init__(self, sleep_s: float = 0.2) -> None:
        self.sleep_s = sleep_s
        self.active = 0
        self.max_concurrent = 0
        self._lock = threading.Lock()
        self.call_times: list[float] = []

    def run(self, task_id, output_path, promoted_outputs=(), context=None,
            timeout_s=60.0, file_rules=()):
        with self._lock:
            self.active += 1
            self.max_concurrent = max(self.max_concurrent, self.active)
            self.call_times.append(time.monotonic())
        Path(output_path).mkdir(parents=True, exist_ok=True)
        time.sleep(self.sleep_s)
        with self._lock:
            self.active -= 1
        return AgentResult(output_path=output_path)


def _three_independent_tasks() -> list[dict]:
    return [
        {
            "id": f"TASK-{i}",
            "execution_type": "atomic",
            "agent_type": "RepoBranchAgent",
            "phase": "implementation",
            "type": "coding",
            "objective": f"Task {i}",
            "description": "",
            "done_definition": "done",
            "verification_steps": [],
            "required_inputs": [],
            "expected_outputs": [],
            "deps": [],
            "facets": {},
        }
        for i in range(1, 4)
    ]


def test_parallel_workers_run_tasks_concurrently(tmp_path):
    """With max_parallel_workers=3 and 3 independent tasks, all run at the same time."""
    db_url = _make_db(tmp_path)
    storage = str(tmp_path / "storage")

    from ai_dev_system.db.migrator import apply_schema
    conn = get_connection(db_url)
    apply_schema(conn)

    run_id = uuid.uuid4().hex
    _bootstrap_run(conn, run_id)
    artifact_id = _create_task_graph_artifact(conn, run_id, _three_independent_tasks(), storage)
    conn.close()

    agent = SlowStubAgent(sleep_s=0.5)
    cfg = Config(storage_root=storage, database_url=db_url,
                 poll_interval_s=0.05, heartbeat_timeout_s=30.0,
                 max_parallel_workers=3)

    start = time.monotonic()
    result = run_execution(run_id, artifact_id, cfg, agent, poll_interval_s=0.05)
    elapsed = time.monotonic() - start

    assert result.status == "COMPLETED"
    # 3 tasks × 0.5s sequential = 1.5s; parallel should be much faster
    assert elapsed < 1.5, f"Expected parallel execution < 1.5s, got {elapsed:.2f}s"
    # At least 2 tasks ran concurrently
    assert agent.max_concurrent >= 2


def test_config_max_parallel_workers_default():
    """Config.from_env() defaults to max_parallel_workers=4."""
    cfg = Config(storage_root="/tmp", database_url="sqlite:///tmp/x.db")
    assert cfg.max_parallel_workers == 4


def test_single_worker_runs_tasks_sequentially(tmp_path):
    """max_parallel_workers=1 runs tasks one at a time."""
    db_url = _make_db(tmp_path)
    storage = str(tmp_path / "storage")

    from ai_dev_system.db.migrator import apply_schema
    conn = get_connection(db_url)
    apply_schema(conn)

    run_id = uuid.uuid4().hex
    _bootstrap_run(conn, run_id)
    artifact_id = _create_task_graph_artifact(conn, run_id, _three_independent_tasks(), storage)
    conn.close()

    agent = SlowStubAgent(sleep_s=0.2)
    cfg = Config(storage_root=storage, database_url=db_url,
                 poll_interval_s=0.05, heartbeat_timeout_s=30.0,
                 max_parallel_workers=1)

    result = run_execution(run_id, artifact_id, cfg, agent, poll_interval_s=0.05)
    assert result.status == "COMPLETED"
    # With 1 worker, max_concurrent must be 1
    assert agent.max_concurrent == 1
