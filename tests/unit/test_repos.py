import uuid
import pytest
from ai_dev_system.db.repos.task_runs import TaskRunRepo
from ai_dev_system.db.repos.escalations import EscalationRepo


def _insert_running_task(conn, run_id, task_id="TASK-1"):
    task_run_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO task_runs (
            task_run_id, run_id, task_id, attempt_number, status,
            agent_type, input_artifact_ids, resolved_dependencies,
            promoted_outputs, retry_count, worker_id,
            locked_at, heartbeat_at, started_at
        ) VALUES (%s, %s, %s, 1, 'RUNNING',
                  'agent', '{}', '{}', '[]', 0, 'worker-1',
                  now(), now(), now())
    """, (task_run_id, run_id, task_id))
    return task_run_id


def test_mark_failed_final_changes_status(conn, seed_run):
    task_run_id = _insert_running_task(conn, seed_run)
    repo = TaskRunRepo(conn)
    rows = repo.mark_failed_final(task_run_id, "EXECUTION_ERROR", "exploded")
    assert rows == 1
    row = conn.execute(
        "SELECT status FROM task_runs WHERE task_run_id = %s", (task_run_id,)
    ).fetchone()
    assert row["status"] == "FAILED_FINAL"


def test_mark_failed_retryable_changes_status(conn, seed_run):
    task_run_id = _insert_running_task(conn, seed_run)
    repo = TaskRunRepo(conn)
    rows = repo.mark_failed_retryable(task_run_id, "EXECUTION_ERROR", "transient")
    assert rows == 1
    row = conn.execute(
        "SELECT status FROM task_runs WHERE task_run_id = %s", (task_run_id,)
    ).fetchone()
    assert row["status"] == "FAILED_RETRYABLE"


def test_create_retry_increments_attempt_number(conn, seed_run):
    task_run_id = _insert_running_task(conn, seed_run)
    conn.execute(
        "UPDATE task_runs SET status = 'FAILED_RETRYABLE', retry_count = 0 WHERE task_run_id = %s",
        (task_run_id,)
    )
    repo = TaskRunRepo(conn)
    source = conn.execute(
        "SELECT * FROM task_runs WHERE task_run_id = %s", (task_run_id,)
    ).fetchone()
    new_id = repo.create_retry(seed_run, dict(source), retry_delay_s=0, reset_retry_count=False)
    new_row = conn.execute(
        "SELECT attempt_number, retry_count, previous_attempt_id FROM task_runs WHERE task_run_id = %s",
        (new_id,)
    ).fetchone()
    assert new_row["attempt_number"] == 2
    assert new_row["retry_count"] == 1
    assert new_row["previous_attempt_id"] == task_run_id


def test_create_retry_resets_count_for_human_override(conn, seed_run):
    task_run_id = _insert_running_task(conn, seed_run)
    conn.execute(
        "UPDATE task_runs SET status = 'FAILED_FINAL', retry_count = 3 WHERE task_run_id = %s",
        (task_run_id,)
    )
    repo = TaskRunRepo(conn)
    source = conn.execute(
        "SELECT * FROM task_runs WHERE task_run_id = %s", (task_run_id,)
    ).fetchone()
    new_id = repo.create_retry(seed_run, dict(source), retry_delay_s=0, reset_retry_count=True)
    new_row = conn.execute(
        "SELECT retry_count FROM task_runs WHERE task_run_id = %s", (new_id,)
    ).fetchone()
    assert new_row["retry_count"] == 0


def test_escalation_upsert_open_creates_record(conn, seed_run, seed_task_run):
    repo = EscalationRepo(conn)
    esc_id = repo.upsert_open(
        run_id=seed_run,
        task_run_id=seed_task_run,
        reason="TASK_FAILURE",
        options=["retry", "skip", "abort"],
    )
    assert esc_id is not None
    row = conn.execute(
        "SELECT * FROM escalations WHERE escalation_id = %s", (esc_id,)
    ).fetchone()
    assert row["status"] == "OPEN"
    assert row["reason"] == "TASK_FAILURE"


def test_escalation_upsert_open_idempotent(conn, seed_run, seed_task_run):
    repo = EscalationRepo(conn)
    id1 = repo.upsert_open(seed_run, seed_task_run, "TASK_FAILURE", ["retry", "skip", "abort"])
    id2 = repo.upsert_open(seed_run, seed_task_run, "TASK_FAILURE", ["retry", "skip", "abort"])
    assert id1 == id2


def test_escalation_get_open(conn, seed_run, seed_task_run):
    repo = EscalationRepo(conn)
    repo.upsert_open(seed_run, seed_task_run, "TASK_FAILURE", ["retry"])
    open_escs = repo.get_open(seed_run)
    assert len(open_escs) == 1
    assert open_escs[0]["reason"] == "TASK_FAILURE"
