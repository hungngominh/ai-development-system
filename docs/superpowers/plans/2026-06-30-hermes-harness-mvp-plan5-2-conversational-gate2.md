# Plan 5.2 — Conversational Gate 2 (Phase B suspend/resume) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make Gate 2 (task-graph approval) reviewable from chat: after Gate 1, the pipeline runs to the generated task graph and **pauses at `PAUSED_AT_GATE_2`** (with a one-shot push); the operator reviews + approves/rejects in chat; on approve the pipeline **resumes** into execution + verification. Completes the new-project chat loop end to end (replaces 5.1's auto-approve).

**Architecture:** Split `run_phase_b_pipeline` into two shared halves — `_phase_b_spec_and_graph` (finalize_spec → generate_task_graph → TASK_GRAPH_GENERATED) and `_phase_b_promote_and_execute` (promote TASK_GRAPH_APPROVED → beads_sync → RUNNING_PHASE_3 → execution → Phase V). The existing synchronous `run_phase_b_pipeline(..., gate2_io)` is rebuilt by composing the two halves around `run_gate_2` — **behavior identical, all existing tests stay green**. Two new entry points drive the conversational path: `run_phase_b_to_gate2` (pause) and `resume_phase_b_after_gate2` (approve→execute / reject→abort). The harness's `dev_answer_gate` becomes status-aware (Gate 1 vs Gate 2), the notifier pushes on `PAUSED_AT_GATE_2`, and `dev_run_status` shows the task graph.

**Tech Stack:** Python stdlib, SQLite, the existing debate/engine machinery, detached `subprocess.Popen`, the Plan-5.1 harness tools + notifier, pytest stub-LLM/StubAgent.

## Global Constraints

- **Task 1 is a PURE behavior-preserving refactor.** `run_phase_b_pipeline`'s observable behavior (entry guard `RUNNING_PHASE_1D`, TASK_GRAPH_APPROVED promotion, execution, Phase V triggers, `PhaseBResult`) must be UNCHANGED. The 7 existing tests MUST stay green untouched: `tests/integration/test_spec_pipeline_phase_b.py` (3), `tests/integration/test_loop_closes.py` (1), `tests/unit/test_debate_pipeline_phase_v.py` (3, which `patch("ai_dev_system.debate_pipeline.run_gate_2")` — keep that symbol importable at that path).
- **Reuse the existing status machine:** `PAUSED_AT_GATE_2` is already a legal `runs.status` (control-layer-schema.sql); resume sets `PAUSED_AT_GATE_2 → RUNNING_PHASE_3` (the only transition `materialize_task_runs` accepts besides CREATED/2A). Do NOT invent new statuses.
- **`Gate2Result.status` is `"approved"`/`"rejected"`** (not approve/reject). `run_gate_2(envelope, io) -> Gate2Result(status, graph)`.
- **Long work runs detached:** the resume (execution+verification, up to 30 min) spawns a detached process, exactly like 5.1's Phase B spawn — never block a chat turn.
- **REUSE 5.1:** extend `dev_answer_gate`/`dev_run_status`/`RunStatusWatcher`; do NOT duplicate them.
- **README test-count chore**; **stdlib only**; **UTF-8**.

---

### Task 1: Refactor `run_phase_b_pipeline` into composable halves (behavior-preserving)

**Files:**
- Modify: `src/ai_dev_system/debate_pipeline.py`
- Tests: the existing 7 must stay green; add 1 characterization test if helpful.

**Interfaces (produced; consumed by Task 2):**
- `_phase_b_spec_and_graph(run_id, config, conn, *, pick, task_run_repo, event_repo) -> tuple[str, dict, dict]` returns `(spec_artifact_id, envelope, task_run_g2)` after promoting SPEC_BUNDLE + TASK_GRAPH_GENERATED. (Steps 1-2 + the `task_run_g2 = create_sync("task_graph_gate2")` row, verbatim from current lines 330-377.)
- `_phase_b_promote_and_execute(run_id, config, conn, *, graph, spec_artifact_id, task_run_g2, agent, llm_client, pick) -> "ExecutionResult | None"` does: write `graph` as `task_graph.json` → promote TASK_GRAPH_APPROVED → `beads_sync` → `conn.commit()` → status `→RUNNING_PHASE_3` (from RUNNING_PHASE_1D **or** PAUSED_AT_GATE_2) → `run_execution` → Phase V if COMPLETED → `conn.commit()`. (Current lines 386-438, with the status-update WHERE clause widened to accept both source statuses.)

