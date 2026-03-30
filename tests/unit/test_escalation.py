import uuid
import pytest
from ai_dev_system.engine.escalation import resolve_escalation
from ai_dev_system.db.repos.escalations import EscalationRepo


def _insert_failed_final(conn, run_id, task_id="TASK-A"):
    tid = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO task_runs (
            task_run_id, run_id, task_id, attempt_number, status,
            agent_type, input_artifact_ids, resolved_dependencies, promoted_outputs,
            retry_count
        ) VALUES (%s, %s, %s, 3, 'FAILED_FINAL', 'agent', '{}', '{}', '[]', 2)
    """, (tid, run_id, task_id))
    return tid


def _insert_blocked(conn, run_id, task_id, deps):
    tid = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO task_runs (
            task_run_id, run_id, task_id, attempt_number, status,
            agent_type, input_artifact_ids, resolved_dependencies, promoted_outputs,
            retry_count
        ) VALUES (%s, %s, %s, 1, 'BLOCKED_BY_FAILURE', 'agent', '{}', %s, '[]', 0)
    """, (tid, run_id, task_id, deps))
    return tid


def _open_escalation(conn, run_id, task_run_id):
    repo = EscalationRepo(conn)
    return repo.upsert_open(run_id, task_run_id, "TASK_FAILURE", ["retry", "skip", "abort"])


def test_resolve_skip_marks_task_skipped(conn, seed_run):
    conn.execute("UPDATE runs SET status = 'PAUSED_FOR_DECISION' WHERE run_id = %s", (seed_run,))
    failed_id = _insert_failed_final(conn, seed_run)
    esc_id = _open_escalation(conn, seed_run, failed_id)
    resolve_escalation(conn, esc_id, "skip", seed_run)
    row = conn.execute(
        "SELECT status FROM task_runs WHERE task_run_id = %s", (failed_id,)
    ).fetchone()
    assert row["status"] == "SKIPPED"


def test_resolve_skip_unblocks_downstream(conn, seed_run):
    conn.execute("UPDATE runs SET status = 'PAUSED_FOR_DECISION' WHERE run_id = %s", (seed_run,))
    failed_id = _insert_failed_final(conn, seed_run, "TASK-A")
    blocked_id = _insert_blocked(conn, seed_run, "TASK-B", ["TASK-A"])
    esc_id = _open_escalation(conn, seed_run, failed_id)
    resolve_escalation(conn, esc_id, "skip", seed_run)
    row = conn.execute(
        "SELECT status FROM task_runs WHERE task_run_id = %s", (blocked_id,)
    ).fetchone()
    assert row["status"] == "PENDING"


def test_resolve_skip_resumes_run(conn, seed_run):
    conn.execute("UPDATE runs SET status = 'PAUSED_FOR_DECISION' WHERE run_id = %s", (seed_run,))
    failed_id = _insert_failed_final(conn, seed_run)
    esc_id = _open_escalation(conn, seed_run, failed_id)
    resolve_escalation(conn, esc_id, "skip", seed_run)
    status = conn.execute(
        "SELECT status FROM runs WHERE run_id = %s", (seed_run,)
    ).scalar()
    assert status == "RUNNING_EXECUTION"


def test_resolve_retry_creates_new_attempt(conn, seed_run):
    conn.execute("UPDATE runs SET status = 'PAUSED_FOR_DECISION' WHERE run_id = %s", (seed_run,))
    failed_id = _insert_failed_final(conn, seed_run, "TASK-A")
    esc_id = _open_escalation(conn, seed_run, failed_id)
    resolve_escalation(conn, esc_id, "retry", seed_run)
    count = conn.execute(
        "SELECT COUNT(*) FROM task_runs WHERE run_id = %s AND task_id = 'TASK-A'", (seed_run,)
    ).scalar()
    assert count == 2  # original + retry
    new_row = conn.execute(
        "SELECT retry_count FROM task_runs WHERE run_id = %s AND task_id = 'TASK-A' "
        "AND status = 'PENDING'", (seed_run,)
    ).fetchone()
    assert new_row["retry_count"] == 0  # reset for human override


def test_resolve_abort_marks_run_failed(conn, seed_run):
    conn.execute("UPDATE runs SET status = 'PAUSED_FOR_DECISION' WHERE run_id = %s", (seed_run,))
    failed_id = _insert_failed_final(conn, seed_run)
    esc_id = _open_escalation(conn, seed_run, failed_id)
    resolve_escalation(conn, esc_id, "abort", seed_run)
    status = conn.execute(
        "SELECT status FROM runs WHERE run_id = %s", (seed_run,)
    ).scalar()
    assert status == "FAILED"


def test_resolve_is_idempotent(conn, seed_run):
    """Calling resolve_escalation twice on same escalation is safe."""
    conn.execute("UPDATE runs SET status = 'PAUSED_FOR_DECISION' WHERE run_id = %s", (seed_run,))
    failed_id = _insert_failed_final(conn, seed_run)
    esc_id = _open_escalation(conn, seed_run, failed_id)
    resolve_escalation(conn, esc_id, "skip", seed_run)
    resolve_escalation(conn, esc_id, "skip", seed_run)  # second call — no-op
