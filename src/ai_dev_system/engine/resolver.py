# src/ai_dev_system/engine/resolver.py
"""Dependency resolver (SQLite).

PG → SQLite: `unnest(?::text[])` is gone; we iterate `resolved_dependencies` in
Python (small lists, no perf cost) and check each dep status with a single
parameterised IN clause.
"""
import json
import sqlite3

from ai_dev_system.db.repos.events import EventRepo


def _deps_as_list(raw) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        if not raw.strip():
            return []
        return json.loads(raw)
    return list(raw)


def resolve_dependencies(conn: sqlite3.Connection, run_id: str) -> int:
    """Move PENDING tasks to READY if all dependencies satisfied.

    Returns count of tasks promoted to READY.
    """
    event_repo = EventRepo(conn)
    pending = conn.execute(
        "SELECT task_run_id, task_id, resolved_dependencies FROM task_runs "
        "WHERE run_id = ? AND status = 'PENDING'",
        (run_id,),
    ).fetchall()

    promoted = 0
    for task in pending:
        deps = _deps_as_list(task["resolved_dependencies"])

        if not deps:
            all_satisfied = True
        else:
            placeholders = ",".join("?" for _ in deps)
            row = conn.execute(
                f"""
                SELECT COUNT(*) AS satisfied_count FROM task_runs
                WHERE run_id = ?
                  AND task_id IN ({placeholders})
                  AND status IN ('SUCCESS', 'SKIPPED')
                """,
                (run_id, *deps),
            ).fetchone()
            all_satisfied = row["satisfied_count"] == len(deps)

        if all_satisfied:
            result = conn.execute(
                """
                UPDATE task_runs SET status = 'READY'
                WHERE task_run_id = ? AND status = 'PENDING'
                """,
                (task["task_run_id"],),
            )
            if result.rowcount == 1:
                event_repo.insert(run_id, "TASK_READY", "system", task["task_run_id"])
                promoted += 1

    return promoted
