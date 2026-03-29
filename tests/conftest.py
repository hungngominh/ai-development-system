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
