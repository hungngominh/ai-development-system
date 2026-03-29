# src/ai_dev_system/engine/worker.py
import json
import os
from typing import Optional

import psycopg

from ai_dev_system.agents.base import AgentResult, PromotedOutput
from ai_dev_system.config import Config
from ai_dev_system.db.repos.events import EventRepo
from ai_dev_system.db.repos.task_runs import TaskRunRepo
from ai_dev_system.engine.resolver import resolve_dependencies
from ai_dev_system.storage.paths import build_temp_path
from ai_dev_system.storage.promote import promote_output


def pickup_task(
    conn: psycopg.Connection,
    config: Config,
    run_id: str,
    worker_id: str,
) -> Optional[dict]:
    """
    Tx 1 (Job B): Lock a READY task, mark RUNNING, emit TASK_STARTED.
    Returns enriched task dict (includes temp_path, promoted_outputs_parsed) or None.
    Short transaction — releases task_run lock before agent execution.
    """
    repo = TaskRunRepo(conn)
    event_repo = EventRepo(conn)

    task = repo.pickup(run_id=run_id, worker_id=worker_id)
    if task is None:
        return None

    event_repo.insert(run_id, "TASK_STARTED", f"worker:{worker_id}", task["task_run_id"])

    promoted_raw = task.get("promoted_outputs") or []
    if isinstance(promoted_raw, str):
        promoted_raw = json.loads(promoted_raw)
    promoted_outputs = [PromotedOutput(**po) for po in promoted_raw]

    temp_path = build_temp_path(config.storage_root, run_id, task["task_id"], task["attempt_number"])
    os.makedirs(temp_path, exist_ok=True)

    return task | {"temp_path": temp_path, "promoted_outputs_parsed": promoted_outputs}


def execute_and_promote(
    conn: psycopg.Connection,
    config: Config,
    task: dict,
    result: AgentResult,
    worker_id: str,
) -> str:
    """
    Tx 2 (Job C): Given agent result, promote outputs and mark task SUCCESS/FAILED.
    Returns final task status string.
    """
    # Phase 1 limitation: promote_output() calls mark_success() internally,
    # which sets output_artifact_id on the task_run. Calling it twice would
    # fail the promotion guard on the second call. Multi-output support
    # requires refactoring promote_output to not call mark_success.
    if len(task["promoted_outputs_parsed"]) > 1:
        raise NotImplementedError(
            "Phase 1 only supports tasks with 0 or 1 promoted_output. "
            f"Task {task['task_run_id']} has {len(task['promoted_outputs_parsed'])}."
        )

    repo = TaskRunRepo(conn)
    event_repo = EventRepo(conn)
    run_id = task["run_id"]

    if not result.success:
        repo.mark_failed(task["task_run_id"], "EXECUTION_ERROR", result.error or "unknown")
        event_repo.insert(run_id, "TASK_FAILED", f"worker:{worker_id}", task["task_run_id"],
                          {"error": result.error})
        return "FAILED"

    for po in task["promoted_outputs_parsed"]:
        promote_output(conn, config, task, po, task["temp_path"])

    if not task["promoted_outputs_parsed"]:
        repo.mark_success(task["task_run_id"], task["temp_path"], None)
        event_repo.insert(run_id, "TASK_COMPLETED", f"worker:{worker_id}", task["task_run_id"], {})

    resolve_dependencies(conn, run_id)
    return "SUCCESS"
