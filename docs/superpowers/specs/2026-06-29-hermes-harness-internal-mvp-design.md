# Hermes + Harness — Internal MVP — Design

> **Status:** design draft 2026-06-29, awaiting review.
> **Provenance:** synthesizes (1) the 2026-06-29 "is ai-dev a Hermes-agent + Agent-harness combination?"
> gap analysis (verdict: *partial-core-only* — ai-dev **rents** its tool-use loop from `claude -p`,
> owns ~0% of the harness half and ~0% of the Hermes conversational envelope, but owns a superior
> third thing: the planning + TDD + learning compiler) and (2) four scope decisions + two flow-coverage
> decisions confirmed with the user 2026-06-29.
> **Scope (this spec):** build the **smallest** thing that legitimately makes ai-dev a *Hermes + harness*
> combination for **internal single-user** use — an owned agentic harness, a persistent conversational
> envelope (memory + crash-resumable sessions + budget), reachable from Telegram/Discord/terminal, that
> drives **both** existing flows (single-task and new-project) as tools. Everything not needed for that
> first closable loop is an explicit, sequenced **deferral**, not a silent omission.

## Goal

A persistent conversational agent (`assistant`) that **owns its tool-use loop** via the Claude Agent SDK
(on the Max subscription, **zero extra API cost**), carries **long-term memory** (`MEMORY.md` + `USER.md`)
and **crash-resumable sessions**, tracks **budget**, is reachable from **Telegram / Discord / local REPL**,
and exposes the existing ai-dev pipeline as **tools** so the operator can run — and approve — both the
**single-task** and **new-project** flows entirely from chat.

This is the "combination": **harness** (the assistant owns a real ReAct loop + tools + permissions) wrapped
in the **Hermes envelope** (memory, sessions, surfaces, reactive push) around the **existing pipeline**
(intake → debate → spec → plan → TDD execution → verification → learning), which becomes a callable capability.

## Scope boundary

