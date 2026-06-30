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

Gate routing:
  PAUSED_AT_GATE_1 → Gate-1 NLU handling (parse_user_input); on approve/confirm
      spawns `phase-b to-gate2 --run-id R` (pauses pipeline at Gate 2 for review).
  PAUSED_AT_GATE_2 → simple approve/reject regex; on approve spawns
      `phase-b resume-gate2 --run-id R --decision approve`; on reject `--decision reject`.
  other status → guidance only.
"""
from __future__ import annotations

import glob as _glob
import json
import os
import re
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
from ai_dev_system.gate.gate1_review.state import clear_state, load_state, save_state

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
    spawn_task_worker=None,
    spawn_executor=None,
    create_pr=None,
    make_spec_id=None,
    chat_task_store=None,
) -> list:
    """Return [dev_newproject_start, dev_run_status, dev_answer_gate, dev_task_start] bound to this chat."""

    _spawn = spawn_start if spawn_start is not None else _real_spawn
    _spawn_pb = spawn_phase_b if spawn_phase_b is not None else _real_spawn
    _spawn_worker = spawn_task_worker if spawn_task_worker is not None else _real_spawn
    _spawn_exec = spawn_executor if spawn_executor is not None else _real_spawn

    if create_pr is None:
        from ai_dev_system.vcs.github_pr import create_pr as create_pr  # noqa: PLW0127
    if make_spec_id is None:
        import uuid
        def make_spec_id():  # noqa: E306
            return uuid.uuid4().hex
    if chat_task_store is None:
        from ai_dev_system.harness.tools.chat_task_store import ChatTaskStore
        chat_task_store = ChatTaskStore(config.storage_root)

    # Resolve this chat's bound repo (match surface == bot.label)
    _repo_path = ""
    _base_branch = ""
    for _b in getattr(config, "telegram_bots", ()):
        if getattr(_b, "label", None) == surface:
            _repo_path = getattr(_b, "repo_path", "") or ""
            _base_branch = getattr(_b, "base_branch", "") or ""
            break

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
            # Debate row not yet written — record as pending so the watcher can link later
            link_store.add_pending(project_id, surface, chat_id)
            text = json.dumps({
                "project_id": project_id,
                "status": "starting",
                "note": "Debate đang khởi động; sẽ thông báo khi tới Gate 1.",
            })

        return {"content": [{"type": "text", "text": text}]}

    # ------------------------------------------------------------------ #
    # Tool 2: dev_run_status                                              #
    # ------------------------------------------------------------------ #

    @tool(
        "dev_run_status",
        "Get the current status of a pipeline run. If the run is PAUSED_AT_GATE_1, "
        "includes the Gate 1 questions the human needs to answer. "
        "run_id is optional — if omitted, resolves this chat's most recent run.",
        {"run_id": str},
    )
    async def dev_run_status(args: dict[str, Any]) -> dict[str, Any]:
        run_id: str = (args.get("run_id") or "").strip()
        if not run_id:
            run_id = link_store.latest_for_chat(surface, chat_id) or ""
        if not run_id:
            return {"content": [{"type": "text", "text": "Chưa có run nào cho chat này."}]}

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

        elif status == "PAUSED_AT_GATE_2":
            try:
                # Load the generated task graph from the TASK_GRAPH_GENERATED artifact.
                # current_artifacts["task_graph_gen_id"] → artifacts.content_ref →
                # <dir>/generate_task_graph.json (fallback: any non-task_graph.json *.json)
                arts_row = conn.execute(
                    "SELECT current_artifacts FROM runs WHERE run_id=?", (run_id,)
                ).fetchone()
                current_artifacts: dict = {}
                if arts_row and arts_row["current_artifacts"]:
                    try:
                        current_artifacts = json.loads(arts_row["current_artifacts"])
                    except (json.JSONDecodeError, TypeError):
                        pass

                gen_id = current_artifacts.get("task_graph_gen_id")
                if gen_id:
                    art_row = conn.execute(
                        "SELECT content_ref FROM artifacts WHERE artifact_id=?", (gen_id,)
                    ).fetchone()
                    if art_row:
                        content_dir = art_row["content_ref"]
                        graph_path = Path(content_dir) / "generate_task_graph.json"
                        if not graph_path.exists():
                            all_json = _glob.glob(str(Path(content_dir) / "*.json"))
                            candidates = [
                                f for f in all_json
                                if Path(f).name != "task_graph.json"
                                and not Path(f).name.startswith("_")
                            ] or all_json
                            graph_path = Path(candidates[0]) if candidates else graph_path
                        if graph_path.exists():
                            with open(graph_path, encoding="utf-8") as f:
                                envelope = json.load(f)
                            payload["task_graph"] = [
                                {
                                    "id": t.get("id", ""),
                                    "title": t.get("title") or t.get("objective") or "",
                                    "agent_type": t.get("agent_type", ""),
                                }
                                for t in envelope.get("tasks", [])
                            ]
            except Exception as exc:
                payload["task_graph_load_error"] = str(exc)

        return {"content": [{"type": "text", "text": json.dumps(payload)}]}

    # ------------------------------------------------------------------ #
    # Tool 3: dev_answer_gate                                             #
    # ------------------------------------------------------------------ #

    # Compiled Gate-2 decision regex (case-insensitive)
    _G2_APPROVE_RE = re.compile(
        r"\b(approve|duy[eệ]t|đồng\s*ý|ok|yes)\b", re.IGNORECASE
    )
    # Reject includes English/Vietnamese NEGATORS so a negated approval
    # ("do not approve", "never approve", "not ok") matches BOTH approve and
    # reject → lands in the ambiguous→guidance branch instead of silently
    # approving (the approve keyword alone would otherwise win). The Vietnamese
    # negator "không" is already covered by the kh[oô]ng alternative.
    _G2_REJECT_RE = re.compile(
        r"\b(reject|t[uừ]\s*ch[oố]i|no|kh[oô]ng|not|never|cannot|don'?t|won'?t|can'?t)\b",
        re.IGNORECASE,
    )

    @tool(
        "dev_answer_gate",
        "Route a free-text gate answer. At Gate 1: `Q1 chọn A/B`, `approve all`, `confirm`. "
        "At Gate 2: `duyệt` / `approve` to approve the task graph, "
        "`từ chối` / `reject` to reject it. "
        "run_id is optional — if omitted, resolves this chat's most recent run.",
        {"run_id": str, "text": str},
    )
    async def dev_answer_gate(args: dict[str, Any]) -> dict[str, Any]:
        run_id: str = (args.get("run_id") or "").strip()
        if not run_id:
            run_id = link_store.latest_for_chat(surface, chat_id) or ""
        if not run_id:
            return {"content": [{"type": "text", "text": "Chưa có run nào cho chat này."}]}

        text: str = args["text"]
        conn = conn_factory()

        # Read status first to route between Gate 1 and Gate 2
        status_row = conn.execute(
            "SELECT status FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if status_row is None:
            return {"content": [{"type": "text", "text": f"run not found: {run_id!r}"}]}

        run_status: str = status_row["status"]

        # ------------------------------------------------------------------ #
        # Gate-2 routing                                                      #
        # ------------------------------------------------------------------ #
        if run_status == "PAUSED_AT_GATE_2":
            # Decide once. A message matching BOTH (or neither) is ambiguous — never
            # silently approve a task graph on a mixed signal; ask for a clear answer.
            _g2_approve = bool(_G2_APPROVE_RE.search(text))
            _g2_reject = bool(_G2_REJECT_RE.search(text))
            if _g2_approve and not _g2_reject:
                log_dir = Path(config.storage_root) / "ui_logs"
                log_dir.mkdir(parents=True, exist_ok=True)
                log_path = log_dir / f"phase_b_resume_{run_id[:8]}.log"
                pb_argv = [
                    sys.executable, "-m", "ai_dev_system.cli.main",
                    "phase-b", "resume-gate2", "--run-id", run_id, "--decision", "approve",
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
                    return {"content": [{"type": "text", "text": f"resume spawn error: {exc}"}]}
                payload = json.dumps({"gate2_decision": "approve", "run_id": run_id,
                                      "message": "Đang chạy task graph đã duyệt..."})
                return {"content": [{"type": "text", "text": payload}]}

            elif _g2_reject and not _g2_approve:
                log_dir = Path(config.storage_root) / "ui_logs"
                log_dir.mkdir(parents=True, exist_ok=True)
                log_path = log_dir / f"phase_b_resume_{run_id[:8]}.log"
                pb_argv = [
                    sys.executable, "-m", "ai_dev_system.cli.main",
                    "phase-b", "resume-gate2", "--run-id", run_id, "--decision", "reject",
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
                    return {"content": [{"type": "text", "text": f"resume spawn error: {exc}"}]}
                payload = json.dumps({"gate2_decision": "reject", "run_id": run_id,
                                      "message": "Đã từ chối, huỷ run."})
                return {"content": [{"type": "text", "text": payload}]}

            else:
                # neither matched, or both matched (ambiguous)
                guidance = "Gõ rõ 'duyệt' HOẶC 'từ chối' để quyết định task graph."
                return {"content": [{"type": "text", "text": guidance}]}

        # ------------------------------------------------------------------ #
        # Non-gate status → guidance                                          #
        # ------------------------------------------------------------------ #
        if run_status != "PAUSED_AT_GATE_1":
            guidance = (
                f"Run không ở trạng thái chờ duyệt "
                f"(status={run_status}). "
                "Dùng dev_run_status để kiểm tra tiến độ."
            )
            return {"content": [{"type": "text", "text": guidance}]}

        # ------------------------------------------------------------------ #
        # Gate-1 routing (PAUSED_AT_GATE_1)                                  #
        # ------------------------------------------------------------------ #
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

            # Unresolved-questions guard (mirrors webui._do_gate1_approve):
            # For "confirm", block if any question is not yet answered.
            # For "approve_all", set approved_all=True and proceed (explicit accept-defaults).
            if pr.action_type == "approve_all":
                state.approved_all = True
                save_state(run_id, state, conn)
                conn.commit()
            else:
                # confirm: guard against unresolved questions
                unresolved = [q.id for q in ctx.questions if not state.is_resolved(q.id)]
                if unresolved:
                    ids_str = ", ".join(unresolved)
                    guidance = (
                        f"Còn {len(unresolved)} câu chưa trả lời: {ids_str}. "
                        "Trả lời hoặc gõ 'approve all'."
                    )
                    return {"content": [{"type": "text", "text": guidance}]}

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
            # Clear the gate session state so a finalized gate leaves no stale
            # resolved choices behind (matches webui._do_gate1_approve).
            clear_state(run_id, conn)
            conn.commit()

            # Spawn Phase B detached — pauses at Gate 2 for human review
            log_dir = Path(config.storage_root) / "ui_logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"phase_b_{run_id[:8]}.log"

            pb_argv = [
                sys.executable, "-m", "ai_dev_system.cli.main",
                "phase-b", "to-gate2", "--run-id", run_id,
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

    # ------------------------------------------------------------------ #
    # Tool 4: dev_task_start                                              #
    # ------------------------------------------------------------------ #

    @tool(
        "dev_task_start",
        "Start a coding task on THIS bot's bound repo (existing repo). Generates a "
        "task spec + plan; reply 'duyệt' to run it and get a PR. Only works if the bot "
        "is repo-bound.",
        {"task_description": str},
    )
    async def dev_task_start(args: dict[str, Any]) -> dict[str, Any]:
        if not _repo_path:
            return {"content": [{"type": "text", "text":
                "Bot này chưa gắn repo. Chạy `ai-dev telegram setup` và nhập đường dẫn repo."}]}
        task_description: str = args["task_description"]
        spec_id = make_spec_id()
        log_dir = Path(config.storage_root) / "ui_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        argv = [
            sys.executable, "-m", "ai_dev_system.task_graph.single_task_worker",
            "--id", spec_id, "--idea", task_description, "--repo", _repo_path,
            "--storage-root", str(config.storage_root),
            "--database-url", str(config.database_url),
        ]
        try:
            with open(log_dir / f"task_{spec_id[:8]}.log", "a", encoding="utf-8", errors="replace") as logf:
                _spawn_worker(argv, stdout=logf, stderr=subprocess.STDOUT, cwd=str(_REPO_ROOT))
        except Exception as exc:  # pragma: no cover
            return {"content": [{"type": "text", "text": f"spawn error: {exc}"}]}
        chat_task_store.set_pending(surface, chat_id, spec_id=spec_id,
                                    repo=_repo_path, base_branch=_base_branch)
        text = json.dumps({"spec_id": spec_id, "status": "spec_generating",
                           "note": "Đang tạo spec + plan. Hỏi trạng thái rồi nhắn 'duyệt' để chạy."})
        return {"content": [{"type": "text", "text": text}]}

    return [dev_newproject_start, dev_run_status, dev_answer_gate, dev_task_start]
