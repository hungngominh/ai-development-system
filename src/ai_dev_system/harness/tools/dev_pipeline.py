"""Chat-bound pipeline tools: dev_newproject_start + dev_run_status.

Factory: make_dev_pipeline_tools(*, surface, chat_id, conn_factory, config,
                                    link_store, spawn_start=None) -> list

These tools are injected into an assistant session so a chat user can start a
new-project debate and query its status without leaving the conversation.

Spawn isolation: the actual subprocess.Popen is replaced by the `spawn_start`
injectable so tests never fork real processes.

Run-id discovery approach chosen: single immediate query after spawn.
After calling spawn_start, the tool queries:
    SELECT run_id FROM runs WHERE project_id=? ORDER BY created_at DESC LIMIT 1
If found (e.g. a pre-existing or very-fast run row), it links run_id→chat.
If not found (debate hasn't written its row yet), returns status:"starting"
and skips linking. The watcher/notifier will link on first poll.
This is simpler than polling and fully deterministic in tests.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from claude_agent_sdk import tool

from ai_dev_system.cli.start_project import make_project_id, name_to_slug
from ai_dev_system.gate.gate1_review.loader import load_gate1_context

# Repo root: this file lives at src/ai_dev_system/harness/tools/dev_pipeline.py
# parents: [0]=tools/, [1]=harness/, [2]=ai_dev_system/, [3]=src/, [4]=repo root
_REPO_ROOT = Path(__file__).resolve().parents[4]


def _real_spawn(argv: list[str], **kwargs) -> None:
    """Detached subprocess.Popen matching webui._start pattern."""
    popen_kwargs: dict = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        popen_kwargs["start_new_session"] = True
    popen_kwargs.update(kwargs)
    subprocess.Popen(argv, **popen_kwargs)


def make_dev_pipeline_tools(
    *,
    surface: str,
    chat_id: str,
    conn_factory,
    config,
    link_store,
    spawn_start=None,
    spawn_phase_b=None,  # reserved for Task 3
) -> list:
    """Return [dev_newproject_start, dev_run_status] bound to this chat."""

    _spawn = spawn_start if spawn_start is not None else _real_spawn

    # ------------------------------------------------------------------ #
    # Tool 1: dev_newproject_start                                        #
    # ------------------------------------------------------------------ #

    @tool(
        "dev_newproject_start",
        "Start a new-project debate pipeline from chat. Spawns the debate in the "
        "background and returns a run_id (or project_id while the run row is being "
        "created). Use dev_run_status to poll progress.",
        {"project_name": str, "idea": str},
    )
    async def dev_newproject_start(args: dict[str, Any]) -> dict[str, Any]:
        project_name: str = args["project_name"]
        idea: str = args["idea"]

        # Compute deterministic project_id
        slug = name_to_slug(project_name)
        project_id = make_project_id(slug)

        # Prepare log directory
        log_dir = Path(config.storage_root) / "ui_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "start.log"

        argv = [
            sys.executable, "-m", "ai_dev_system.cli.main",
            "start",
            "--project-name", project_name,
            "--idea", idea,
        ]

        try:
            # Open log file for spawn output (only used by real spawn; tests inject
            # a callable that ignores kwargs, so we pass stdout as a kwarg).
            with open(log_path, "a", encoding="utf-8", errors="replace") as logf:
                _spawn(
                    argv,
                    stdout=logf,
                    stderr=subprocess.STDOUT,
                    cwd=str(_REPO_ROOT),
                )
        except Exception as exc:  # pragma: no cover
            return {"content": [{"type": "text", "text": f"spawn error: {exc}"}]}

        # Attempt immediate run_id resolution
        conn = conn_factory()
        row = conn.execute(
            "SELECT run_id FROM runs WHERE project_id=? ORDER BY created_at DESC LIMIT 1",
            (project_id,),
        ).fetchone()

        if row is not None:
            run_id = row["run_id"]
            link_store.link(run_id, surface, chat_id)
            text = json.dumps({"run_id": run_id, "project_id": project_id, "status": "started"})
        else:
            text = json.dumps({"project_id": project_id, "status": "starting"})

        return {"content": [{"type": "text", "text": text}]}

    # ------------------------------------------------------------------ #
    # Tool 2: dev_run_status                                              #
    # ------------------------------------------------------------------ #

    @tool(
        "dev_run_status",
        "Get the current status of a pipeline run. If the run is PAUSED_AT_GATE_1, "
        "includes the Gate 1 questions the human needs to answer.",
        {"run_id": str},
    )
    async def dev_run_status(args: dict[str, Any]) -> dict[str, Any]:
        run_id: str = args["run_id"]
        conn = conn_factory()

        row = conn.execute(
            "SELECT status FROM runs WHERE run_id=?",
            (run_id,),
        ).fetchone()

        if row is None:
            return {"content": [{"type": "text", "text": f"run not found: {run_id!r}"}]}

        status: str = row["status"]
        payload: dict[str, Any] = {"run_id": run_id, "status": status}

        if status == "PAUSED_AT_GATE_1":
            try:
                ctx = load_gate1_context(run_id, conn)
                payload["questions"] = [
                    {"id": q.id, "text": q.text} for q in ctx.questions
                ]
            except Exception as exc:
                payload["gate1_load_error"] = str(exc)

        return {"content": [{"type": "text", "text": json.dumps(payload)}]}

    return [dev_newproject_start, dev_run_status]
