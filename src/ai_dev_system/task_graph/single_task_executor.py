"""Bridge worker: approved task spec → git branch → execution engine.

Spawned detached by webui after spec approval. Writes:
  task_specs/<spec_id>-exec.log  — timestamped progress lines
  task_specs/<spec_id>-exec.json — status/result summary

JSON schema:
  {"status": "running|done|error", "run_id": "...", "branch": "ai-dev/task-<8>",
   "base_branch": "...", "exec_status": "COMPLETED|FAILED|ABORTED", "error": "..."}
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import subprocess
import time
import uuid
from pathlib import Path

from ai_dev_system.config import Config
from ai_dev_system.db.connection import get_connection
from ai_dev_system.engine.runner import run_execution
from ai_dev_system.task_graph.single_task_plan import load_plan, branch_name_for

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Git helpers (shared — see ai_dev_system.task_graph.git_ops)
# ---------------------------------------------------------------------------
from ai_dev_system.task_graph.git_ops import (  # noqa: E402
    run_git as _git,
    current_branch as _git_current_branch,
    base_branch as _git_base_branch,
    checkout_branch as _git_checkout_branch,
    normalize_github_url as _normalize_github_url,
    push_branch_compare as _push_branch_compare,
)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _create_run_row(conn, run_id: str, title: str, spec_id: str, branch: str) -> None:
    metadata = json.dumps({"kind": "task_exec", "spec_id": spec_id, "branch": branch})
    conn.execute(
        """
        INSERT INTO runs
            (run_id, project_id, status, title, metadata, current_artifacts)
        VALUES (?, 'adhoc-task-exec', 'RUNNING_EXECUTION', ?, ?, '{}')
        """,
        (run_id, title[:60], metadata),
    )
    conn.commit()


def _create_task_graph_artifact(
    conn, run_id: str, task_graph: dict, storage_root: str
) -> str:
    artifact_id = uuid.uuid4().hex
    artifact_dir = (
        Path(storage_root) / "task_execs" / run_id / "task_graph"
    )
    artifact_dir.mkdir(parents=True, exist_ok=True)
    content = json.dumps(task_graph, indent=2, ensure_ascii=False)
    (artifact_dir / "task_graph.json").write_text(content, encoding="utf-8")
    checksum = "sha256:" + hashlib.sha256(content.encode()).hexdigest()
    conn.execute(
        """
        INSERT INTO artifacts
            (artifact_id, run_id, artifact_type, version, status, created_by,
             input_artifact_ids, content_ref, content_checksum, content_size, annotations)
        VALUES (?, ?, 'TASK_GRAPH_APPROVED', 1, 'ACTIVE', 'system',
                '[]', ?, ?, ?, '{}')
        """,
        (artifact_id, run_id, str(artifact_dir), checksum, len(content)),
    )
    conn.commit()
    return artifact_id


# ---------------------------------------------------------------------------
# Log helpers
# ---------------------------------------------------------------------------

def _exec_log(log_path: Path, msg: str) -> None:
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        f.flush()


def _write_exec_status(status_path: Path, data: dict) -> None:
    status_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Main worker
# ---------------------------------------------------------------------------

def run_executor(
    spec_id: str,
    storage_root: str,
    database_url: str | None = None,
) -> None:
    """Blocking. Runs until execution engine finishes or an error occurs."""
    out_dir = Path(storage_root) / "task_specs"
    out_dir.mkdir(parents=True, exist_ok=True)
    spec_path = out_dir / f"{spec_id}.json"
    log_path = out_dir / f"{spec_id}-exec.log"
    status_path = out_dir / f"{spec_id}-exec.json"

    _exec_log(log_path, "Executor khởi động")

    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except Exception as exc:
        _exec_log(log_path, f"LỖI đọc spec: {exc}")
        _write_exec_status(status_path, {"status": "error", "error": str(exc)})
        return

    repo_path = spec.get("repo") or ""
    task = spec.get("task") or {}
    facets = spec.get("facets") or {}

    if not repo_path:
        _exec_log(log_path, "LỖI: spec không có repo path — không thể execute")
        _write_exec_status(
            status_path,
            {"status": "error", "error": "no repo path in spec"},
        )
        return

    # Gate: execute only an APPROVED, persisted plan (built at spec-approval time).
    plan = load_plan(storage_root, spec_id)
    if plan is None or not plan.get("approved"):
        msg = "plan chưa được duyệt" if plan is not None else "chưa có plan đã duyệt"
        _exec_log(log_path, f"LỖI: {msg} — không thể execute")
        _write_exec_status(status_path, {"status": "error", "error": msg})
        return

    branch_name = plan.get("branch") or branch_name_for(spec_id)
    _exec_log(log_path, f"Repo: {repo_path}")

    # 1. Get current branch and create execution branch
    try:
        base_branch = _git_base_branch(repo_path)
        _exec_log(log_path, f"Base branch: {base_branch}")
        _git_checkout_branch(repo_path, branch_name)
        _exec_log(log_path, f"Branch: {branch_name}")
    except Exception as exc:
        _exec_log(log_path, f"LỖI git branch: {exc}")
        _write_exec_status(status_path, {"status": "error", "error": str(exc)})
        return

    _write_exec_status(
        status_path,
        {"status": "running", "branch": branch_name, "base_branch": base_branch},
    )

    # 2. Resolve config and DB
    if database_url is None:
        cfg = Config.from_env()
        database_url = cfg.database_url
    else:
        cfg = Config.from_env()

    conn = get_connection(database_url)

    run_id = uuid.uuid4().hex
    title = str(task.get("title") or spec.get("idea") or "Task exec")
    try:
        _create_run_row(conn, run_id, title, spec_id, branch_name)
        _exec_log(log_path, f"Run row: {run_id[:8]}")
    except Exception as exc:
        _exec_log(log_path, f"LỖI tạo run row: {exc}")
        _write_exec_status(
            status_path,
            {
                "status": "error", "error": str(exc),
                "branch": branch_name, "base_branch": base_branch,
            },
        )
        conn.close()
        return

    # 3. Load task_graph from approved plan and create TASK_GRAPH_APPROVED artifact
    task_graph = plan["graph"]
    try:
        graph_artifact_id = _create_task_graph_artifact(
            conn, run_id, task_graph, storage_root
        )
        _exec_log(log_path, f"Task graph artifact: {graph_artifact_id[:8]}")
    except Exception as exc:
        _exec_log(log_path, f"LỖI tạo task graph artifact: {exc}")
        _write_exec_status(
            status_path,
            {
                "status": "error", "error": str(exc),
                "run_id": run_id, "branch": branch_name, "base_branch": base_branch,
            },
        )
        conn.close()
        return

    conn.close()

    # 4. Run execution engine (blocking, up to 30 minutes)
    _exec_log(log_path, "Đang chạy execution engine (tối đa 30 phút)…")
    _write_exec_status(
        status_path,
        {
            "status": "running", "run_id": run_id,
            "branch": branch_name, "base_branch": base_branch,
        },
    )

    from ai_dev_system.agents.phase_routing_agent import PhaseRoutingAgent
    agent = PhaseRoutingAgent(
        repo_path=repo_path,
        branch_name=branch_name,
        base_branch=base_branch,
        live_log_path=log_path,
    )

    try:
        result = run_execution(
            run_id, graph_artifact_id, cfg, agent, poll_interval_s=5.0
        )
        _exec_log(log_path, f"Execution xong: {result.status}")
        exec_data = {
            "status": "done", "exec_status": result.status,
            "run_id": run_id, "branch": branch_name, "base_branch": base_branch,
        }
        # On success, push the branch so it can be reviewed on GitHub before
        # the human Accepts (which then opens the PR).
        if result.status == "COMPLETED":
            push_info = _push_branch_compare(repo_path, branch_name, base_branch)
            exec_data.update(push_info)
            if push_info["pushed"]:
                _exec_log(
                    log_path,
                    "Đã push branch lên origin. Compare: "
                    f"{push_info.get('compare_url') or '(không có URL GitHub)'}",
                )
            else:
                _exec_log(log_path, f"Push branch thất bại: {push_info.get('push_error')}")
        _write_exec_status(status_path, exec_data)
    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        _exec_log(log_path, f"LỖI execution: {type(exc).__name__}: {exc}")
        _exec_log(log_path, f"Traceback:\n{tb}")
        _write_exec_status(
            status_path,
            {
                "status": "error", "error": str(exc),
                "run_id": run_id, "branch": branch_name, "base_branch": base_branch,
            },
        )


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Execute an approved single-task spec against the target repo."
    )
    p.add_argument("--id", required=True, help="spec_id (task_specs/<id>.json)")
    p.add_argument("--storage-root", required=True)
    p.add_argument("--database-url", default=None)
    args = p.parse_args(argv)
    run_executor(args.id, args.storage_root, args.database_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
