# src/ai_dev_system/engine/resolver.py
import psycopg
from ai_dev_system.db.repos.events import EventRepo

def resolve_dependencies(conn: psycopg.Connection, run_id: str) -> int:
    """
    Move PENDING tasks to READY if all dependencies satisfied.
    Returns count of tasks promoted to READY.
    """
    event_repo = EventRepo(conn)
    pending = conn.execute(
        "SELECT task_run_id, task_id, resolved_dependencies FROM task_runs WHERE run_id = %s AND status = 'PENDING'",
        (run_id,)
    ).fetchall()

    promoted = 0
    for task in pending:
        deps = task["resolved_dependencies"] or []
        if not deps:
            all_satisfied = True
        else:
            row = conn.execute("""
                SELECT NOT EXISTS (
                    SELECT 1 FROM unnest(%s::text[]) AS dep(task_id)
                    WHERE NOT EXISTS (
                        SELECT 1 FROM task_runs
                        WHERE run_id = %s AND task_id = dep.task_id
                          AND status IN ('SUCCESS', 'SKIPPED')
                    )
                ) AS satisfied
            """, (deps, run_id)).fetchone()
            all_satisfied = row["satisfied"]

        if all_satisfied:
            result = conn.execute("""
                UPDATE task_runs SET status = 'READY'
                WHERE task_run_id = %s AND status = 'PENDING'
            """, (task["task_run_id"],))
            if result.rowcount == 1:
                event_repo.insert(run_id, "TASK_READY", "system", task["task_run_id"])
                promoted += 1

    return promoted