**Scope decision (`#1` is load-bearing): the new harness applies to the _conversational agent_, not to the
code-writing executor.** The pipeline's internal implementer (`repo_branch_agent` via `claude -p`) keeps
delegating in v1; it is wrapped as a tool. Porting the executor onto the owned harness is a **later phase**.
This already satisfies the gap analysis's one *blocking* finding ("ai-dev owns no tool loop on any path") —
after v1, ai-dev owns one (the assistant's) — without rewriting the heaviest subsystem.

| In v1 | Deferred (sequenced, not excluded) |
|---|---|
| Owned harness via Agent SDK (tools, permissions, prompt, budget, observability) | Port the code-executor onto the owned harness |
| Long-term memory: `MEMORY.md` + `USER.md` (load + inject + explicit `memory` tool) | LLM "librarian" auto-save / background-review of memory |
| Crash-resumable sessions (turn-level, clean-shutdown marker, auto-resume) | Intra-turn checkpoint / process supervisor |
| Budget rollup (per session + per run, optional cap) | Per-token cost model for non-Max API spend |
| Surfaces: **Telegram + local REPL** (first slice); **Discord** fast-follow (same ABC) | Voice/TTS, desktop app, additional chat platforms |
| Assistant **drives intake conversationally** (new project from chat) | — |
| **Reactive** one-shot push on gate/terminal state change | Full cron / scheduler / recurring jobs / wall-clock autonomy |
| Single-task flow refactored to **spec → plan → exec** (plan reviewable) | — |
| **Spec self-review** critic (placeholder/consistency/scope/ambiguity) on both flows | — |
| In-process tools only | External MCP client/server, ACP surface |
| Single provider (Claude via SDK) | Multi-provider routing + failover + credential rotation |
| — | Conversational context compression (v1 uses a simple recent-history window) |

## Key decisions (locked with user 2026-06-29)

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | Harness route | **Claude Agent SDK** | Owns tools/permissions/prompt + observes every step, **stays on Max ($0 API)**. The raw Messages-API loop needs an `ANTHROPIC_API_KEY` (subscription OAuth does not authorize raw `messages.create`) → abandons the no-cost benefit. SDK is the only route that owns the loop *and* stays on Max. |
| 2 | Integration model | **Conversational front-door wraps pipeline as tools** | The clean "combination"; existing intake→…→verify becomes callable tools the agent invokes and reports on. |
| 3 | v1 envelope scope | **Memory + crash-resumable sessions + budget rollup** | The "Hermes identity" minimum. Cron/proactive deferred (then partially pulled back — see #6). |
| 4 | Surfaces | **Telegram + local REPL first; Discord fast-follow** (same `Platform` ABC) | Ship the first closable loop on Telegram + REPL; Discord lands right after through the same adapter ABC, so the abstraction is built once and the first slice carries no `discord.py` dependency. |
| 5 | New-project intake | **Assistant drives intake conversationally** | The existing wizard is an interactive terminal program — undriveable from chat. Expose the serializable `IntakeState` as a stepwise tool so the agent conducts intake field-by-field in chat. |
| 6 | Long gated-flow UX | **Reactive one-shot push on gate/terminal** (no cron) | A gated new-project flow you can't be notified about is not a closable loop. Pull forward a *state-change* notifier (no schedule store, no recurring jobs). |
| 7 | Single-task shape | **spec → plan → exec** (plan reviewable before exec) | `plan` already exists as the TDD task graph but is fused inside `run_executor`; extract it into a reviewable step, symmetric with new-project Gate 2 and with human-as-approver. |
| 8 | Spec self-review | **AI critic on the 4 superpowers dimensions, on BOTH flows** | The existing grounding check is a *different axis* (traceability/measurability/scope-leak); add a critic for placeholder/consistency/scope/ambiguity, reusing the existing repair + gate-warning machinery, applied to the new-project SpecBundle **and** the single-task facet spec. |

## Architecture

```
[Telegram]  [Discord]  [Local REPL]
     \          |          /            gateway/  — Platform ABC + registry + run daemon + notifier
      v         v         v
      InboundMessage(platform, chat_id, text) ──► chat_id allowlist (only the operator)
                    │
                    ▼
      assistant/  Assistant.handle(session)
        ├─ SessionManager   transcript bền + resume sau crash      ◄── SQLite (db/)
        ├─ MemoryStore      MEMORY.md + USER.md → system prompt     ◄── ~/.ai-dev/assistant/
        └─ PromptBuilder    base persona + memory + tool docs
                    │
                    ▼
      harness/  AgentRuntime.run_turn(system, history, tools)  ← Claude Agent SDK (auth: Max)
        model → tool_use → permissions.can_use_tool → dispatch tool cục bộ
                    │                       ├─ builtin: file/bash/web      (gated)
                    │                       ├─ memory:  add/replace/remove
                    │                       ├─ dev_intake_*       ─┐
                    │                       ├─ dev_*  (newproject) ├─► pipeline ai-dev hiện có
                    │                       └─ dev_singletask_*   ─┘   (chạy nền → runs/task_runs)
                    ▼
        usage → BudgetTracker (rollup/cap)
                    │
                    ▼
      outbound → gateway delivery → đúng surface
                    ▲
      RunStatusWatcher (gateway/notifier.py): runs.status → PAUSED_AT_GATE_* | terminal
        ⇒ delivery.send MỘT lần (dedup) tới chat gốc của run   [reactive, KHÔNG cron]
```

### Module layout

```
src/ai_dev_system/
  harness/                      # the OWNED agent runtime  ("Agent harness" half)
    runtime.py                  # AgentRuntime: wraps Agent SDK; run_turn(system, history, tools) -> events + final + usage
    permissions.py              # can_use_tool callback + PreToolUse/PostToolUse hooks (allow / deny / confirm)
    budget.py                   # BudgetTracker: per-session + per-run rollup + optional caps
    tools/
      registry.py               # ToolRegistry: @tool registration -> SDK in-process MCP server
      builtin.py                # file/bash/web tools (or SDK built-ins) — all routed through permissions
      memory_tool.py            # memory(add|replace|remove, target=MEMORY|USER, text)  — file-locked
      dev_intake.py             # dev_intake_start / dev_intake_answer        (drives IntakeState)
      dev_pipeline.py           # dev_newproject_start / dev_run_status / dev_answer_gate  (gate-aware)
      dev_singletask.py         # dev_singletask_spec / _plan / _review / _exec / _accept
  assistant/                    # conversational orchestration (ties harness + memory + session)
    agent.py                    # Assistant.handle(inbound) -> orchestrates one turn
    session.py                  # SessionManager + SessionStore (SQLite transcript + state machine + resume)
    memory.py                   # MemoryStore: load/inject + locked writes of MEMORY.md / USER.md
    prompt.py                   # PromptBuilder: base persona + memory + tool docs
    run_links.py                # run_id <-> (platform, chat_id, session_id, kind)  for status routing
  gateway/                      # surfaces ("Hermes envelope" surface layer)
    base.py                     # Platform ABC + InboundMessage + DeliveryTarget
    registry.py                 # PlatformRegistry (enable/configure surfaces via env/config)
    telegram.py                 # Telegram long-poll adapter (HTTP getUpdates) — lightest — FIRST SLICE
    local_cli.py                # terminal REPL adapter — FIRST SLICE
    discord.py                  # Discord gateway adapter (websocket; adds dep: discord.py) — FAST-FOLLOW (not first slice)
    notifier.py                 # RunStatusWatcher: reactive one-shot push on run state change
    run.py                      # daemon: start enabled platforms, dispatch -> Assistant, deliver; lifecycle
```

**CLI entry:** `ai-dev assistant` → launches `gateway/run.py` with the configured surfaces.

## Components

### `harness/runtime.py` — AgentRuntime (owns the loop)

- **Interface:** `run_turn(system_prompt, history, tools, *, model, max_turns) -> TurnResult` where
  `TurnResult = {final_text, events, usage}`. Streams `events` (assistant text, tool_use, tool_result)
  so the gateway can show progress.
- **Auth:** Claude Agent SDK using the **Max subscription** (`claude login`); **`ANTHROPIC_API_KEY` must be
  unset** in the process env (if set, the SDK silently switches to API billing). Documented in SETUP.
- **Tools:** registered in-process via the SDK's `@tool` / in-process MCP server (`tools/registry.py`).
- **Ownership reality (honest):** the SDK orchestrates the per-turn loop over the Claude Code engine. We own
  the **tool set, permissions, system prompt, history, budget, and full event observability** — ~80% of "own
  the harness" at ~20% of the cost of a raw-API loop, and the only route compatible with Max. The raw-API
  loop (100% ownership, paid API) is a deferred option, not v1.

### `harness/permissions.py` — permission gate

- **Interface:** `can_use_tool(tool_name, tool_input, session) -> ALLOW | DENY | CONFIRM`, plus
  `PreToolUse` / `PostToolUse` hooks for logging and budget checks.
- **Policy (v1):**
  - **ALLOW:** read-only/safe tools — `Read`, `Grep`, `Glob`, web fetch, every `dev_*_status` / read tool.
  - **CONFIRM:** destructive/outward — `Bash` containing `rm`/`git push`/etc., `Write` outside the target
    repo, and `dev_singletask_accept` (pushes + opens a PR). The assistant surfaces the action and waits for
    an explicit operator "ok" in chat before proceeding. (Human-as-approver, reused.)
  - **DENY:** anything touching paths outside the workspace allowlist.
- A per-session "approved for this session" set lets the operator pre-bless a repeated action.

### `harness/budget.py` — BudgetTracker

- Records `usage` from each `run_turn` into `assistant_messages` (per session) and, for pipeline runs, the
  executor's already-emitted `total_cost_usd`
  ([`repo_branch_agent.py:65`](../../../src/ai_dev_system/agents/repo_branch_agent.py)) into `assistant_run_links`.
- Per-session and per-run rollups; **optional** soft cap (warn in chat) and hard cap (refuse to start a new
  turn/run). Caps configured via env; off by default.

### `harness/tools/` — the tool surface

**Memory** — `memory(action, target, text)` writes `MEMORY.md` (agent facts/conventions) or `USER.md`
(operator preferences/style), file-locked. v1: the agent calls it explicitly; the background "librarian" is deferred.

**Intake (new project, conversational)** — drives the existing serializable `IntakeState`
([`intake/engine.py`](../../../src/ai_dev_system/intake/engine.py)) **programmatically** (not the interactive CLI loop):
- `dev_intake_start(idea, repo?) -> {run_id, question}` — create a run in `COLLECTING_INTAKE`, return the first field's question (+ AI suggestion).
- `dev_intake_answer(run_id, answer) -> {next_question} | {brief_ready, run_id}` — apply one field, advance the state machine; on `DONE`, promote the brief.

**New project (multi-stage, two gates)** —
- `dev_newproject_start(run_id)` — run [`run_debate_pipeline`](../../../src/ai_dev_system/debate_pipeline.py) in the **background** → `PAUSED_AT_GATE_1`.
- `dev_run_status(run_id) -> {status, gate?, payload?}` — generic status; when paused, `payload` carries the Gate-1 debate questions or the Gate-2 task graph.
- `dev_answer_gate(run_id, payload)` — **gate-aware**: Gate 1 routes answers through the existing NLU
  ([`gate/gate1_review/nlu.py`](../../../src/ai_dev_system/gate/gate1_review/)) (answer / expand / edit_brief / approve);
  Gate-1 approve **auto-resumes** [`run_phase_b_pipeline`](../../../src/ai_dev_system/debate_pipeline.py); Gate 2 approves/rejects the task graph. Execution + verification run automatically after Gate 2.

**Single task (spec → plan → exec)** — see *Single-task flow* below for the refactor.
- `dev_singletask_spec(task, repo) -> {spec_id}` — task definition + 13 facets ([`task_graph/facets.py`](../../../src/ai_dev_system/task_graph/facets.py)); facets editable in chat.
- `dev_singletask_plan(spec_id) -> {plan_id, graph}` — build + persist the `TASK-TEST → TASK-IMPL` graph as a reviewable artifact.
- `dev_singletask_review(plan_id, decision)` — approve ⇒ unlock exec; revise (facets/approach) ⇒ re-plan.
- `dev_singletask_exec(plan_id) -> {run_id}` — execute the **approved** plan in the background.
- `dev_singletask_accept(run_id, decision) -> {pr_url} | {deleted}` — accept ⇒ push + `gh pr create`; reject ⇒ delete branch. (`CONFIRM`-gated.)

### `assistant/` — orchestration

- `Assistant.handle(inbound)`: resolve `(platform, chat_id) → session_id`; load session + memory; build the
  system prompt; `run_turn`; stream output to the surface; persist transcript + usage; (post-turn) optional
  memory write if the agent called `memory`.
- `SessionStore` (SQLite): one persistent transcript per `(platform, chat_id)`; state machine
  `active | resume_pending | suspended`.
- `MemoryStore`: `MEMORY.md` + `USER.md` under `~/.ai-dev/assistant/` (override `AI_DEV_ASSISTANT_HOME`),
  injected into every system prompt. Shared across surfaces (single internal operator).
- `PromptBuilder`: v1 bounds context with a **simple recent-history window** (last *N* turns of the
  transcript) — a crude cap, **not** compression. True conversational context compression is deferred; the
  full transcript stays durable in `assistant_messages` regardless of what the window sends to the model.
- `run_links`: persists `run_id ↔ (session_id, platform, chat_id, kind)` at start so the notifier knows
  where to deliver.

### `gateway/` — surfaces + lifecycle + notifier

- `Platform` ABC: `start()` (begin ingestion), `send(chat_id, text)`, normalize inbound → `InboundMessage`.
  - `telegram.py`: long-poll `getUpdates` (plain HTTP, lightest). `discord.py`: gateway websocket (**adds dep
    `discord.py`**). `local_cli.py`: terminal REPL.
- `PlatformRegistry`: enable/configure surfaces from env/config; **`chat_id` allowlist** (operator only) enforced on every inbound.
- `run.py` daemon **lifecycle:** on graceful exit (SIGINT/SIGTERM → drain) write `~/.ai-dev/assistant/.clean_shutdown`;
  on startup, if the marker is **absent**, mark sessions updated within *N* minutes `resume_pending=1`, then delete the marker.
  The next inbound on a `resume_pending` session restores its transcript and continues, clearing the flag only after a successful turn.
- `notifier.py` `RunStatusWatcher`: a **state-change** watcher (DB poll every ~5–10 s over runs in
  `run_links`). On a transition to `PAUSED_AT_GATE_*` or terminal (`COMPLETED`/`FAILED`/`ABORTED`) not yet in
  `assistant_notifications`, `delivery.send` **once** to the linked chat, then record it. **No schedule store,
  no recurring jobs, no wall-clock tasks** — it fires only on a real run state change. (This is the single
  piece pulled forward from the deferred cron/proactive layer.)

## The two flows, end-to-end

**Both flows now share the shape `… → plan → review → exec`, with review before exec (human-as-approver).**

### New Project
`dev_intake_start` → *(chat Q&A)* `dev_intake_answer`×N → brief promoted → `dev_newproject_start`
→ **Gate 1** (`RunStatusWatcher` pushes; operator answers via `dev_answer_gate`, NLU) → auto `run_phase_b_pipeline`
→ spec → **spec self-review** (critic → repair) → **plan = task graph → Gate 2** (push; remaining self-review +
grounding findings shown; `dev_answer_gate` approve) → execution → verification → (push: completed).

### Single Task
`dev_singletask_spec` → **spec self-review** (findings → chat; operator edits facets or accepts) →
`dev_singletask_plan` → **`dev_singletask_review`** (operator approves the `TASK-TEST → TASK-IMPL` plan in chat)
→ `dev_singletask_exec` → `dev_singletask_accept` (push + PR, `CONFIRM`-gated).

## Single-task flow: spec → plan → exec (targeted refactor)

Today `plan` is **fused into exec**: [`run_executor`](../../../src/ai_dev_system/task_graph/single_task_executor.py)
calls [`_build_task_graph`](../../../src/ai_dev_system/task_graph/single_task_executor.py) at execution time,
so the `TASK-TEST → TASK-IMPL` plan is never a separate, reviewable artifact.

**Refactor (small, in service of the flow):**
1. Extract `_build_task_graph` into a `plan_single_task(spec) -> plan` step.
2. Persist the plan as an artifact (`task_specs/{spec_id}-plan.json` or a `TASK_GRAPH` artifact row).
3. `run_executor` takes a **plan_id** and executes the persisted, **approved** plan instead of rebuilding it.
4. Add the `dev_singletask_review` checkpoint (and surface the plan in the existing webui too, for parity).

The plan shown for review = the test task + impl task + branch + the key facets summary. (`auto_approve_plan`
for trivial tasks is a later toggle, not v1.)

## Spec Self-Review (AI critic) — both flows

A distinct LLM **critic** reviews the spec the system just authored, on the **four superpowers
brainstorming self-review dimensions**. This is *complementary* to the existing grounding checks
([`spec/grounding.py`](../../../src/ai_dev_system/spec/grounding.py)), which cover a different axis
(traceability / measurable AC / scope-leak / optional hallucination). The critic adds the dimensions
grounding does **not**: authored-placeholder, cross-section contradiction, scope-decomposition, and ambiguity.

**Criteria — the 4 superpowers dimensions, unchanged (locked decision #8):**
1. **Placeholder scan** — any `TBD` / `TODO` / "to be decided" / vague requirement in *authored* content
   (distinct from the degraded-section placeholders the pipeline already flags at
   [`pipeline.py:143`](../../../src/ai_dev_system/spec/pipeline.py)).
2. **Internal consistency** — contradictions across sections/facets (design ↔ functional ↔ acceptance criteria;
   or facet ↔ facet ↔ objective).
3. **Scope check** — does the spec fit **one** implementation plan / one task graph, or must it be decomposed?
   (Single-task: is this *truly* one atomic task, or several hiding as one?)
4. **Ambiguity check** — any requirement readable two ways → pick one explicitly, or flag for the human.

**One critic module, two callers** — `spec/self_review.py` :
`self_review(payload, kind) -> [Finding(section, dimension, severity, fix)]`.

- **New Project** (`SpecBundle`): runs as **Stage 3.5** of
  [`run_spec_pipeline`](../../../src/ai_dev_system/spec/pipeline.py) — after generators + grounding, before the
  bundle is finalized. Auto-repairable findings feed the **existing** `repair_section` budget
  ([`spec/repair.py`](../../../src/ai_dev_system/spec/repair.py)); remaining high-severity findings ride the
  **existing** `grounding_violations` metadata channel → surfaced to the human at **Gate 2**.
- **Single Task** (task + 13 facets): runs inside `dev_singletask_spec`, which returns `{spec_id, findings}`.
  The assistant relays findings in chat; the operator edits facets (or accepts) **before** `dev_singletask_plan`.
  The **scope** dimension is the key catch here — it flags a "single task" that is really several and should
  go through the new-project pipeline instead.

**Flag-gated** (mirrors `grounding_llm_check`): `AI_DEV_SPEC_SELF_REVIEW` — default **on** for both flows;
off → legacy behavior. The critic is advisory to the repair loop and the human gate; it **never silently
rewrites intent** — high-severity/structural findings (esp. scope-decomposition) always reach the human.

## Data model

New SQLite tables (reuse [`db/`](../../../src/ai_dev_system/db/)); memory lives on disk:

- `assistant_sessions(session_id PK, platform, chat_id, status, updated_at, resume_pending, last_event_at)`
- `assistant_messages(id PK, session_id FK, role, content, ts, input_tokens, output_tokens, cost_usd)`
- `assistant_run_links(run_id PK, session_id, platform, chat_id, kind)` — `kind ∈ {newproject, singletask}`
- `assistant_notifications(run_id, status, notified_at, PRIMARY KEY(run_id, status))` — notifier dedup
- On disk: `~/.ai-dev/assistant/{MEMORY.md, USER.md, .clean_shutdown}` (override `AI_DEV_ASSISTANT_HOME`).

## Crash-resume & lifecycle (turn-level)

- Each turn is persisted to `assistant_messages` immediately; the daemon writes `.clean_shutdown` on graceful exit.
- Restart with **no** marker ⇒ recently-active sessions become `resume_pending`; their transcript is restored and the next inbound continues the same conversation.
- A crash **mid-turn** loses the in-flight turn (the model turn runs inside the SDK/engine we don't checkpoint);
  resume = re-run the last operator message. This is **turn-level** resilience (the Hermes bar is process-level;
  honestly noted as a v1 limit). Pipeline runs themselves keep their existing, stronger task-graph recovery
  (heartbeat dead-task recovery + idempotent re-materialization).

## Error handling

- Tool failure ⇒ returned as a `tool_result(error)` so the loop can recover; persistent transcript intact.
- Model/turn error ⇒ short backoff retry, then a friendly message to the surface; transcript persisted.
- Pipeline tools never block a turn — they start background work and return a `run_id`; progress via
  `dev_run_status` (pull) and `RunStatusWatcher` (one-shot push).
- Surface delivery failure ⇒ log + one retry.

## Testing strategy (mirrors the existing StubAgent pattern)

- **Harness:** a `FakeRuntime` / fake SDK transport replays scripted `tool_use` + `usage` → exercises the loop,
  permission gate, and budget **without network**.
- **Tools:** `dev_*` tools run the pipeline in **stub LLM mode** (existing stubs); `dev_intake_*` drive a scripted `IntakeState` to `DONE`.
- **Session:** persist → simulate restart **without** marker → assert `resume_pending` + transcript restored; mid-turn crash → last-message replay.
- **Memory:** load + inject + `memory` tool round-trip with file locking.
- **Budget:** per-session and per-run aggregation + cap enforcement.
- **Gateway:** Telegram/Discord adapters against fake transports (mock `getUpdates` / mock ws); `chat_id` allowlist enforced.
- **Notifier:** a state transition fires **exactly once** (dedup) and routes to the correct chat via `run_links`.
- **Single-task plan:** `plan` artifact persisted; `exec` consumes the **approved** plan; the review gate blocks exec until approved.
- **Spec self-review critic:** with a stub LLM returning scripted findings — assert (a) placeholder/contradiction/ambiguity findings route auto-repairable items into `repair_section` and surface the rest as gate metadata (new-project); (b) `dev_singletask_spec` returns findings and a **scope** finding flags an over-large "single task" (single-task); (c) `AI_DEV_SPEC_SELF_REVIEW=0` restores legacy behavior.

## Honest caveats (do not over-claim)

1. **~80% harness ownership.** The SDK orchestrates the per-turn loop over the Claude Code engine; we own tools/permissions/prompt/history/budget/observability. Raw-API 100%-owned loop is deferred (and costs API $).
2. **Turn-level resume**, not process-level/intra-turn checkpoint.
3. **The code executor still delegates** to `claude -p`; porting it onto the owned harness is a later phase.
4. **ToS:** confirm that programmatic/automated internal use of the Agent SDK on a Max subscription is permitted before relying on it at volume. (Open item.)

## Open questions (resolve during planning)

1. **Memory home:** global `~/.ai-dev/assistant/` (proposed — operator is global) vs per-project. → propose global.
2. **Discord:** *resolved 2026-06-29* — ship **Telegram + REPL first**; **Discord is a fast-follow** right after (same `Platform` ABC, adds dep `discord.py` only at that point). The first implementation slice does not depend on Discord.
3. **Plan richness:** v1 plan = task graph + facets summary; add an LLM "approach summary" later?
4. **Confirm-destructive UX:** chat confirm vs per-session pre-blessed allowlist (both supported; default = chat confirm).

## Sequenced follow-ups (after v1)

Port executor onto owned harness · full cron/scheduler + recurring jobs · LLM "librarian" auto-memory ·
multi-provider routing + failover + credential rotation · conversational context compression · process-level
session resilience · voice/desktop surfaces · external MCP/ACP.
