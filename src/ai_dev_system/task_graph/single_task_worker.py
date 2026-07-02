"""Background worker: produce a single-task TaskSpec and write a status file.

Spawned detached by the webui for repo-grounded (agentic) specs so the HTTP
request doesn't block. Writes <storage_root>/task_specs/<id>.json with a status,
and records a terminal row in the `runs` table so the spec shows up in the home
"Runs" list (marked via metadata.kind == "task_spec").
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

from ai_dev_system.task_graph.clarify_questions import find_blocking, synthesize_questions
from ai_dev_system.task_graph.facets import SPEC_FACET_KEYS as _SPEC_KEYS
from ai_dev_system.task_graph.single_task import spec_single_task, _TITLE_MAX
from ai_dev_system.llm_factory import make_llm_client
from ai_dev_system.task_graph.repo_docs import (
    spec_doc_relpath, plan_doc_relpath, render_spec_md, render_plan_md, publish_doc,
)
from ai_dev_system.task_graph.single_task_plan import (
    plan_single_task, plan_path, branch_name_for,
)

logger = logging.getLogger(__name__)


def _spec_log(log_path: Path, msg: str) -> None:
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        f.flush()


def _record_run_row(spec_id: str, payload: dict, idea: str, repo: str | None,
                    database_url: str) -> None:
    """Best-effort: upsert a terminal `runs` row for this task-spec.

    Marked with metadata.kind == "task_spec" so the webui links it to
    /task-spec?id=... rather than the debate /run page. A terminal status
    (COMPLETED/FAILED) keeps the row inert for the per-run execution loop.

    Never raises: the JSON status file is the primary artifact, so a DB problem
    must not lose it.
    """
    from ai_dev_system.db.connection import get_connection
    from ai_dev_system.db.helpers import dump_json

    if payload.get("status") == "done":
        status = "COMPLETED"
        title = str((payload.get("task") or {}).get("title") or "").strip()
    else:
        status = "FAILED"
        title = ""
    if not title:
        idea = (idea or "").strip()
        title = (idea[:_TITLE_MAX].rstrip() + ("…" if len(idea) > _TITLE_MAX else "")) or "Task spec"
    metadata = dump_json({"kind": "task_spec", "spec_id": spec_id, "repo": repo})

    conn = None
    try:
        conn = get_connection(database_url)
        conn.execute(
            """
            INSERT INTO runs (run_id, project_id, status, title, completed_at, metadata)
            VALUES (?, 'adhoc-task-spec', ?, ?, CURRENT_TIMESTAMP, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                status = excluded.status,
                title = excluded.title,
                completed_at = CURRENT_TIMESTAMP,
                last_activity_at = CURRENT_TIMESTAMP,
                metadata = excluded.metadata
            """,
            (spec_id, status, title, metadata),
        )
        conn.commit()
    except Exception:  # noqa: BLE001 — best-effort; the file artifact is what matters
        logger.exception("Failed to record runs row for task-spec %s", spec_id)
    finally:
        if conn is not None:
            conn.close()


def run_worker(spec_id: str, idea: str, repo: str | None, *, storage_root: str,
               database_url: str | None = None) -> Path:
    out_dir = Path(storage_root) / "task_specs"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{spec_id}.json"
    log_path = out_dir / f"{spec_id}.log"

    _spec_log(log_path, "Worker khởi động")
    idea_preview = (idea or "").strip()[:100]
    _spec_log(log_path, f"Task: {idea_preview}{'…' if len((idea or '').strip()) > 100 else ''}")

    try:
        if repo:
            _spec_log(log_path, f"Chế độ: agentic — đọc repo tại {repo}")
            _spec_log(log_path, f"Đang chạy claude CLI (đọc code + sinh {len(_SPEC_KEYS)} spec facets, tối đa 300s)…")
            llm = None
        else:
            _spec_log(log_path, "Chế độ: text spec via LLM")
            _spec_log(log_path, "Đang khởi tạo LLM client…")
            llm = make_llm_client("spec")
            _spec_log(log_path, f"Đang gọi LLM sinh {len(_SPEC_KEYS)} spec facets…")

        result = spec_single_task(idea, llm, repo_path=repo,
                                  log=lambda msg: _spec_log(log_path, msg))
        facets = result["facets"]
        spec_facets = {k: v for k, v in facets.items() if k in _SPEC_KEYS}
        filled = sum(1 for f in spec_facets.values() if f.get("status") == "filled")
        na = sum(1 for f in spec_facets.values() if f.get("status") == "na")
        needs_human = sum(1 for f in spec_facets.values() if f.get("status") == "needs_human")
        _spec_log(log_path, f"Sinh facets xong — filled={filled} na={na} needs_human={needs_human}")
        if needs_human == len(_SPEC_KEYS):
            _spec_log(log_path, f"CẢNH BÁO: tất cả {len(_SPEC_KEYS)} spec facets đều needs_human — có thể claude CLI đã timeout hoặc lỗi nội bộ")

        payload = {"status": "done", "idea": idea, "repo": repo,
                   "task": result["task"], "facets": facets}
        # M1: only include 'findings' key when non-empty — keeps disabled-path JSON
        # byte-identical to legacy output (webui reader already uses .get("findings", []))
        _findings = result.get("findings", [])
        if _findings:
            payload["findings"] = _findings
        # Pre-generate clarifying questions for blocking findings so the gateway
        # ClarifyWatcher can push them WITHOUT any LLM call on the daemon thread.
        blocking = find_blocking(payload)
        if blocking:
            try:
                synth_llm = llm if llm is not None else make_llm_client("spec")
            except Exception:  # noqa: BLE001
                synth_llm = None
            questions = synthesize_questions(blocking, idea=idea, llm=synth_llm)
            _spec_log(log_path, f"Cần làm rõ: {len(questions)} câu hỏi (blocking={len(blocking)})")
        else:
            questions = []
        payload["clarify"] = {"needed": bool(blocking), "questions": questions}
        _spec_log(log_path, "Hoàn thành ✓")
    except Exception as exc:  # noqa: BLE001
        _spec_log(log_path, f"LỖI: {type(exc).__name__}: {exc}")
        payload = {"status": "error", "idea": idea, "repo": repo, "error": str(exc)}

    # Publish the spec doc to the repo branch (off the gateway thread) when the
    # spec is final and not blocked on clarify. Best-effort: no link on failure.
    if (payload.get("status") == "done" and repo
            and not (payload.get("clarify") or {}).get("needed")):
        title = (payload.get("task") or {}).get("title") or idea
        url = publish_doc(
            repo, branch_name_for(spec_id),
            spec_doc_relpath(spec_id, title),
            render_spec_md(payload, spec_id),
            f"docs(ai-dev): spec for {spec_id[:8]}",
        )
        if url:
            payload["spec_doc_url"] = url
        else:
            # Surfaced by the gateway as a ⚠️ warning: the doc exists only in
            # the container's clone (push/auth failed), not on GitHub.
            payload["doc_publish_failed"] = True
        _spec_log(log_path, f"Spec doc: {url or '(local only / no push)'}")

    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    if database_url is None:
        from ai_dev_system.config import Config
        database_url = Config.from_env().database_url
    _record_run_row(spec_id, payload, idea, repo, database_url)
    return path


def run_plan_worker(spec_id: str, *, storage_root: str,
                    database_url: str | None = None) -> dict:
    """Plan gate: build the reviewable plan for an already-approved spec and
    publish <id>-plan.md to the repo branch. Records doc_url in the plan file."""
    out_dir = Path(storage_root) / "task_specs"
    log_path = out_dir / f"{spec_id}.log"
    spec = json.loads((out_dir / f"{spec_id}.json").read_text(encoding="utf-8"))
    plan = plan_single_task(spec, spec_id, storage_root=storage_root)
    repo = spec.get("repo")
    if repo:
        title = (spec.get("task") or {}).get("title") or spec.get("idea")
        url = publish_doc(
            repo, plan["branch"], plan_doc_relpath(spec_id, title),
            render_plan_md(spec, plan),
            f"docs(ai-dev): plan for {spec_id[:8]}",
        )
        if url:
            plan["doc_url"] = url
        else:
            plan["doc_publish_failed"] = True
        plan_path(storage_root, spec_id).write_text(
            json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
        _spec_log(log_path, f"Plan doc: {url or '(local only / no push)'}")
    return plan


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--id", required=True)
    p.add_argument("--idea", default="")
    p.add_argument("--repo", default=None)
    p.add_argument("--mode", choices=["spec", "plan"], default="spec")
    p.add_argument("--storage-root", required=True)
    p.add_argument("--database-url", default=None)
    args = p.parse_args(argv)
    if args.mode == "plan":
        run_plan_worker(args.id, storage_root=args.storage_root,
                        database_url=args.database_url)
    else:
        run_worker(args.id, args.idea, args.repo or None,
                   storage_root=args.storage_root, database_url=args.database_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
