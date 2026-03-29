import os
import uuid
import pytest
import psycopg
from ai_dev_system.config import Config

DATABASE_URL = "REDACTED_TEST_DATABASE_URL"


@pytest.fixture(scope="session")
def config():
    os.environ.setdefault("STORAGE_ROOT", "/tmp/ai-dev-test")
    os.environ["DATABASE_URL"] = DATABASE_URL
    return Config.from_env()


@pytest.fixture
def conn(config):
    """One connection per test, rolled back after."""
    c = psycopg.connect(config.database_url, autocommit=False, row_factory=psycopg.rows.dict_row)
    c.execute("BEGIN")
    yield c
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
