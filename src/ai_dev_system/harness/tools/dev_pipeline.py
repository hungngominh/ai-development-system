"""Chat-bound pipeline tools: dev_newproject_start + dev_run_status + dev_answer_gate.

Factory: make_dev_pipeline_tools(*, surface, chat_id, conn_factory, config,
                                    link_store, spawn_start=None, spawn_phase_b=None) -> list

These tools are injected into an assistant session so a chat user can start a
new-project debate, query its status, and answer Gate 1 decisions without leaving
the conversation.

Spawn isolation: the actual subprocess.Popen is replaced by the `spawn_start` /
`spawn_phase_b` injectables so tests never fork real processes.

Run-id discovery approach chosen: single immediate query after spawn.
After calling spawn_start, the tool queries:
    SELECT run_id FROM runs WHERE project_id=? ORDER BY created_at DESC LIMIT 1
If found (e.g. a pre-existing or very-fast run row), it links run_id→chat.
If not found (debate hasn't written its row yet), returns status:"starting"
and skips linking. The watcher/notifier will link on first poll.
This is simpler than polling and fully deterministic in tests.

dev_answer_gate decision assembly:
Mirrors webui._do_gate1_approve: iterates ctx.questions, looks up result_by_id from
debate_report["results"], maps ResolvedItem.choice → (answer, resolution_type):
  agent_a → agent_a_position, CONSENSUS
  agent_b → agent_b_position, CONSENSUS
  moderator / None → moderator_summary, CONSENSUS
  override → override_text, FORCED_HUMAN
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
from ai_dev_system.gate.gate1_bridge import Decision as GateDecision
from ai_dev_system.gate.gate1_bridge import finalize_gate1
from ai_dev_system.gate.gate1_review.loader import load_gate1_context
from ai_dev_system.gate.gate1_review.parser import parse_user_input
from ai_dev_system.gate.gate1_review.state import load_state, save_state

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
    spawn_phase_b=None,
) -> list:
    """Return [dev_newproject_start, dev_run_status, dev_answer_gate] bound to this chat."""

    _spawn = spawn_start if spawn_start is not None else _real_spawn
    _spawn_pb = spawn_phase_b if spawn_phase_b is not None else _real_spawn

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

    # ------------------------------------------------------------------ #
    # Tool 3: dev_answer_gate                                             #
    # ------------------------------------------------------------------ #

    @tool(
        "dev_answer_gate",
        "Route a free-text Gate-1 answer. Supported inputs: "
        "`Q1 chọn A/B`, `Q1 approve moderator`, `Q1: override text`, "
        "`confirm` / `finalize`, `approve all`, `show Q1`, `abort`. "
        "On confirm/approve_all: finalizes Gate 1 and spawns Phase B automatically.",
        {"run_id": str, "text": str},
    )
    async def dev_answer_gate(args: dict[str, Any]) -> dict[str, Any]:
        run_id: str = args["run_id"]
        text: str = args["text"]
        conn = conn_factory()

        # Parse the input (regex-first; LLM off in v1 tool path)
        pr = parse_user_input(text, llm_client=None)

        if pr.action_type == "answer":
            # Record the choice in session state
            state = load_state(run_id, conn)
            state.record_choice(pr.target, pr.choice, override_text=pr.payload)
            save_state(run_id, state, conn)
            conn.commit()

            # Compute remaining questions
            ctx = load_gate1_context(run_id, conn)
            remaining = [q for q in ctx.questions if q.id not in state.resolved]
            msg = f"{pr.message} | {len(remaining)} question(s) remaining."
            return {"content": [{"type": "text", "text": msg}]}

        elif pr.action_type in ("approve_all", "confirm"):
            # Build decisions list (mirrors webui._do_gate1_approve logic)
            state = load_state(run_id, conn)
            ctx = load_gate1_context(run_id, conn)

            result_by_id = {
                qdr["question"]["id"]: qdr
                for qdr in ctx.debate_report.get("results", [])
            }
            decisions: list[GateDecision] = []
            for q in ctx.questions:
                qdr = result_by_id.get(q.id, {})
                final = qdr.get("final", {})
                ri = state.resolved.get(q.id)

                if ri is None:
                    # Not explicitly resolved → use moderator_summary as consensus
                    answer = final.get("moderator_summary") or ""
                    resolution_type = "CONSENSUS"
                    rationale = ""
                elif ri.choice == "agent_a":
                    answer = final.get("agent_a_position") or ""
                    resolution_type = "CONSENSUS"
                    rationale = ""
                elif ri.choice == "agent_b":
                    answer = final.get("agent_b_position") or ""
                    resolution_type = "CONSENSUS"
                    rationale = ""
                elif ri.choice == "moderator":
                    answer = final.get("moderator_summary") or ""
                    resolution_type = "CONSENSUS"
                    rationale = ""
                else:
                    # override
                    answer = ri.override_text or ""
                    resolution_type = "FORCED_HUMAN"
                    rationale = ri.override_text or ""

                decisions.append(GateDecision(
                    question_id=q.id,
                    question_text=q.text,
                    classification=q.classification,
                    resolution_type=resolution_type,
                    answer=answer,
                    options_considered=[
                        final.get("agent_a_position") or "",
                        final.get("agent_b_position") or "",
                    ],
                    rationale=rationale,
                ))

            # Finalize Gate 1 (sets run status to RUNNING_PHASE_1D, writes artifacts)
            finalize_gate1(run_id, decisions, config.storage_root, conn)
            conn.commit()

            # Spawn Phase B detached (auto-approves Gate 2 via non-TTY stdin)
            log_dir = Path(config.storage_root) / "ui_logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"phase_b_{run_id[:8]}.log"

            pb_argv = [
                sys.executable, "-m", "ai_dev_system.cli.main",
                "phase-b", "run", "--run-id", run_id,
            ]
            try:
                with open(log_path, "a", encoding="utf-8", errors="replace") as logf:
                    _spawn_pb(
                        pb_argv,
                        stdout=logf,
                        stderr=subprocess.STDOUT,
                        cwd=str(_REPO_ROOT),
                    )
            except Exception as exc:  # pragma: no cover
                return {"content": [{"type": "text", "text": f"finalized but phase-b spawn error: {exc}"}]}

            payload = json.dumps({"started_phase_b": True, "run_id": run_id})
            return {"content": [{"type": "text", "text": payload}]}

        else:
            # expand / edit_brief / abort / unknown → return guidance, no state change
            guidance = pr.message or (
                "Không hiểu lệnh. Thử: `Q1 chọn A`, `Q1 approve moderator`, "
                "`Q1: text riêng`, `approve all`, `confirm`, `abort`."
            )
            return {"content": [{"type": "text", "text": guidance}]}

    return [dev_newproject_start, dev_run_status, dev_answer_gate]