- [ ] **Step 1: Characterize current behavior** — run the existing suite for this module to confirm the green baseline:
  `python -m pytest tests/integration/test_spec_pipeline_phase_b.py tests/integration/test_loop_closes.py tests/unit/test_debate_pipeline_phase_v.py -q -p no:cacheprovider`
  Expected: all pass. (This is your regression oracle for the refactor.)

- [ ] **Step 2: Extract the two helpers** — move the current bodies into `_phase_b_spec_and_graph` (lines ~330-377) and `_phase_b_promote_and_execute` (lines ~386-438). In `_phase_b_promote_and_execute`, change the pre-execution status update from
  `WHERE run_id=? AND status='RUNNING_PHASE_1D'` to
  `WHERE run_id=? AND status IN ('RUNNING_PHASE_1D','PAUSED_AT_GATE_2')`
  (so the same helper serves both the synchronous path and the resume path). Everything else verbatim.

- [ ] **Step 3: Rebuild `run_phase_b_pipeline` by composition** — keep its signature + the `RUNNING_PHASE_1D` entry guard + the `pick`/`conn`/repos setup, then:
```python
    spec_artifact_id, envelope, task_run_g2 = _phase_b_spec_and_graph(
        run_id, config, conn, pick=pick, task_run_repo=task_run_repo, event_repo=event_repo,
    )
    gate2_result = run_gate_2(envelope, gate2_io)
    if gate2_result.status == "rejected":
        task_run_repo.mark_failed(task_run_g2["task_run_id"], "EXECUTION_ERROR", "user_rejected")
        raise PipelineAborted("User rejected task graph at Gate 2")
    execution_result = _phase_b_promote_and_execute(
        run_id, config, conn, graph=gate2_result.graph, spec_artifact_id=spec_artifact_id,
        task_run_g2=task_run_g2, agent=agent, llm_client=llm_client, pick=pick,
    )
    return PhaseBResult(run_id=run_id, graph_artifact_id=<the approved id>, execution_result=execution_result)
```
  (Thread the approved `graph_artifact_id` out of `_phase_b_promote_and_execute` — return `(execution_result, graph_artifact_id)` from it, or set it on a small result object. Keep `PhaseBResult` shape unchanged.)

