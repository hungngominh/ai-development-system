# src/ai_dev_system/engine/materializer.py
import copy
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import psycopg
import psycopg.types.json

from ai_dev_system.config import Config
from ai_dev_system.db.repos.artifacts import ArtifactRepo
from ai_dev_system.db.repos.events import EventRepo

logger = logging.getLogger(__name__)


class ArtifactResolutionError(Exception):
    """Required input artifact could not be resolved to a filesystem path."""


def materialize_task_runs(
    conn: psycopg.Connection,
    run_id: str,
    graph_artifact_id: str,
    config: Config,
) -> None:
    """Load approved task graph → create PENDING task_runs. Safe to call multiple times.

    Idempotency: SELECT FOR UPDATE on run row, then INSERT ON CONFLICT DO NOTHING.
    Both guards together prevent duplicates even under concurrent callers.
    """
    artifact_repo = ArtifactRepo(conn)
    event_repo = EventRepo(conn)

    # Read graph from promoted artifact path (I/O before locking — safe, file is immutable)
    artifact = artifact_repo.get(graph_artifact_id)
    if artifact is None:
        raise ValueError(f"Artifact {graph_artifact_id} not found")
    graph_path = os.path.join(artifact["content_ref"], "task_graph.json")
    with open(graph_path) as f:
        graph = json.load(f)

    atomic_tasks = [t for t in graph["tasks"] if t.get("execution_type") == "atomic"]

    # Lock run row to serialize concurrent materializer calls
    conn.execute("SELECT run_id FROM runs WHERE run_id = %s FOR UPDATE", (run_id,))
    existing = conn.execute("""
        SELECT COUNT(*) FROM task_runs
        WHERE run_id = %s AND task_graph_artifact_id = %s
    """, (run_id, graph_artifact_id)).scalar()

    if existing and existing > 0:
        logger.info("materialize_task_runs: already materialized for run %s, skipping", run_id)
        return

    for task in atomic_tasks:
        conn.execute("""
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
                gen_random_uuid(), %s, %s,
                %s,
                1, 'PENDING',
                %s,
                0,
                %s,
                %s,
                now(),
                '{}',
                '[]'
            )
            ON CONFLICT (run_id, task_id, attempt_number) DO NOTHING
        """, (
            run_id,
            task["id"],
            graph_artifact_id,
            task.get("deps", []),
            task.get("agent_type"),
            psycopg.types.json.Jsonb(_build_context(task)),
        ))

    conn.execute("""
        UPDATE runs
        SET status = 'RUNNING_EXECUTION', last_activity_at = now()
        WHERE run_id = %s AND status IN ('CREATED', 'RUNNING_PHASE_3', 'RUNNING_PHASE_2A')
    """, (run_id,))

    event_repo.insert(run_id, "PHASE_STARTED", "system",
                      payload={"phase": "execution", "task_count": len(atomic_tasks)})

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
    conn: psycopg.Connection,
    run_id: str,
    context_snapshot: dict,
) -> dict:
    """Enrich context_snapshot.required_inputs with real artifact paths.

    Raises ArtifactResolutionError if a required input cannot be resolved.
    """
    required = context_snapshot.get("required_inputs", [])
    if not required:
        ctx = copy.deepcopy(context_snapshot)
        ctx["required_inputs"] = []
        return ctx

    current = conn.execute(
        "SELECT current_artifacts FROM runs WHERE run_id = %s", (run_id,)
    ).scalar() or {}

    resolved = []
    for logical_name in required:
        artifact_id = _match_artifact(logical_name, current)
        if artifact_id is None:
            raise ArtifactResolutionError(
                f"Required input '{logical_name}' not in current_artifacts for run {run_id}. "
                f"Upstream task may not have completed yet."
            )
        artifact = conn.execute(
            "SELECT artifact_id, content_ref FROM artifacts WHERE artifact_id = %s",
            (artifact_id,)
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
