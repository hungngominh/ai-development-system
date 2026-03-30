# tests/integration/test_create_from_graph.py
import uuid
from ai_dev_system.db.repos.runs import RunRepo
from ai_dev_system.db.repos.task_runs import TaskRunRepo
from ai_dev_system.task_graph.generator import generate_task_graph


def _make_artifact(conn, run_id: str) -> str:
    """Insert a minimal TASK_GRAPH_APPROVED artifact and return its artifact_id."""
    artifact_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO artifacts (
            artifact_id, run_id, artifact_type, version, status, created_by,
            input_artifact_ids, content_ref, content_checksum, content_size
        ) VALUES (%s, %s, 'TASK_GRAPH_APPROVED', 1, 'ACTIVE', 'system',
                  '{}', '/tmp/stub', 'abc123', 0)
    """, (artifact_id, run_id))
    return artifact_id


def test_create_from_graph(conn, project_id):
    run_repo = RunRepo(conn)
    run_id = run_repo.create(project_id=project_id, pipeline_type="test")
    task_run_repo = TaskRunRepo(conn)

    brief = {"constraints": {"hard": [], "soft": []},
             "scope": {"type": "unknown"}, "success_signals": []}
    envelope = generate_task_graph({}, brief, "art-123")

    artifact_id = _make_artifact(conn, run_id)

    created = []
    for task in envelope["tasks"]:
        if task["execution_type"] == "atomic":
            tr_id = task_run_repo.create_from_graph(
                run_id=run_id, task=task, task_graph_artifact_id=artifact_id)
            created.append(tr_id)

    assert len(created) == 4
    rows = conn.execute(
        "SELECT * FROM task_runs WHERE run_id = %s AND task_graph_artifact_id = %s",
        (run_id, artifact_id)
    ).fetchall()
    assert len(rows) == 4
    assert all(r["status"] == "PENDING" for r in rows)