- [ ] **Step 4: Run the regression oracle** (Step 1's command) → all green, unchanged. Run the FULL suite. **Do not bump README if no NET new tests** (a characterization test added → bump).

- [ ] **Step 5: Commit**
```bash
git add src/ai_dev_system/debate_pipeline.py [tests if added] README.md
git commit -m "refactor(phaseb): split run_phase_b_pipeline into spec_and_graph + promote_and_execute (behavior-preserving)"
```

---

### Task 2: `run_phase_b_to_gate2` (pause) + `resume_phase_b_after_gate2` (resume)

**Files:**
- Modify: `src/ai_dev_system/debate_pipeline.py`
- Test: `tests/integration/test_phase_b_gate2_pause_resume.py`

**Interfaces:**
- `run_phase_b_to_gate2(run_id, config, conn_factory, llm_client, *, llm_for=None) -> dict` — guard status `RUNNING_PHASE_1D`; `conn=conn_factory()`; `_phase_b_spec_and_graph(...)`; set status `PAUSED_AT_GATE_2`; `conn.commit()`; return `{"run_id", "task_graph_gen_id", "envelope"}`. **No Gate 2, no execution.**
- `resume_phase_b_after_gate2(run_id, config, conn_factory, *, decision, edited_graph=None, agent=None, llm_client=None, llm_for=None) -> "ExecutionResult | None"` — guard status `PAUSED_AT_GATE_2`; load the generated envelope (from `current_artifacts["task_graph_gen_id"]` → artifact `content_ref` → `f"{task_id}.json"`; the implementer: read it the way `_phase_b_spec_and_graph` wrote it, or re-load via the artifact row); `graph = edited_graph or envelope`. If `decision == "reject"`: mark the gate2 task_run failed + set status (e.g. `ABORTED`) + return None (raise `PipelineAborted` consistent with the sync path). If `decision == "approve"`: need `spec_artifact_id` (from `current_artifacts["spec_bundle_id"]`) + a fresh `task_run_g2` row (or reuse) → `_phase_b_promote_and_execute(...)`.

- [ ] **Step 1: Write the failing test** — drive a run to `RUNNING_PHASE_1D` (reuse the fixture/setup from `test_spec_pipeline_phase_b.py` which already gets a run there), then:
  - `run_phase_b_to_gate2(...)` → assert status becomes `PAUSED_AT_GATE_2`, a `TASK_GRAPH_GENERATED` artifact exists, NO execution ran, NO `TASK_GRAPH_APPROVED` yet.
  - `resume_phase_b_after_gate2(..., decision="approve", agent=StubAgent())` → assert `TASK_GRAPH_APPROVED` promoted, execution ran (status reaches a terminal `COMPLETED`/`PAUSED_AT_GATE_3`), `ExecutionResult` returned.
  - `resume_phase_b_after_gate2(..., decision="reject")` (separate run) → assert `PipelineAborted`/aborted status, no execution.
  Use `StubGate2IO`/`StubAgent` patterns from the existing integration tests; stub LLM via the existing fixtures.

- [ ] **Step 2: RED. Step 3: Implement** both functions (compose the Task-1 helpers). **Step 4: GREEN** + full suite. **Step 5: Commit**
```bash
git add src/ai_dev_system/debate_pipeline.py tests/integration/test_phase_b_gate2_pause_resume.py README.md
git commit -m "feat(phaseb): run_phase_b_to_gate2 (pause) + resume_phase_b_after_gate2 (approve/reject)"
```

---

### Task 3: CLI entry points for pause + resume (detached spawn targets)

**Files:**
- Modify: `src/ai_dev_system/cli/run_phase_b.py` (add a `--to-gate2` mode) OR create `src/ai_dev_system/cli/run_phase_b_gate2.py`
- Modify: `src/ai_dev_system/cli/commands/phase_b.py` (expose the verbs)
- Test: `tests/unit/test_phase_b_gate2_cli.py`

**Interface:** two non-interactive entry points the harness can spawn detached:
- `phase-b to-gate2 --run-id R` → builds llm_client/llm_for like `run_phase_b.main` (reuse `_make_llm_client`/the per-step resolver) and calls `run_phase_b_to_gate2(run_id, config, conn_factory, llm_client, llm_for=...)` then commits + exits.
- `phase-b resume-gate2 --run-id R --decision approve|reject` → loads agent (the same agent `run_phase_b.main` uses for execution — reuse that construction) and calls `resume_phase_b_after_gate2(...)`.

- [ ] **Step 1: Write failing tests** — invoke the entry `main([...])` with a seeded run (stub LLM) and assert the right pipeline function is called / status transitions. Mirror the existing `cli/run_phase_b.py` test style (stub mode). **Step 2: RED. Step 3: Implement** reusing `run_phase_b.py`'s client/agent construction (extract a shared helper if needed; don't duplicate). **Step 4: GREEN.** **Step 5: Commit**
```bash
git add src/ai_dev_system/cli/ tests/unit/test_phase_b_gate2_cli.py README.md
git commit -m "feat(cli): phase-b to-gate2 + resume-gate2 entries (detached, non-interactive)"
```

---

### Task 4: `dev_answer_gate` status-aware (Gate 1 → pause-at-Gate-2; Gate 2 → resume) + `dev_run_status` Gate-2 payload

**Files:**
- Modify: `src/ai_dev_system/harness/tools/dev_pipeline.py`
- Test: extend `tests/unit/harness/test_dev_answer_gate.py` + `test_dev_pipeline_tools.py`

**Changes:**
- `dev_answer_gate`: read `runs.status` first and ROUTE:
  - `PAUSED_AT_GATE_1` → existing Gate-1 NLU handling, BUT on approve/confirm replace the current `spawn phase-b run` with `spawn phase-b to-gate2 --run-id R` (so it pauses at Gate 2 instead of auto-approving). (finalize_gate1 + clear_state unchanged.)
  - `PAUSED_AT_GATE_2` → Gate-2 handling: parse a simple approve/reject from `text` (regex: "approve"/"duyệt"/"đồng ý" → approve; "reject"/"từ chối" → reject). On approve → `spawn phase-b resume-gate2 --run-id R --decision approve`; on reject → `--decision reject`. Return a confirmation message.
  - other status → guidance ("run không ở trạng thái chờ duyệt").
