# src/ai_dev_system/engine/materializer.py
"""Task graph materialization (SQLite).

PG → SQLite changes:
- `SELECT ... FOR UPDATE` removed (SQLite single-writer)
- `.scalar()` → `.fetchone()[col]` (sqlite3 has no scalar())
- `gen_random_uuid()` → app-side `new_uuid()`
- `resolved_dependencies` stored as JSON TEXT (json.dumps)
"""
import copy
import json
import logging
import os
import sqlite3
from typing import Optional

from ai_dev_system.config import Config
from ai_dev_system.db.repos.artifacts import ArtifactRepo
from ai_dev_system.db.repos.events import EventRepo
from ai_dev_system.db.helpers import dump_json, load_array, new_uuid

logger = logging.getLogger(__name__)


class ArtifactResolutionError(Exception):
    """Required input artifact could not be resolved to a filesystem path."""


def materialize_task_runs(
    conn: sqlite3.Connection,
    run_id: str,
    graph_artifact_id: str,
    config: Config,
) -> None:
    """Load approved task graph → create PENDING task_runs. Safe to call multiple times.

    Idempotency: existence check + INSERT ON CONFLICT DO NOTHING (UNIQUE on
    (run_id, task_id, attempt_number)).
    """
    artifact_repo = ArtifactRepo(conn)
    event_repo = EventRepo(conn)

    artifact = artifact_repo.get(graph_artifact_id)
    if artifact is None:
        raise ValueError(f"Artifact {graph_artifact_id} not found")
    graph_path = os.path.join(artifact["content_ref"], "task_graph.json")
    with open(graph_path) as f:
        graph = json.load(f)

    atomic_tasks = [t for t in graph["tasks"] if t.get("execution_type") == "atomic"]

    existing_row = conn.execute(
        "SELECT COUNT(*) AS c FROM task_runs WHERE run_id = ? AND task_graph_artifact_id = ?",
        (run_id, graph_artifact_id),
    ).fetchone()
    if existing_row and existing_row["c"] > 0:
        logger.info("materialize_task_runs: already materialized for run %s, skipping", run_id)
        return

    for task in atomic_tasks:
        conn.execute(
            """
            INSERT INTO task_runs (
                task_run_id, run_id, task_id,
                task_graph_artifact_id,
                attempt_number, status,
                resolved_dependencies,
                retry_count,
                agent_routing_key,
                context_snapshot,
                materialized_at,
                input_artifact_ids,
                promoted_outputs
            ) VALUES (
                ?, ?, ?,
                ?,
                1, 'PENDING',
                ?,
                0,
                ?,
                ?,
                CURRENT_TIMESTAMP,
                '[]',
                '[]'
            )
            ON CONFLICT (run_id, task_id, attempt_number) DO NOTHING
            """,
            (
                new_uuid(),
                run_id,
                task["id"],
                graph_artifact_id,
                dump_json(task.get("deps", [])),
                task.get("agent_type"),
                dump_json(_build_context(task)),
            ),
        )

    conn.execute(
        """
        UPDATE runs
        SET status = 'RUNNING_EXECUTION', last_activity_at = CURRENT_TIMESTAMP
        WHERE run_id = ? AND status IN ('CREATED', 'RUNNING_PHASE_3', 'RUNNING_PHASE_2A')
        """,
        (run_id,),
    )

    event_repo.insert(
        run_id, "PHASE_STARTED", "system",
        payload={"phase": "execution", "task_count": len(atomic_tasks)},
    )

    logger.info("Materialized %d tasks for run %s", len(atomic_tasks), run_id)


def _build_context(task: dict) -> dict:
    """Immutable snapshot stored at materialization time."""
    return {
        "task_id": task["id"],
        "phase": task.get("phase", ""),
        "type": task.get("type", ""),
        "agent_type": task.get("agent_type", ""),
        "objective": task.get("objective", ""),
        "description": task.get("description", ""),
        "done_definition": task.get("done_definition", ""),
        "verification_steps": list(task.get("verification_steps", [])),
        "required_inputs": list(task.get("required_inputs", [])),
        "expected_outputs": list(task.get("expected_outputs", [])),
    }


def _resolve_artifact_paths(
    conn: sqlite3.Connection,
    run_id: str,
    context_snapshot: dict,
) -> dict:
    """Enrich context_snapshot.required_inputs with real artifact paths."""
    required = context_snapshot.get("required_inputs", [])
    if not required:
        ctx = copy.deepcopy(context_snapshot)
        ctx["required_inputs"] = []
        return ctx

    row = conn.execute(
        "SELECT current_artifacts FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    current_raw = row["current_artifacts"] if row else None
    if isinstance(current_raw, str):
        current = json.loads(current_raw) if current_raw else {}
    else:
        current = current_raw or {}

    resolved = []
    for logical_name in required:
        artifact_id = _match_artifact(logical_name, current)
        if artifact_id is None:
            raise ArtifactResolutionError(
                f"Required input '{logical_name}' not in current_artifacts for run {run_id}. "
                f"Upstream task may not have completed yet."
            )
        artifact = conn.execute(
            "SELECT artifact_id, content_ref FROM artifacts WHERE artifact_id = ?",
            (artifact_id,),
        ).fetchone()
        if artifact is None:
            raise ArtifactResolutionError(
                f"Artifact {artifact_id} referenced by '{logical_name}' not found."
            )
        resolved.append({
            "name": logical_name,
            "artifact_id": str(artifact["artifact_id"]),
            "path": artifact["content_ref"],
        })

    ctx = copy.deepcopy(context_snapshot)
    ctx["required_inputs"] = resolved
    return ctx


def _match_artifact(logical_name: str, current_artifacts: dict) -> Optional[str]:
    """Map a logical input name to an artifact_id from current_artifacts."""
    name_lower = logical_name.lower().replace("_", "").replace(".", "").replace("-", "")
    for key, artifact_id in current_artifacts.items():
        if artifact_id:
            key_clean = key.replace("_id", "").replace("_", "")
            if key_clean in name_lower or name_lower in key_clean:
                return str(artifact_id)
    return None
