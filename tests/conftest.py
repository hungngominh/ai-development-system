import json
import os
import uuid
import pytest
import psycopg
from ai_dev_system.config import Config


@pytest.fixture(scope="session")
def config():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise ValueError(
            "DATABASE_URL must be set to run integration tests. "
            "Example: export DATABASE_URL=postgresql://user:pass@host/db"
        )
    os.environ.setdefault("STORAGE_ROOT", "/tmp/ai-dev-test")
    return Config.from_env()


@pytest.fixture
def conn(config):
    """One connection per test. Everything is rolled back after, even if test raises."""
    c = psycopg.connect(config.database_url, autocommit=False, row_factory=psycopg.rows.dict_row)
    # Return UUID columns as plain strings so tests can compare with str(uuid.uuid4())
    from psycopg.adapt import Loader
    class UUIDTextLoader(Loader):
        def load(self, data):
            return data.tobytes().decode() if hasattr(data, "tobytes") else data.decode()
    c.adapters.register_loader("uuid", UUIDTextLoader)
    try:
        c.execute("BEGIN")
        yield c
    finally:
        c.execute("ROLLBACK")
        c.close()


@pytest.fixture
def project_id():
    return str(uuid.uuid4())


@pytest.fixture
def seed_run(conn, project_id):
    """Insert a minimal run row for testing."""
    run_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO runs (run_id, project_id, status, title, current_artifacts, metadata)
        VALUES (%s, %s, 'RUNNING_PHASE_3', 'Test Run', '{}', '{}')
    """, (run_id, project_id))
    return run_id


@pytest.fixture
def seed_task_run(conn, seed_run):
    """Insert a READY task_run row."""
    task_run_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO task_runs (
            task_run_id, run_id, task_id, attempt_number, status,
            agent_type, input_artifact_ids, resolved_dependencies,
            promoted_outputs
        ) VALUES (%s, %s, 'TASK-1', 1, 'READY', 'StubAgent', '{}', '{}', '[]')
    """, (task_run_id, seed_run))
    return task_run_id


@pytest.fixture
def seed_graph_artifact(conn, seed_run, tmp_path):
    """TASK_GRAPH_APPROVED artifact backed by a real file for materializer tests."""
    artifact_id = str(uuid.uuid4())
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
    conn.execute("""
        INSERT INTO artifacts (
            artifact_id, run_id, artifact_type, version, status, created_by,
            input_artifact_ids, content_ref, content_checksum, content_size
        ) VALUES (%s, %s, 'TASK_GRAPH_APPROVED', 1, 'ACTIVE', 'system',
                  '{}', %s, 'abc123', 0)
    """, (artifact_id, seed_run, str(graph_dir)))
    return artifact_id


@pytest.fixture
def seed_pending_task_run(conn, seed_run):
    """Insert a PENDING task_run with no dependencies (ready to be resolved)."""
    task_run_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO task_runs (
            task_run_id, run_id, task_id, attempt_number, status,
            agent_type, input_artifact_ids, resolved_dependencies, promoted_outputs,
            retry_count
        ) VALUES (%s, %s, 'TASK-PARSE', 1, 'PENDING',
                  'SpecAnalyst', '{}', '{}', '[]', 0)
    """, (task_run_id, seed_run))
    return task_run_id
