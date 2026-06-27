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
import warnings
from typing import Optional

from ai_dev_system.config import Config
from ai_dev_system.db.repos.artifacts import ArtifactRepo
from ai_dev_system.db.repos.events import EventRepo
from ai_dev_system.db.repos.runs import RunRepo
from ai_dev_system.db.helpers import dump_json, load_array, load_json, new_uuid

logger = logging.getLogger(__name__)


class ArtifactResolutionError(Exception):
    """Required input artifact could not be resolved to a filesystem path."""


def _build_promoted_outputs(task: dict) -> list[dict]:
    """Derive promoted outputs from a task's declared expected_outputs.

    One PromotedOutput-shaped dict per expected output so the agent knows which
    files to write; the worker maps each name to the task's single artifact.
    Empty expected_outputs -> [] (task still runs, just promotes nothing).
    """
    return [
        {"name": out, "artifact_type": "EXECUTION_LOG", "description": ""}
        for out in task.get("expected_outputs", [])
    ]


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
    with open(graph_path, encoding="utf-8") as f:
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
                ?
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
                dump_json(_build_promoted_outputs(task)),
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

    # Seed the name-addressed output map with pipeline inputs the first task
    # needs: TASK-PARSE's required_input "raw_spec" IS the SPEC_BUNDLE produced
    # earlier in Phase B. Both aliases point at spec_bundle_id so resolution
    # finds it before any task has run.
    run_row = conn.execute(
        "SELECT current_artifacts FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if run_row:
        current = load_json(run_row["current_artifacts"], default={}) or {}
        spec_id = current.get("spec_bundle_id")
        if spec_id:
            run_repo = RunRepo(conn)
            run_repo.record_output(run_id, "spec_bundle", spec_id)
            run_repo.record_output(run_id, "raw_spec", spec_id)

    logger.info("Materialized %d tasks for run %s", len(atomic_tasks), run_id)


def _build_context(task: dict) -> dict:
    """Immutable snapshot stored at materialization time."""
    return {
        "task_id": task["id"],
        "phase": task.get("phase", ""),
        "type": task.get("type", ""),
        "tags": list(task.get("tags", [])),
        "agent_type": task.get("agent_type", ""),
        "objective": task.get("objective", ""),
        "description": task.get("description", ""),
        "done_definition": task.get("done_definition", ""),
        "verification_steps": list(task.get("verification_steps", [])),
        "required_inputs": list(task.get("required_inputs", [])),
        "expected_outputs": list(task.get("expected_outputs", [])),
        "facets": dict(task.get("facets") or {}),
        "tdd_tests_authored": task.get("tdd_tests_authored", False),
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

    # Task outputs are name-addressed in current_artifacts.outputs; consult that
    # first (case-insensitive), then fall back to the fuzzy pipeline-key match.
    outputs = current.get("outputs") or {}
    outputs_ci = {str(k).lower(): v for k, v in outputs.items()}

    resolved = []
    for logical_name in required:
        artifact_id = outputs_ci.get(str(logical_name).lower()) or _match_artifact(logical_name, current)
        artifact = None
        if artifact_id is not None:
            artifact = conn.execute(
                "SELECT artifact_id, content_ref FROM artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
        if artifact is None:
            # Lenient: don't fail the task on an unresolvable input (e.g. ad-hoc
            # rule-task names). Pass the name through without a path; the agent
            # still has the task objective + whatever else resolved.
            warnings.warn(
                f"Required input '{logical_name}' not resolvable for run {run_id}; "
                f"task will run without it.",
                stacklevel=2,
            )
            resolved.append({"name": logical_name, "artifact_id": None, "path": None})
            continue
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
        if key == "outputs":
            continue  # nested name->id map, handled separately (value is a dict)
        if artifact_id:
            key_clean = key.replace("_id", "").replace("_", "")
            if key_clean in name_lower or name_lower in key_clean:
                return str(artifact_id)
    return None