- `dev_run_status`: when status `PAUSED_AT_GATE_2`, load the generated graph (TASK_GRAPH_GENERATED via `current_artifacts["task_graph_gen_id"]`) and include a compact task list `[{id, title/objective, agent_type}]` so the operator can review it in chat.

- [ ] **Step 1: Write failing tests** — seed a `PAUSED_AT_GATE_2` run with a TASK_GRAPH_GENERATED artifact:
  - `dev_answer_gate(text="approve")` → injected `spawn_phase_b` (reuse the Task-3 spawn injectable) called once with `resume-gate2 ... --decision approve`; `text="reject"` → `--decision reject`.
  - a `PAUSED_AT_GATE_1` approve now spawns `to-gate2` (not `run`) — update/extend the Task-3-from-5.1 assertion.
  - `dev_run_status` on `PAUSED_AT_GATE_2` → returns the task list.
  Stub artifacts/loaders as in 5.1's gate tests.
- [ ] **Step 2: RED. Step 3: Implement. Step 4: GREEN.** **Step 5: Commit**
```bash
git add src/ai_dev_system/harness/tools/dev_pipeline.py tests/unit/harness/ README.md
git commit -m "feat(harness): dev_answer_gate routes Gate 1->pause-at-Gate2 and Gate 2->resume; status shows graph"
```

---

### Task 5: Notifier push on `PAUSED_AT_GATE_2`

**Files:**
- Modify: `src/ai_dev_system/gateway/notifier.py`
- Test: extend `tests/unit/gateway/test_notifier.py`

**Change:** add `PAUSED_AT_GATE_2` to `DEFAULT_PUSH_STATES`; message: f"🔔 Run {run_id[:8]} tới Gate 2 — xem & duyệt task graph". Dedup is already per-(run_id,state), so Gate 1 and Gate 2 pushes are distinct rows.

- [ ] **Step 1: Failing test** — seed a `run_links` row + a `runs` row at `PAUSED_AT_GATE_2`; `check_once()` pushes once (and again not, dedup); confirm a run that goes GATE_1→GATE_2 over two sweeps pushes BOTH (distinct states). **Step 2: RED. Step 3: Implement** (one-line states tuple + message branch). **Step 4: GREEN.** **Step 5: Commit**
```bash
git add src/ai_dev_system/gateway/notifier.py tests/unit/gateway/test_notifier.py README.md
git commit -m "feat(gateway): notifier pushes on PAUSED_AT_GATE_2"
```

---

## Acceptance (whole plan)
The new-project chat loop is complete with BOTH gates human-reviewed:
- Gate 1 approve → pipeline runs to the task graph and **pauses at PAUSED_AT_GATE_2** (push fires).
- Operator reviews the graph (`dev_run_status`) and approves/rejects in chat (`dev_answer_gate`).
- Approve → resume runs execution + verification → terminal push. Reject → aborts.
- `run_phase_b_pipeline` (synchronous/auto-approve) unchanged; all prior tests green.

## Risk + Self-Review (plan author)
- **Highest risk = Task 1 refactor of core `debate_pipeline.py`.** Mitigation: Task 1 is behavior-preserving with the 7 existing tests as the regression oracle, run BEFORE and AFTER; the only intentional change is widening one status-update WHERE clause to also accept `PAUSED_AT_GATE_2` (harmless for the sync path, which is never in that state). ✓
- **Resume must read the GENERATED graph correctly:** the generated artifact's filename is `{task_id}.json` (not `task_graph.json`); the plan flags this and says to load via the artifact row. The implementer MUST verify the filename when reading it back (named risk). ✓
- **Reuse, not duplicate:** Tasks 3-5 reuse `run_phase_b.py` client/agent construction, the 5.1 dev tools, and the 5.1 notifier — only extended. ✓
- **No new status invented; PAUSED_AT_GATE_2 is schema-legal and the materializer accepts RUNNING_PHASE_3 from it.** ✓
- **The real end-to-end (LLM + execution) is operator-smoke territory** (like 5.1); unit/integration tests use stubs. Flag the operator smoke as the real-path check.
