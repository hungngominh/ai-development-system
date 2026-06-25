"""Resume a stale RUNNING_EXECUTION run.

Spawned detached by the webui when the user clicks "Làm tiếp" on a stale run.
Resets any stuck RUNNING task_runs to PENDING, then re-calls run_execution()
with the appropriate agent (RepoBranchAgent for task_exec runs, ClaudeMaxAgent
for regular debate runs).

Writes progress to task_specs/<spec_id>-exec.log when it's a task_exec run,
or to ui_logs/resume-<run_id[:8]>.log for regular runs.
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

from ai_dev_system.config import Config
from ai_dev_system.db.connection import get_connection
from ai_dev_system.engine.runner import run_execution

logger = logging.getLogger(__name__)


def _resume_log(log_path: Path, msg: str) -> None:
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        f.flush()


def _reset_stuck_task_runs(conn, run_id: str, log: callable) -> int:
    """Reset RUNNING task_runs to PENDING so the worker can pick them up."""
    cursor = conn.execute(
        "UPDATE task_runs SET status = 'PENDING' WHERE run_id = ? AND status = 'RUNNING'",
        (run_id,),
    )
    conn.commit()
    count = cursor.rowcount
    if count:
        log(f"Đặt lại {count} task_run RUNNING → PENDING")
    return count


def _find_task_graph_artifact(conn, run_id: str) -> str | None:
    row = conn.execute(
        """SELECT artifact_id FROM artifacts
           WHERE run_id = ? AND artifact_type = 'TASK_GRAPH_APPROVED'
           ORDER BY created_at DESC LIMIT 1""",
        (run_id,),
    ).fetchone()
    return row["artifact_id"] if row else None


def run_resume(run_id: str, storage_root: str, database_url: str | None = None) -> None:
    """Re-run the execution engine for a stale RUNNING_EXECUTION run."""
    cfg = Config.from_env()
    if database_url:
        import dataclasses
        cfg = dataclasses.replace(cfg, database_url=database_url)

    # Determine log path (task_exec runs have a spec log; regular runs get their own)
    conn = get_connection(cfg.database_url)
    try:
        run_row = conn.execute(
            "SELECT status, metadata FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
    finally:
        conn.close()

    if not run_row:
        logger.error("run_id %s not found in DB", run_id)
        return

    metadata: dict = {}
    try:
        metadata = json.loads(run_row["metadata"] or "{}")
    except Exception:
        pass

    is_task_exec = metadata.get("kind") == "task_exec"
    spec_id = metadata.get("spec_id") or ""

    if is_task_exec and spec_id:
        log_path = Path(storage_root) / "task_specs" / f"{spec_id}-exec.log"
    else:
        log_dir = Path(storage_root) / "ui_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"resume-{run_id[:8]}.log"

    def log(msg: str) -> None:
        _resume_log(log_path, msg)

    log(f"Resume executor khởi động — run_id={run_id[:8]}")
    log(f"Loại run: {'task_exec' if is_task_exec else 'regular'}")

    conn = get_connection(cfg.database_url)
    try:
        _reset_stuck_task_runs(conn, run_id, log)
        graph_artifact_id = _find_task_graph_artifact(conn, run_id)
    finally:
        conn.close()

    if not graph_artifact_id:
        log("LỖI: Không tìm thấy TASK_GRAPH_APPROVED artifact — không thể resume")
        return

    log(f"TASK_GRAPH_APPROVED artifact: {graph_artifact_id[:8]}")

    # Build the right agent
    if is_task_exec:
        branch = metadata.get("branch") or f"ai-dev/task-{spec_id[:8]}"
        # Read repo_path and base_branch from exec-status JSON
        status_path = Path(storage_root) / "task_specs" / f"{spec_id}-exec.json"
        repo_path = ""
        base_branch = "main"
        if status_path.exists():
            try:
                exec_st = json.loads(status_path.read_text(encoding="utf-8"))
                base_branch = exec_st.get("base_branch") or "main"
            except Exception:
                pass
        if not repo_path:
            spec_path = Path(storage_root) / "task_specs" / f"{spec_id}.json"
            try:
                spec = json.loads(spec_path.read_text(encoding="utf-8"))
                repo_path = spec.get("repo") or ""
            except Exception:
                pass
        if not repo_path:
            log("LỖI: Không đọc được repo_path từ spec — không thể resume task_exec")
            return

        from ai_dev_system.agents.repo_branch_agent import RepoBranchAgent
        agent = RepoBranchAgent(repo_path=repo_path, branch_name=branch, base_branch=base_branch)
        log(f"Agent: RepoBranchAgent branch={branch} base={base_branch}")
    else:
        from ai_dev_system.agents.claude_max_agent import ClaudeMaxAgent
        agent = ClaudeMaxAgent()
        log("Agent: ClaudeMaxAgent")

    log("Đang chạy execution engine…")
    try:
        result = run_execution(run_id, graph_artifact_id, cfg, agent, poll_interval_s=5.0)
        log(f"Execution xong: {result.status}")
        if is_task_exec and spec_id:
            status_path = Path(storage_root) / "task_specs" / f"{spec_id}-exec.json"
            try:
                exec_st: dict = {}
                if status_path.exists():
                    exec_st = json.loads(status_path.read_text(encoding="utf-8"))
                exec_st.update({"status": "done", "exec_status": result.status, "run_id": run_id})
                status_path.write_text(
                    json.dumps(exec_st, indent=2, ensure_ascii=False), encoding="utf-8"
                )
            except Exception:
                pass
    except Exception as exc:
        log(f"LỖI: {type(exc).__name__}: {exc}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Resume a stale RUNNING_EXECUTION run.")
    p.add_argument("--id", required=True, help="run_id")
    p.add_argument("--storage-root", required=True)
    p.add_argument("--database-url", default=None)
    args = p.parse_args(argv)
    run_resume(args.id, args.storage_root, args.database_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
