# tests/integration/test_resolver.py
import uuid
import pytest
from ai_dev_system.engine.resolver import resolve_dependencies

def seed_task(conn, run_id, task_id, status, deps=None):
    tid = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO task_runs (task_run_id, run_id, task_id, attempt_number, status,
            agent_type, input_artifact_ids, resolved_dependencies, promoted_outputs)
        VALUES (%s, %s, %s, 1, %s, 'StubAgent', '{}', %s, '[]')
    """, (tid, run_id, task_id, status, deps or []))
    return tid

def test_task_with_no_deps_moves_to_ready(conn, seed_run):
    tid = seed_task(conn, seed_run, "TASK-1", "PENDING", deps=[])
    resolve_dependencies(conn, seed_run)
    row = conn.execute("SELECT status FROM task_runs WHERE task_run_id = %s", (tid,)).fetchone()
    assert row["status"] == "READY"

def test_task_with_unsatisfied_dep_stays_pending(conn, seed_run):
    seed_task(conn, seed_run, "TASK-1", "PENDING", deps=[])   # dep, still PENDING
    tid2 = seed_task(conn, seed_run, "TASK-2", "PENDING", deps=["TASK-1"])
    resolve_dependencies(conn, seed_run)
    row = conn.execute("SELECT status FROM task_runs WHERE task_run_id = %s", (tid2,)).fetchone()
    assert row["status"] == "PENDING"

def test_task_with_satisfied_dep_moves_to_ready(conn, seed_run):
    seed_task(conn, seed_run, "TASK-1", "SUCCESS", deps=[])
    tid2 = seed_task(conn, seed_run, "TASK-2", "PENDING", deps=["TASK-1"])
    resolve_dependencies(conn, seed_run)
    row = conn.execute("SELECT status FROM task_runs WHERE task_run_id = %s", (tid2,)).fetchone()
    assert row["status"] == "READY"

def test_skipped_dep_counts_as_satisfied(conn, seed_run):
    seed_task(conn, seed_run, "TASK-1", "SKIPPED", deps=[])
    tid2 = seed_task(conn, seed_run, "TASK-2", "PENDING", deps=["TASK-1"])
    resolve_dependencies(conn, seed_run)
    row = conn.execute("SELECT status FROM task_runs WHERE task_run_id = %s", (tid2,)).fetchone()
    assert row["status"] == "READY"
