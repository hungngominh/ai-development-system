"""Test fixtures — SQLite-backed.

Per-test in-memory SQLite database with full schema applied. Each test gets a
fresh DB; tests cannot leak state into each other.

Param style: SQLite uses `?`. PG-era `%s` is gone.
"""
from __future__ import annotations

import json
import uuid

import pytest

from ai_dev_system.db.connection import get_connection
from ai_dev_system.db.helpers import dump_json, new_uuid
from ai_dev_system.db.migrator import apply_schema


@pytest.fixture
def conn():
    """Fresh in-memory SQLite with full schema applied. One connection per test."""
    c = get_connection("sqlite:///:memory:")
    apply_schema(c)
    try:
        yield c
    finally:
        c.close()


@pytest.fixture
def project_id():
    return new_uuid()


@pytest.fixture
def config(tmp_path):
    """Minimal Config for unit tests.

    storage_root → tmp_path (per-test isolated).
    database_url → in-memory sqlite (consumers that actually open this URL get a
    fresh DB; most consumers only need the dataclass fields, not the URL itself,
    because tests pass the `conn` fixture directly).
    """
    from ai_dev_system.config import Config
    return Config(
        storage_root=str(tmp_path / "storage"),
        database_url="sqlite:///:memory:",
        poll_interval_s=0.05,
        heartbeat_interval_s=1.0,
        heartbeat_timeout_s=2.0,
        task_timeout_s=30.0,
    )


@pytest.fixture
def seed_run(conn, project_id):
    """Insert a minimal run row for testing."""
    run_id = new_uuid()
    conn.execute(
        """
        INSERT INTO runs (run_id, project_id, status, title, current_artifacts, metadata)
        VALUES (?, ?, 'RUNNING_PHASE_3', 'Test Run', '{}', '{}')
        """,
        (run_id, project_id),
    )
    return run_id


@pytest.fixture
def seed_task_run(conn, seed_run):
    """Insert a READY task_run row."""
    task_run_id = new_uuid()
    conn.execute(
        """
        INSERT INTO task_runs (
            task_run_id, run_id, task_id, attempt_number, status,
            agent_type, input_artifact_ids, resolved_dependencies, promoted_outputs
        ) VALUES (?, ?, 'TASK-1', 1, 'READY', 'StubAgent', '[]', '[]', '[]')
        """,
        (task_run_id, seed_run),
    )
    return task_run_id


@pytest.fixture
def seed_graph_artifact(conn, seed_run, tmp_path):
    """TASK_GRAPH_APPROVED artifact backed by a real file for materializer tests."""
    artifact_id = new_uuid()
    graph = {
        "graph_version": 1,
        "tasks": [
            {
                "id": "TASK-PARSE", "execution_type": "atomic",
                "phase": "parse_spec", "type": "design",
                "agent_type": "SpecAnalyst",
                "objective": "Parse spec", "description": "", "done_definition": "done",
                "verification_steps": [], "deps": [],
                "required_inputs": [], "expected_outputs": [],
            },
            {
                "id": "TASK-DESIGN", "execution_type": "atomic",
                "phase": "design_solution", "type": "design",
                "agent_type": "Architect",
                "objective": "Design", "description": "", "done_definition": "done",
                "verification_steps": [], "deps": ["TASK-PARSE"],
                "required_inputs": [], "expected_outputs": [],
            },
        ],
    }
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    (graph_dir / "task_graph.json").write_text(json.dumps(graph))
    conn.execute(
        """
        INSERT INTO artifacts (
            artifact_id, run_id, artifact_type, version, status, created_by,
            input_artifact_ids, content_ref, content_checksum, content_size
        ) VALUES (?, ?, 'TASK_GRAPH_APPROVED', 1, 'ACTIVE', 'system',
                  '[]', ?, 'abc123', 0)
        """,
        (artifact_id, seed_run, str(graph_dir)),
    )
    return artifact_id


@pytest.fixture
def file_db_url(tmp_path):
    """File-backed SQLite DB URL — required for subprocess and multi-thread tests.

    Returns a URL like 'sqlite:///tmp_path/test.db' with schema already applied.
    Multiple connections to this URL share the same DB (unlike :memory:).
    """
    db_path = tmp_path / "test.db"
    url = f"sqlite:///{db_path}"
    c = get_connection(url)
    apply_schema(c)
    c.close()
    return url


@pytest.fixture
def file_config(tmp_path, file_db_url):
    """Config backed by a file-based SQLite DB (for subprocess + multi-thread tests)."""
    from ai_dev_system.config import Config
    return Config(
        storage_root=str(tmp_path / "storage"),
        database_url=file_db_url,
        poll_interval_s=0.05,
        heartbeat_interval_s=1.0,
        heartbeat_timeout_s=30.0,
        task_timeout_s=30.0,
    )


@pytest.fixture
def seed_pending_task_run(conn, seed_run):
    """Insert a PENDING task_run with no dependencies (ready to be resolved)."""
    task_run_id = new_uuid()
    conn.execute(
        """
        INSERT INTO task_runs (
            task_run_id, run_id, task_id, attempt_number, status,
            agent_type, input_artifact_ids, resolved_dependencies, promoted_outputs,
            retry_count
        ) VALUES (?, ?, 'TASK-PARSE', 1, 'PENDING',
                  'SpecAnalyst', '[]', '[]', '[]', 0)
        """,
        (task_run_id, seed_run),
    )
    return task_run_id
