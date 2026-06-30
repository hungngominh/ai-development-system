# src/ai_dev_system/task_graph/repo_docs.py
"""Render a single-task spec/plan as markdown and publish it to the bound repo's
feature branch (commit + push), returning a GitHub blob URL the bot can send.

All git IO is best-effort: publish_doc never raises — a push/auth failure (or no
repo) returns None and the calling worker simply records no link.
"""
from __future__ import annotations

import logging
import re
import time
import unicodedata
from pathlib import Path

from ai_dev_system.task_graph import git_ops

logger = logging.getLogger(__name__)


def slugify(title: str, maxlen: int = 40) -> str:
    if not title:
        return "task"
    t = str(title).replace("đ", "d").replace("Đ", "D")
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode("ascii")
    t = re.sub(r"[^a-zA-Z0-9]+", "-", t).strip("-").lower()
    t = t[:maxlen].strip("-")
    return t or "task"


def spec_doc_relpath(spec_id: str, title: str) -> str:
    return f".ai-dev/tasks/task-{spec_id[:8]}-{slugify(title)}-spec.md"


def plan_doc_relpath(spec_id: str, title: str) -> str:
    return f".ai-dev/tasks/task-{spec_id[:8]}-{slugify(title)}-plan.md"


def _title_of(spec: dict) -> str:
    task = spec.get("task") or {}
    return str(task.get("title") or spec.get("idea") or "Task").strip() or "Task"


def render_spec_md(spec: dict, spec_id: str) -> str:
    from ai_dev_system.task_graph.single_task_plan import branch_name_for
    task = spec.get("task") or {}
    title = _title_of(spec)
    branch = branch_name_for(spec_id)
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    lines = [
        f"# {title}",
        "",
        f"> Task `task-{spec_id[:8]}` · branch `{branch}` · cập nhật {ts}",
        "",
        f"**Mục tiêu:** {task.get('objective') or spec.get('idea') or ''}",
        "",
    ]
    facets = spec.get("facets") or {}
    if facets:
        lines.append("**Facets:**")
        for key, f in facets.items():
            status = (f or {}).get("status", "")
            val = (f or {}).get("value")
            val = f" — {val}" if isinstance(val, str) and val else ""
            lines.append(f"- **{key}** ({status}){val}")
        lines.append("")
    findings = spec.get("findings") or []
    if findings:
        lines.append("**Findings:**")
        lines += [f"- {x}" for x in findings]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_plan_md(spec: dict, plan: dict) -> str:
    title = _title_of(spec)
    branch = plan.get("branch") or ""
    gate = "on" if plan.get("tdd_gate") else "off"
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    tasks = ((plan.get("graph") or {}).get("tasks")) or []
    n = len(tasks) if isinstance(tasks, list) else 0
    lines = [
        f"# Plan — {title}",
        "",
        f"> branch `{branch}` · TDD gate: {gate} · cập nhật {ts}",
        "",
        f"## {n} bước",
        "",
    ]
    for i, t in enumerate(tasks, 1):
        deps = ", ".join(t.get("deps") or []) or "—"
        lines.append(
            f"{i}. **{t.get('objective') or t.get('id')}** — "
            f"agent `{t.get('agent_type')}`, phase `{t.get('phase')}`"
        )
        lines.append(f"   - Done: {t.get('done_definition') or ''}")
        lines.append(f"   - Deps: {deps}")
    return "\n".join(lines).rstrip() + "\n"


def publish_doc(repo_path: str, branch: str, relpath: str, content: str,
                commit_msg: str) -> str | None:
    """Ensure branch → write file → commit → push. Returns GitHub blob URL or None."""
    if not repo_path:
        return None
    try:
        git_ops.ensure_branch_from_base(repo_path, branch)
        abs_path = Path(repo_path) / relpath
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(content, encoding="utf-8")
        git_ops.commit_paths(repo_path, [relpath], commit_msg)
        push = git_ops.run_git(["push", "-u", "origin", branch], repo_path)
        if push.returncode != 0:
            logger.warning("publish_doc push failed: %s", (push.stderr or "").strip()[:200])
            return None
        remote = git_ops.run_git(["remote", "get-url", "origin"], repo_path)
        if remote.returncode != 0 or not remote.stdout.strip():
            return None
        return git_ops.blob_url(remote.stdout.strip(), branch, relpath)
    except Exception:  # noqa: BLE001 — publishing must never sink the flow
        logger.exception("publish_doc failed for %s", relpath)
        return None
