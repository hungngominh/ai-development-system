# src/ai_dev_system/engine/worker.py
import copy
import json
import logging
import os
import socket
import threading as _threading
from pathlib import Path
from typing import Optional

import sqlite3

from ai_dev_system.agents.base import AgentResult, PromotedOutput
from ai_dev_system.config import Config
from ai_dev_system.db.repos.events import EventRepo
from ai_dev_system.db.repos.runs import RunRepo
from ai_dev_system.db.repos.task_runs import TaskRunRepo
from ai_dev_system.engine.resolver import resolve_dependencies
from ai_dev_system.rules.registry import RuleRegistry
from ai_dev_system.storage.paths import build_temp_path
from ai_dev_system.storage.promote import promote_output

_RULES_DIR = Path(__file__).parent.parent / "rules" / "definitions"
_rule_registry = RuleRegistry(rules_dir=_RULES_DIR)

logger = logging.getLogger(__name__)


def pickup_task(
    conn: sqlite3.Connection,
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
    conn: sqlite3.Connection,
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


# ── New worker loop for runner.py ─────────────────────────────────────────────

from ai_dev_system.engine.heartbeat import HeartbeatThread
from ai_dev_system.engine.failure import _handle_failure
from ai_dev_system.engine.materializer import _resolve_artifact_paths, ArtifactResolutionError


def worker_loop(
    run_id: str,
    config,
    agent,
    stop_event: _threading.Event,
    conn_factory,
) -> None:
    """Worker loop for runner.py. Differences from run_worker_loop():
    - Uses stop_event instead of max_iterations
    - Runs HeartbeatThread per task
    - Checks run status before promoting (abort guard)
    - Catches ArtifactResolutionError
    - Does NOT call resolve_dependencies() (background thread handles that)

    Transaction management: conn_factory() must return autocommit=True connections.
    """
    conn = conn_factory()
    worker_id = f"{socket.gethostname()}-{_threading.get_ident()}"
    try:
        while not stop_event.is_set():
            # Abort guard at loop head (autocommit=True — no tx)
            run_status_row = conn.execute(
                "SELECT status FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if run_status_row and run_status_row["status"] in ("ABORTED", "FAILED", "COMPLETED"):
                break

            # Pickup task
            task = None
            try:
                conn.execute("BEGIN")
                task = _pickup_for_runner(conn, config, run_id, worker_id)
                conn.execute("COMMIT")
            except Exception:
                logger.exception("Pickup error in worker_loop")
                conn.execute("ROLLBACK")
                stop_event.wait(timeout=min(getattr(config, "poll_interval_s", 5.0), 1.0))
                continue

            if task is None:
                stop_event.wait(timeout=min(getattr(config, "poll_interval_s", 5.0), 1.0))
                continue

            # Start heartbeat
            heartbeat = HeartbeatThread(
                conn_factory=conn_factory,
                task_run_id=task["task_run_id"],
                interval_s=getattr(config, "heartbeat_interval_s", 30.0),
            )
            heartbeat.start()
            result = None
            try:
                # Resolve artifact paths
                try:
                    ctx_raw = task.get("context_snapshot") or {}
                    if isinstance(ctx_raw, str):
                        ctx_raw = json.loads(ctx_raw) if ctx_raw.strip() else {}
                    context = _resolve_artifact_paths(conn, run_id, ctx_raw)
                except ArtifactResolutionError as e:
                    result = _make_error_result(task, str(e))
                else:
                    rule_match = _rule_registry.match_rules(task)
                    if rule_match.skill_rules or rule_match.file_rules:
                        event_repo = EventRepo(conn)
                        event_repo.insert(run_id, "RULES_APPLIED", "worker",
                                          task_run_id=task["task_run_id"],
                                          payload={"skill_rules": rule_match.skill_rules,
                                                   "file_rules": rule_match.file_rules})
                        for skill in rule_match.skill_rules:
                            print(f"[RULE] Apply skill: {skill}")
                    result = agent.run(
                        task_id=task["task_id"],
                        output_path=task["temp_path"],
                        promoted_outputs=task["promoted_outputs_parsed"],
                        context=copy.deepcopy(context),
                        timeout_s=getattr(config, "task_timeout_s", 3600.0),
                        file_rules=rule_match.file_rules,
                    )
            except Exception as e:
                result = _make_error_result(task, str(e))
            finally:
                heartbeat.stop()

            # Abort guard before promoting
            run_status_row = conn.execute(
                "SELECT status FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if run_status_row and run_status_row["status"] in ("ABORTED", "FAILED"):
                conn.execute("""
                    UPDATE task_runs SET status = 'ABORTED'
                    WHERE task_run_id = ? AND status = 'RUNNING'
                """, (task["task_run_id"],))
                break

            # Promote or handle failure
            try:
                conn.execute("BEGIN")
                if not result.success:
                    _handle_failure(conn, config, task, result.error or "unknown",
                                    worker_id, run_id, error_type="EXECUTION_ERROR")
                else:
                    _promote_for_runner(conn, config, task, result, worker_id, run_id)
                conn.execute("COMMIT")
            except Exception:
                logger.exception("Promote/failure error for task %s", task["task_id"])
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
    finally:
        conn.close()


def _make_error_result(task: dict, error: str):
    return AgentResult(output_path=task.get("temp_path", "/tmp"), error=error)


def _pickup_for_runner(conn, config, run_id: str, worker_id: str):
    """Pickup with dep double-check + run status guard. Called inside BEGIN.

    SQLite has no FOR UPDATE SKIP LOCKED or ANY(array); we do dep check in Python.
    SQLite is single-writer so the "skip locked" need is moot.
    """
    candidates = conn.execute("""
        SELECT tr.*
        FROM task_runs tr
        WHERE tr.run_id = ?
          AND tr.status = 'READY'
        ORDER BY tr.retry_count ASC, tr.materialized_at ASC
    """, (run_id,)).fetchall()

    task = None
    for cand in candidates:
        deps_raw = cand["resolved_dependencies"]
        if isinstance(deps_raw, str):
            deps = json.loads(deps_raw) if deps_raw.strip() else []
        else:
            deps = list(deps_raw or [])
        if not deps:
            task = cand
            break
        placeholders = ",".join("?" for _ in deps)
        bad = conn.execute(
            f"""
            SELECT 1 FROM task_runs
            WHERE run_id = ? AND task_id IN ({placeholders})
              AND status NOT IN ('SUCCESS', 'SKIPPED')
            LIMIT 1
            """,
            (run_id, *deps),
        ).fetchone()
        if bad is None:
            task = cand
            break

    if task is None:
        return None

    # Run guard inside lock
    run_status = conn.execute(
        "SELECT status FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if run_status and run_status["status"] != "RUNNING_EXECUTION":
        return None

    temp_path = build_temp_path(
        config.storage_root, run_id, task["task_id"], task["attempt_number"]
    )
    os.makedirs(temp_path, exist_ok=True)

    conn.execute("""
        UPDATE task_runs
        SET status = 'RUNNING', worker_id = ?,
            locked_at = CURRENT_TIMESTAMP, heartbeat_at = CURRENT_TIMESTAMP, started_at = CURRENT_TIMESTAMP
        WHERE task_run_id = ?
    """, (worker_id, task["task_run_id"]))

    EventRepo(conn).insert(run_id, "TASK_STARTED", f"worker:{worker_id}",
                           task_run_id=task["task_run_id"])

    task_dict = dict(task)
    promoted_raw = task_dict.get("promoted_outputs") or []
    if isinstance(promoted_raw, str):
        promoted_raw = json.loads(promoted_raw)
    elif not isinstance(promoted_raw, list):
        promoted_raw = list(promoted_raw)
    promoted_outputs = [PromotedOutput(**po) if isinstance(po, dict) else po
                        for po in promoted_raw]
    # Ensure UUID fields are plain strings (psycopg returns uuid columns as uuid.UUID objects
    # when the connection doesn't have a custom UUID loader registered).
    for key in ("task_run_id", "run_id", "task_graph_artifact_id",
                "previous_attempt_id", "output_artifact_id"):
        if key in task_dict and task_dict[key] is not None:
            task_dict[key] = str(task_dict[key])
    return task_dict | {"temp_path": temp_path, "promoted_outputs_parsed": promoted_outputs}


def _promote_for_runner(conn, config, task: dict, result, worker_id: str, run_id: str):
    """Promote output and mark SUCCESS. Called inside transaction.

    Two paths:
    - Tasks WITH promoted_outputs: promote_output() marks success internally.
    - Tasks WITHOUT promoted_outputs: call mark_success() explicitly.
    """
    pos = task["promoted_outputs_parsed"]
    if pos:
        # promote_output moves the WHOLE temp dir as ONE artifact and marks the
        # task SUCCESS, so promote exactly once per task (looping would fail the
        # 2nd call's RUNNING guard). Then map every declared output NAME to that
        # artifact so downstream tasks resolve their required_inputs by name.
        artifact_po = PromotedOutput(
            name=task["task_id"], artifact_type="EXECUTION_LOG",
            description=f"Outputs of {task['task_id']}",
        )
        artifact_id = promote_output(conn, config, task, artifact_po, task["temp_path"])
        run_repo = RunRepo(conn)
        for po in pos:
            run_repo.record_output(run_id, po.name, artifact_id)
    else:
        rows = TaskRunRepo(conn).mark_success(task["task_run_id"], task["temp_path"], None)
        if rows > 0:
            EventRepo(conn).insert(run_id, "TASK_COMPLETED", f"worker:{worker_id}",
                                   task_run_id=task["task_run_id"])
