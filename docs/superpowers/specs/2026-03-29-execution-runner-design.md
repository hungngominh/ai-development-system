# Execution Runner v1 — Design

> Date: 2026-03-29
> Status: Draft
> Goal: Execute an approved task graph (IR level 2) reliably — with heartbeat, dead worker recovery, retry, failure propagation, and human-in-the-loop escalation
> Depends on: spec-pipeline-design, task-graph-generator-design
> Scope: Phase 3 only (post Gate 2 → task execution → completion/escalation)

---

## 1. Overview

```
TASK_GRAPH_APPROVED artifact
        │
        ▼
[Materializer]         graph → atomic task_runs in DB (idempotent)
        │
        ▼
[Runner]               spawn WorkerThread + BackgroundThread
        │
        ├──[WorkerThread]     pickup → agent.run() → promote → SUCCESS/FAILED
        │
        └──[BackgroundThread] recover_dead → mark_ready → check_completion
                │
                ▼
        run → SUCCESS | PAUSED_FOR_DECISION | FAILED | ABORTED
```

### Design Principles

- **DB is source of truth**: graph is read once at materialization; all runtime state lives in DB
- **Exactly-once-ish execution**: idempotency guards at DB level prevent duplicate work
- **Human-in-the-loop recovery**: task failure → escalation → human decides (retry / skip / abort)
- **Non-blocking worker**: agent runs outside any DB transaction; long tasks don't block the pool
- **Thread isolation**: each thread owns its own connection; no sharing
- **Background jobs first clean, then forward**: recover → ready → completion, every cycle

---

## 2. Entry Point

```python
def run_execution(
    run_id: str,
    graph_artifact_id: str,
    config: Config,
    agent: Agent,
    poll_interval_s: float = 5.0,
) -> ExecutionResult:
    """
    Full lifecycle: materialize → spawn threads → wait → return result.

    Args:
        run_id:             UUID of the run (must exist, status RUNNING_PHASE_3)
        graph_artifact_id:  UUID of TASK_GRAPH_APPROVED artifact
        config:             Config with storage_root, db_url, poll_interval, heartbeat settings
        agent:              Agent protocol implementation
        poll_interval_s:    Polling cadence for background jobs and worker idle sleep

    Returns:
        ExecutionResult(run_id, final_status)

    Raises:
        MaterializationError: graph cannot be loaded or is invalid
    """
    conn = get_connection(config)
    try:
        materialize_task_runs(conn, run_id, graph_artifact_id, config)
    finally:
        conn.close()

    stop_event = threading.Event()

    worker_thread = threading.Thread(
        target=worker_loop,
        args=(run_id, config, agent, stop_event),
        name=f"worker-{run_id[:8]}",
        daemon=True,
    )
    background_thread = threading.Thread(
        target=background_loop,
        args=(run_id, config, stop_event),
        name=f"bg-{run_id[:8]}",
        daemon=True,
    )

    worker_thread.start()
    background_thread.start()

    final_status = _wait_for_terminal_state(run_id, config, poll_interval_s)

    stop_event.set()
    worker_thread.join(timeout=30)
    background_thread.join(timeout=10)

    if worker_thread.is_alive():
        log.warning("Worker thread did not stop cleanly for run %s", run_id)

    return ExecutionResult(run_id=run_id, status=final_status)


def _wait_for_terminal_state(run_id, config, poll_interval_s) -> str:
    """Poll runs.status until terminal. Uses poll_interval / 2 for responsiveness."""
    conn = get_connection(config)
    try:
        while True:
            status = conn.execute(
                "SELECT status FROM runs WHERE run_id = %s", (run_id,)
            ).scalar()
            if status in ("SUCCESS", "FAILED", "ABORTED", "PAUSED_FOR_DECISION"):
                return status
            time.sleep(poll_interval_s / 2)
    finally:
        conn.close()
```

**Terminal states that stop the runner:**

| `runs.status` | Meaning |
|---|---|
| `SUCCESS` | All atomic tasks completed successfully |
| `PAUSED_FOR_DECISION` | Stuck — no READY tasks, blocked tasks exist, escalation open |
| `FAILED` | Human chose "abort" during escalation resolution |
| `ABORTED` | External signal (timeout, process kill) |

---

## 3. Thread Model

```
Main Thread
│  materialize_task_runs()
│  spawn threads
│  _wait_for_terminal_state()  ← polls every poll_interval/2
│  stop_event.set() + join
│
├─ WorkerThread ──────────────────────────────────────────────┐
│    conn = get_connection()                                  │
│    worker_id = f"{hostname()}-{thread_id()}"               │
│    while not stop_event:                                    │
│        check run status (abort guard)                       │
│        task = pickup_task()                                 │
│        if task:                                             │
│            heartbeat = HeartbeatThread(conn_factory, ...)   │
│            heartbeat.start()                                │
│            result = agent.run(...)                          │
│            heartbeat.stop()                                 │
│            execute_and_promote(task, result)                │
│        else:                                                │
│            sleep(min(poll_interval, 1.0))                   │
│                                                             │
└─ BackgroundThread ──────────────────────────────────────────┘
     conn = get_connection()
     while not stop_event:
         with transaction:
             recover_dead_tasks()    # Job D — clean first
             mark_ready_tasks()      # Job A — forward progress
             check_completion()      # Job E — termination detect
         stop_event.wait(poll_interval_s)
```

**Thread isolation rule**: each thread creates its own connection from the pool. `HeartbeatThread` receives a `conn_factory` (not a connection), creating and closing a short-lived connection each tick.

---

## 4. State Model

### 4.1 Task-Level State Machine

```
PENDING ──► READY ──► RUNNING ──► SUCCESS          (terminal)
                          │
                          ├──► FAILED_RETRYABLE ──► PENDING  (new attempt row)
                          │       (retry_count < max)
                          │
                          └──► FAILED_FINAL         (terminal)
                                    │
                                    ▼  [propagate_failure BFS]
                              BLOCKED_BY_FAILURE
                                    │
                              [human resolution]
                                    │
                          ┌─────────┴─────────┐
                          │ skip              │ retry (dep)
                          ▼                   ▼
                       SKIPPED            PENDING
                    (terminal)         (re-evaluated by
                                        mark_ready_tasks)

SKIPPED / ABORTED — also terminal
```

**State semantics:**

| Status | Meaning |
|---|---|
| `PENDING` | Created, deps not yet met (or retry_at in future) |
| `READY` | Deps satisfied, waiting for worker pickup |
| `RUNNING` | Worker executing; heartbeat required |
| `SUCCESS` | Completed, output promoted to artifact |
| `FAILED_RETRYABLE` | Failed, retry attempt created (old row immutable) |
| `FAILED_FINAL` | Exhausted retries or non-retryable error |
| `BLOCKED_BY_FAILURE` | Upstream dependency is FAILED_FINAL; frozen |
| `SKIPPED` | Human decided to skip (treated as non-blocking by downstream) |
| `ABORTED` | Run aborted externally |

**Terminal states**: `SUCCESS`, `FAILED_FINAL`, `SKIPPED`, `ABORTED`

**Note on `FAILED` enum value**: The existing schema has a plain `FAILED` status. This spec replaces it with `FAILED_RETRYABLE` (transient, retry created) and `FAILED_FINAL` (terminal, exhausted). `FAILED` is deprecated — no new code should write it. A migration step should `UPDATE task_runs SET status = 'FAILED_FINAL' WHERE status = 'FAILED'` before deploying this runner.

### 4.2 Run-Level State Machine

```
RUNNING_PHASE_3 ──► RUNNING_EXECUTION
                            │
              ┌─────────────┼──────────────┐
              │             │              │
         all SUCCESS    stuck (no        external
                        READY tasks,     signal
                        BLOCKED exist)
              │             │              │
              ▼             ▼              ▼
           SUCCESS   PAUSED_FOR_DECISION  FAILED/ABORTED
                            │
                     [human resolves]
                            │
                    ┌───────┴───────┐
                    │               │
              retry/skip           abort
                    │               │
             RUNNING_EXECUTION    FAILED
```

### 4.3 Retry Policy

| `error_type` | `max_retries` | `retry_delay` |
|---|---|---|
| `EXECUTION_ERROR` | 2 | 0s (immediate) |
| `ENVIRONMENT_ERROR` | 3 | 5s |
| `SPEC_AMBIGUITY` | 0 | — escalate only |
| `SPEC_CONTRADICTION` | 0 | — escalate + invalidate spec |
| `UNKNOWN` | 1 | 0s |

Retry creates a **new task_run row** with `attempt_number + 1`, `previous_attempt_id` linked, `retry_at = now() + delay`. The failed row becomes `FAILED_RETRYABLE` (immutable — audit trail).

**`create_retry()` data contract**: The new row must have `retry_count = previous_row.retry_count + 1` **for automatic retries** (called from `_handle_failure`). For human-initiated retries (called from `resolve_escalation`), the new row must have `retry_count = 0` — human override resets the counter, allowing the task a fresh set of automatic retries. The `create_retry()` function must accept a `reset_retry_count: bool = False` parameter to distinguish the two cases.

---

## 5. Materializer

```python
# src/ai_dev_system/engine/materializer.py

def materialize_task_runs(conn, run_id, graph_artifact_id, config):
    """Load approved graph → create task_runs. Safe to call multiple times."""

    # Load graph from promoted artifact path (before transaction)
    artifact = artifact_repo.get(conn, graph_artifact_id)
    graph_path = os.path.join(artifact["content_ref"], "task_graph.json")
    with open(graph_path) as f:
        graph = json.load(f)

    atomic_tasks = [t for t in graph["tasks"]
                    if t["execution_type"] == "atomic"]

    with conn.transaction():
        # Idempotency guard INSIDE transaction (prevents TOCTOU race)
        existing = conn.execute("""
            SELECT COUNT(*) FROM task_runs
            WHERE run_id = %s AND task_graph_artifact_id = %s
            FOR UPDATE  -- lock the run row to serialize concurrent callers
        """, (run_id, graph_artifact_id)).scalar()
        if existing > 0:
            return  # Already materialized — safe, skip duplicate PHASE_STARTED too

        for task in atomic_tasks:
            conn.execute("""
                INSERT INTO task_runs (
                    task_run_id, run_id, task_id,
                    task_graph_artifact_id,
                    attempt_number, status,
                    resolved_dependencies,
                    retry_count, max_retries,
                    agent_routing_key,
                    context_snapshot,
                    materialized_at
                ) VALUES (
                    gen_random_uuid(), %s, %s,
                    %s,
                    1, 'PENDING',
                    %s,
                    0, %s,
                    %s,
                    %s,
                    now()
                )
                ON CONFLICT (run_id, task_id, attempt_number) DO NOTHING
            """, (
                run_id,
                task["id"],
                graph_artifact_id,
                task["deps"],                           # resolved_dependencies
                _max_retries_for(task),                 # from retry policy
                task["agent_type"],                     # routing key
                json.dumps(_build_context(task)),       # immutable snapshot
            ))

        conn.execute("""
            UPDATE runs
            SET status = 'RUNNING_EXECUTION', last_activity_at = now()
            WHERE run_id = %s AND status IN ('CREATED', 'RUNNING_PHASE_3')
        """, (run_id,))

        event_repo.insert(conn, run_id, "PHASE_STARTED", "system",
                          payload={"phase": "execution",
                                   "task_count": len(atomic_tasks)})


def _build_context(task: dict) -> dict:
    """Immutable snapshot stored at materialization time.
    required_inputs resolved to artifact paths at agent dispatch time
    (artifact paths only known after upstream tasks complete).
    """
    return {
        "task_id": task["id"],
        "phase": task["phase"],
        "type": task["type"],
        "agent_type": task["agent_type"],
        "objective": task["objective"],
        "description": task["description"],
        "done_definition": task["done_definition"],
        "verification_steps": list(task["verification_steps"]),
        "required_inputs": list(task["required_inputs"]),   # logical names
        "expected_outputs": list(task["expected_outputs"]),
    }
```

**DB invariant**: `UNIQUE (run_id, task_id, attempt_number)` on `task_runs` ensures `INSERT ON CONFLICT DO NOTHING` is race-safe. Two concurrent callers: one inserts, one silently skips.

**Artifact path resolution**: `required_inputs` in `context_snapshot` stores logical names (e.g., `"solution_design.md"`). At agent dispatch, the worker resolves these to real artifact paths from `runs.current_artifacts`. This is necessary because upstream artifact IDs are not known at materialization time.

---

## 6. Background Jobs

### 6.1 Background Loop

```python
# src/ai_dev_system/engine/background.py

def background_loop(run_id, config, stop_event):
    conn = get_connection(config)
    try:
        while not stop_event.is_set():
            with conn.transaction():
                recover_dead_tasks(conn, run_id, config)   # Job D — clean first
                mark_ready_tasks(conn, run_id)             # Job A — forward progress
                check_completion(conn, run_id)             # Job E — termination detect
            stop_event.wait(timeout=config.poll_interval_s)
    finally:
        conn.close()
```

All 3 jobs run inside **one transaction per cycle** — state is always consistent after each iteration.

### 6.2 Job D — Recover Dead Tasks

```python
def recover_dead_tasks(conn, run_id, config):
    """Detect RUNNING tasks with stale heartbeat → mark failed / create retry."""
    stale = conn.execute("""
        SELECT task_run_id, task_id, attempt_number, retry_count,
               error_type, worker_id
        FROM task_runs
        WHERE run_id = %s
          AND status = 'RUNNING'
          AND worker_id IS NOT NULL
          AND heartbeat_at < now() - interval '%s seconds'
        FOR UPDATE SKIP LOCKED
    """, (run_id, config.heartbeat_timeout_s)).fetchall()

    for task in stale:
        if task.retry_count < config.retry_policy["ENVIRONMENT_ERROR"]["max_retries"]:
            task_run_repo.mark_failed_retryable(
                conn, task.task_run_id, "ENVIRONMENT_ERROR", "worker_heartbeat_timeout"
            )
            task_run_repo.create_retry(
                conn, run_id, task,
                error_type="ENVIRONMENT_ERROR",
                retry_delay_s=config.retry_policy["ENVIRONMENT_ERROR"]["retry_delay_s"],
            )
        else:
            task_run_repo.mark_failed_final(
                conn, task.task_run_id, "ENVIRONMENT_ERROR", "worker_heartbeat_timeout"
            )
            propagate_failure(conn, run_id,
                              failed_task_id=task.task_id,
                              failed_task_run_id=task.task_run_id)
```

**Race safety**: `FOR UPDATE SKIP LOCKED` prevents two background cycles from double-recovering the same task. A worker updating its heartbeat concurrently holds a row lock — background skips it safely.

### 6.3 Job A — Mark Ready Tasks

```python
def mark_ready_tasks(conn, run_id):
    """PENDING tasks whose deps are all SUCCESS/SKIPPED → READY (single atomic UPDATE)."""
    rows = conn.execute("""
        UPDATE task_runs t
        SET status = 'READY'
        WHERE t.run_id = %s
          AND t.status = 'PENDING'
          AND (t.retry_at IS NULL OR t.retry_at <= now())
          AND NOT EXISTS (
              SELECT 1 FROM task_runs dep
              WHERE dep.run_id = t.run_id
                AND dep.task_id = ANY(t.resolved_dependencies)
                AND dep.status NOT IN ('SUCCESS', 'SKIPPED')
          )
        RETURNING task_run_id
    """, (run_id,)).fetchall()

    for row in rows:
        event_repo.insert(conn, run_id, "TASK_READY", "system",
                          task_run_id=row.task_run_id)
```

Single `UPDATE ... RETURNING` — no separate SELECT. Race-safe: if two cycles run concurrently (shouldn't happen, but defensive), the second UPDATE finds `status != 'PENDING'` and touches nothing.

### 6.4 Job E — Check Completion

```python
def check_completion(conn, run_id):
    """Detect run completion or stuck state."""
    counts = conn.execute("""
        SELECT
            COUNT(*) FILTER (WHERE status = 'SUCCESS')           AS success_count,
            COUNT(*) FILTER (WHERE status = 'FAILED_FINAL')      AS failed_final_count,
            COUNT(*) FILTER (WHERE status = 'BLOCKED_BY_FAILURE')AS blocked_count,
            COUNT(*) FILTER (WHERE status = 'READY')             AS ready_count,
            COUNT(*) FILTER (WHERE status = 'RUNNING')           AS running_count,
            COUNT(*) FILTER (WHERE status = 'PENDING')           AS pending_count
        FROM task_runs
        WHERE run_id = %s
        -- No execution_type filter: materializer inserts ONLY atomic tasks;
        -- composite structural nodes are never persisted to task_runs.
    """, (run_id,)).fetchone()

    active_count = (counts.ready_count + counts.running_count + counts.pending_count)

    # SUCCESS: nothing active, no failures, no blocked orphans
    if (active_count == 0
            and counts.failed_final_count == 0
            and counts.blocked_count == 0):
        conn.execute("""
            UPDATE runs SET status = 'SUCCESS', completed_at = now()
            WHERE run_id = %s AND status = 'RUNNING_EXECUTION'
        """, (run_id,))
        event_repo.insert(conn, run_id, "RUN_COMPLETED", "system",
                          payload={"outcome": "SUCCESS"})

    # PAUSED: nothing can run (no active tasks), but failures exist
    # Covers both: blocked tasks exist, AND leaf-task failure (no blocked downstream)
    elif (active_count == 0
          and counts.failed_final_count > 0
          and counts.running_count == 0
          and counts.ready_count == 0):
        conn.execute("""
            UPDATE runs SET status = 'PAUSED_FOR_DECISION'
            WHERE run_id = %s AND status = 'RUNNING_EXECUTION'
        """, (run_id,))
        # Escalation was raised by propagate_failure — no duplicate here

    # Defensive guard: blocked tasks with no failed tasks (should be impossible,
    # but catches state inconsistency from bugs in unblock logic)
    elif active_count == 0 and counts.blocked_count > 0 and counts.failed_final_count == 0:
        log.error(
            "run %s: inconsistent state — %d BLOCKED tasks but 0 FAILED_FINAL. "
            "Forcing PAUSED_FOR_DECISION for human review.",
            run_id, counts.blocked_count
        )
        conn.execute("""
            UPDATE runs SET status = 'PAUSED_FOR_DECISION'
            WHERE run_id = %s AND status = 'RUNNING_EXECUTION'
        """, (run_id,))
```

**Completion conditions:**

| Condition | Run outcome |
|---|---|
| `active_count == 0` AND `failed_final_count == 0` AND `blocked_count == 0` | `SUCCESS` |
| `active_count == 0` AND `failed_final_count > 0` AND nothing running/ready | `PAUSED_FOR_DECISION` |

> Leaf task failure (no downstream blocked tasks) is also covered by the second branch — the run pauses and the escalation raised by `propagate_failure` allows human resolution.

---

## 7. Worker Loop

### 7.1 Worker Loop

```python
# src/ai_dev_system/engine/worker.py  (extend existing)

def worker_loop(run_id, config, agent, stop_event):
    conn = get_connection(config)
    worker_id = f"{hostname()}-{threading.get_ident()}"
    try:
        while not stop_event.is_set():
            # Abort guard at loop head
            run_status = run_repo.get_status(conn, run_id)
            if run_status in ("ABORTED", "FAILED", "SUCCESS"):
                break

            task = pickup_task(conn, config, run_id, worker_id)
            if task is None:
                stop_event.wait(timeout=min(config.poll_interval_s, 1.0))
                continue

            heartbeat = HeartbeatThread(
                conn_factory=lambda: get_connection(config),
                task_run_id=task["task_run_id"],
                interval_s=config.heartbeat_interval_s,
            )
            heartbeat.start()
            try:
                # Resolve artifact paths for required_inputs before calling agent
                try:
                    context = _resolve_artifact_paths(conn, run_id, task["context_snapshot"])
                except ArtifactResolutionError as e:
                    # Treat as EXECUTION_ERROR — enters normal retry/escalation path
                    result = AgentResult(success=False, error=str(e))
                else:
                    result = agent.run(
                        task_id=task["task_id"],
                        output_path=task["temp_path"],
                        context=copy.deepcopy(context),   # immutable copy for agent
                        timeout_s=config.task_timeout_s,
                    )
            except TimeoutError:
                result = AgentResult(success=False, error="task_execution_timeout")
            finally:
                heartbeat.stop()

            execute_and_promote(conn, config, task, result, worker_id, run_id)
    finally:
        conn.close()
```

### 7.2 Pickup — with Double-Check

```python
def pickup_task(conn, config, run_id, worker_id) -> Optional[dict]:
    with conn.transaction():
        task = conn.execute("""
            SELECT tr.*
            FROM task_runs tr
            WHERE tr.run_id = %s
              AND tr.status = 'READY'
              -- Double-check deps at pickup (guard against READY ↔ BLOCKED race)
              AND NOT EXISTS (
                  SELECT 1 FROM task_runs dep
                  WHERE dep.run_id = tr.run_id
                    AND dep.task_id = ANY(tr.resolved_dependencies)
                    AND dep.status NOT IN ('SUCCESS', 'SKIPPED')
              )
            ORDER BY tr.retry_count ASC, tr.created_at ASC   -- fairness: prefer fresh tasks
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        """, (run_id,)).fetchone()

        if task is None:
            return None

        # Check run not aborted between query and lock
        run_status = conn.execute(
            "SELECT status FROM runs WHERE run_id = %s", (run_id,)
        ).scalar()
        if run_status not in ("RUNNING_EXECUTION",):
            return None  # rollback naturally, don't pick up

        temp_path = build_temp_path(
            config.storage_root, run_id, task["task_id"], task["attempt_number"]
        )
        os.makedirs(temp_path, exist_ok=True)

        conn.execute("""
            UPDATE task_runs
            SET status = 'RUNNING',
                worker_id = %s,
                locked_at = now(),
                heartbeat_at = now(),
                started_at = now()
            WHERE task_run_id = %s
        """, (worker_id, task["task_run_id"]))

        event_repo.insert(conn, run_id, "TASK_STARTED",
                          f"worker:{worker_id}",
                          task_run_id=task["task_run_id"])

        return {**dict(task), "temp_path": temp_path, "worker_id": worker_id}
```

### 7.3 Execute and Promote

```python
def execute_and_promote(conn, config, task, result, worker_id, run_id):
    # Abort guard: check before promoting (agent may have run during abort)
    run_status = run_repo.get_status(conn, run_id)
    if run_status in ("ABORTED", "FAILED"):  # FAILED = human chose abort via resolve_escalation
        with conn.transaction():
            conn.execute("""
                UPDATE task_runs SET status = 'ABORTED'
                WHERE task_run_id = %s AND status = 'RUNNING'
            """, (task["task_run_id"],))
        return

    if not result.success:
        _handle_failure(conn, config, task, result.error,
                        worker_id, run_id, error_type="EXECUTION_ERROR")
        return

    try:
        # promote_output is idempotent (7-step protocol with FOR UPDATE guard)
        artifact_id = promote_output(
            conn, config, task,
            PromotedOutput(
                name=task["task_id"],
                artifact_type="EXECUTION_OUTPUT",
                description=f"Output of {task['task_id']}",
            ),
            temp_path=task["temp_path"],
        )
        with conn.transaction():
            rows_updated = conn.execute("""
                UPDATE task_runs
                SET status = 'SUCCESS',
                    completed_at = now(),
                    output_artifact_id = %s
                WHERE task_run_id = %s AND status = 'RUNNING'   -- idempotency guard
            """, (artifact_id, task["task_run_id"])).rowcount

            if rows_updated == 1:
                event_repo.insert(conn, run_id, "TASK_COMPLETED",
                                  f"worker:{worker_id}",
                                  task_run_id=task["task_run_id"])
    except Exception as e:
        _handle_failure(conn, config, task, str(e),
                        worker_id, run_id, error_type="ENVIRONMENT_ERROR")
```

### 7.4 Failure Handler

```python
# src/ai_dev_system/engine/failure.py
# (imported by worker.py: from ai_dev_system.engine.failure import _handle_failure)

def _handle_failure(conn, config, task, error, worker_id, run_id, error_type):
    retry_cfg = config.retry_policy[error_type]
    can_retry = task["retry_count"] < retry_cfg["max_retries"]

    with conn.transaction():
        if can_retry:
            conn.execute("""
                UPDATE task_runs
                SET status = 'FAILED_RETRYABLE',
                    error_type = %s, error_detail = %s, completed_at = now()
                WHERE task_run_id = %s AND status = 'RUNNING'
            """, (error_type, error, task["task_run_id"]))

            task_run_repo.create_retry(
                conn, run_id, task,
                error_type=error_type,
                retry_delay_s=retry_cfg.get("retry_delay_s", 0),
            )
            event_repo.insert(conn, run_id, "TASK_RETRYING",
                              f"worker:{worker_id}",
                              task_run_id=task["task_run_id"])
        else:
            conn.execute("""
                UPDATE task_runs
                SET status = 'FAILED_FINAL',
                    error_type = %s, error_detail = %s, completed_at = now()
                WHERE task_run_id = %s AND status = 'RUNNING'
            """, (error_type, error, task["task_run_id"]))

            event_repo.insert(conn, run_id, "TASK_FAILED",
                              f"worker:{worker_id}",
                              task_run_id=task["task_run_id"])

            propagate_failure(conn, run_id,
                              failed_task_id=task["task_id"],
                              failed_task_run_id=task["task_run_id"])
```

---

## 8. Heartbeat Thread

```python
# src/ai_dev_system/engine/heartbeat.py

class HeartbeatThread(threading.Thread):
    """Per-task heartbeat. Lives only while agent is executing.
    Receives conn_factory (not conn) — creates short-lived connection each tick.
    """
    def __init__(self, conn_factory, task_run_id, interval_s=30):
        super().__init__(daemon=True, name=f"hb-{task_run_id[:8]}")
        self._stop = threading.Event()
        self.conn_factory = conn_factory
        self.task_run_id = task_run_id
        self.interval_s = interval_s

    def run(self):
        while not self._stop.wait(self.interval_s):
            conn = self.conn_factory()
            try:
                conn.execute("""
                    UPDATE task_runs SET heartbeat_at = now()
                    WHERE task_run_id = %s AND status = 'RUNNING'
                """, (self.task_run_id,))
                conn.commit()
            except Exception:
                pass   # Non-fatal — dead worker recovery (Job D) handles missed heartbeats
            finally:
                conn.close()

    def stop(self):
        self._stop.set()
        self.join(timeout=5)
        if self.is_alive():
            log.warning("HeartbeatThread did not stop cleanly for task %s",
                        self.task_run_id)
```

---

## 9. Failure Propagation

```python
# src/ai_dev_system/engine/failure.py

def propagate_failure(conn, run_id, failed_task_id, failed_task_run_id):
    """BFS: mark all downstream tasks BLOCKED_BY_FAILURE.
    Skips terminal states. Raises escalation (deduplicated).
    Must be called inside a transaction.
    """
    visited = set()
    queue = [failed_task_id]

    while queue:
        current_task_id = queue.pop(0)

        dependents = conn.execute("""
            SELECT task_run_id, task_id, status
            FROM task_runs
            WHERE run_id = %s
              AND %s = ANY(resolved_dependencies)
              AND status NOT IN (
                  'SUCCESS', 'SKIPPED', 'FAILED_FINAL',
                  'FAILED_RETRYABLE', 'ABORTED'
              )
        """, (run_id, current_task_id)).fetchall()

        for dep in dependents:
            if dep.task_id in visited:
                continue
            visited.add(dep.task_id)

            conn.execute("""
                UPDATE task_runs
                SET status = 'BLOCKED_BY_FAILURE',
                    error_detail = %s
                WHERE task_run_id = %s
                  AND status IN ('PENDING', 'READY')   -- never overwrite running/terminal
            """, (f"dependency_failed:{failed_task_id}", dep.task_run_id))

            queue.append(dep.task_id)

    # Raise escalation — UNIQUE constraint deduplicates concurrent calls
    escalation_repo.upsert_open(
        conn, run_id,
        task_run_id=failed_task_run_id,   # exact attempt that failed
        reason="TASK_FAILURE",
        options=["retry", "skip", "abort"],
    )
```

---

## 10. Escalation Resolution

```python
# src/ai_dev_system/engine/escalation.py

def resolve_escalation(conn, escalation_id, resolution, run_id):
    """
    resolution: 'retry' | 'skip' | 'abort'
    Called by human-facing CLI or future UI.
    """
    with conn.transaction():
        esc = conn.execute("""
            SELECT * FROM escalations
            WHERE escalation_id = %s
            FOR UPDATE
        """, (escalation_id,)).fetchone()

        if esc is None or esc["status"] != "OPEN":
            return  # Already resolved — idempotent

        conn.execute("""
            UPDATE escalations
            SET status = 'RESOLVED', resolution = %s, resolved_at = now()
            WHERE escalation_id = %s
        """, (resolution, escalation_id))

        event_repo.insert(conn, run_id, "HUMAN_DECISION_RECORDED", "human",
                          task_run_id=esc["task_run_id"],
                          payload={"resolution": resolution,
                                   "escalation_id": escalation_id})

        task = task_run_repo.get(conn, esc["task_run_id"])

        if resolution == "retry":
            # Create new attempt for the failed task.
            # reset_retry_count=True: human override resets counter for fresh automatic retries.
            task_run_repo.create_retry(conn, run_id, task, error_type=None,
                                       reset_retry_count=True)
            # Unblock direct downstream (BFS via mark_ready_tasks will cascade)
            _unblock_downstream_bfs(conn, run_id, task["task_id"])

        elif resolution == "skip":
            conn.execute("""
                UPDATE task_runs SET status = 'SKIPPED'
                WHERE task_run_id = %s AND status = 'FAILED_FINAL'
            """, (esc["task_run_id"],))
            # BFS unblock all downstream BLOCKED_BY_FAILURE
            _unblock_downstream_bfs(conn, run_id, task["task_id"])

        elif resolution == "abort":
            conn.execute("""
                UPDATE task_runs SET status = 'ABORTED'
                WHERE run_id = %s
                  AND status NOT IN ('SUCCESS', 'FAILED_FINAL', 'SKIPPED', 'ABORTED')
            """, (run_id,))
            conn.execute("""
                UPDATE runs SET status = 'FAILED', completed_at = now()
                WHERE run_id = %s
            """, (run_id,))
            event_repo.insert(conn, run_id, "RUN_ABORTED", "human",
                              payload={"reason": "human_abort_on_escalation"})
            return  # Don't resume

        # Resume run (for retry/skip paths)
        conn.execute("""
            UPDATE runs SET status = 'RUNNING_EXECUTION'
            WHERE run_id = %s AND status = 'PAUSED_FOR_DECISION'
        """, (run_id,))


def _unblock_downstream_bfs(conn, run_id, unblocked_task_id):
    """BFS: move BLOCKED_BY_FAILURE → PENDING for tasks downstream of unblocked_task_id,
    BUT ONLY if the task has no other FAILED_FINAL dependencies remaining.
    (If other deps are still FAILED_FINAL, the task stays BLOCKED until those are resolved.)
    mark_ready_tasks() will then evaluate which PENDING tasks can become READY.
    """
    visited = set()
    queue = [unblocked_task_id]

    while queue:
        current = queue.pop(0)
        blocked = conn.execute("""
            SELECT task_run_id, task_id
            FROM task_runs
            WHERE run_id = %s
              AND %s = ANY(resolved_dependencies)
              AND status = 'BLOCKED_BY_FAILURE'
        """, (run_id, current)).fetchall()

        for dep in blocked:
            if dep.task_id in visited:
                continue
            visited.add(dep.task_id)

            # Only unblock if ALL remaining deps are non-FAILED_FINAL
            # (task may have multiple upstream failures; resolve one at a time)
            rows_updated = conn.execute("""
                UPDATE task_runs SET status = 'PENDING', error_detail = NULL
                WHERE task_run_id = %s
                  AND status = 'BLOCKED_BY_FAILURE'
                  AND NOT EXISTS (
                      SELECT 1 FROM task_runs other_dep
                      WHERE other_dep.run_id = %s
                        AND other_dep.task_id = ANY(
                            (SELECT resolved_dependencies FROM task_runs
                             WHERE task_run_id = %s)
                        )
                        AND other_dep.status = 'FAILED_FINAL'
                  )
            """, (dep.task_run_id, run_id, dep.task_run_id)).rowcount

            if rows_updated > 0:
                queue.append(dep.task_id)
            # If rows_updated == 0: task still has FAILED_FINAL deps → stays BLOCKED, don't recurse
```

---

## 11. DB Schema Changes

Minimal additions to existing schema (`docs/schema/control-layer-schema.sql`):

```sql
-- 1. New run statuses
ALTER TYPE run_status ADD VALUE IF NOT EXISTS 'RUNNING_EXECUTION';
ALTER TYPE run_status ADD VALUE IF NOT EXISTS 'PAUSED_FOR_DECISION';

-- 2. New task_run statuses
ALTER TYPE task_run_status ADD VALUE IF NOT EXISTS 'FAILED_RETRYABLE';
ALTER TYPE task_run_status ADD VALUE IF NOT EXISTS 'FAILED_FINAL';
ALTER TYPE task_run_status ADD VALUE IF NOT EXISTS 'BLOCKED_BY_FAILURE';

-- 3. New columns on task_runs
ALTER TABLE task_runs
    ADD COLUMN IF NOT EXISTS retry_count        INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS retry_at           TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS agent_routing_key  TEXT,
    ADD COLUMN IF NOT EXISTS context_snapshot   JSONB,
    ADD COLUMN IF NOT EXISTS materialized_at    TIMESTAMPTZ;

-- Idempotency constraint for materializer
ALTER TABLE task_runs
    ADD CONSTRAINT uq_task_runs_attempt
    UNIQUE (run_id, task_id, attempt_number);

-- 4. Escalations table
CREATE TABLE IF NOT EXISTS escalations (
    escalation_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          UUID NOT NULL REFERENCES runs(run_id),
    task_run_id     UUID NOT NULL REFERENCES task_runs(task_run_id),
    status          TEXT NOT NULL DEFAULT 'OPEN'
                        CHECK (status IN ('OPEN', 'RESOLVED')),
    reason          TEXT NOT NULL,
    options         JSONB NOT NULL,
    resolution      TEXT,
    resolved_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- One open escalation per (run, task_run, reason)
    -- Requires PostgreSQL 15+ for NULLS NOT DISTINCT syntax.
    -- (Project dependency: PostgreSQL 15+ is confirmed in schema/control-layer-schema.sql)
    UNIQUE NULLS NOT DISTINCT (run_id, task_run_id, reason, status)
);

CREATE INDEX IF NOT EXISTS idx_escalations_run_open
    ON escalations (run_id) WHERE status = 'OPEN';
```

---

## 12. Agent Contract

```python
# src/ai_dev_system/agents/base.py  (already exists — shown for clarity)

class Agent(Protocol):
    def run(
        self,
        task_id: str,
        output_path: str,       # Dir pre-created by worker; agent writes files here
        context: dict,          # Fully resolved snapshot (see _build_context + path resolution)
        timeout_s: float,       # Worker enforces; agent should also respect
    ) -> AgentResult:
        ...

@dataclass
class AgentResult:
    success: bool
    error: Optional[str] = None


# Context dict structure (agent receives this — read-only):
# {
#   "task_id": "TASK-IMPL.BACKEND",
#   "phase": "implement",
#   "type": "coding",
#   "agent_type": "Backend Developer",
#   "objective": "...",
#   "description": "...",
#   "done_definition": "...",
#   "verification_steps": [...],
#   "required_inputs": [
#       {"name": "solution_design.md", "artifact_id": "uuid", "path": "/data/runs/.../v1/solution_design.md"},
#   ],
#   "expected_outputs": ["backend source code", "tests/test_api.py"],
# }
```

---

## 12b. Artifact Path Resolution Contract

`_resolve_artifact_paths` is called in the worker loop before agent dispatch. It enriches the `context_snapshot` with real filesystem paths for each required input.

```python
# src/ai_dev_system/engine/materializer.py

def _resolve_artifact_paths(conn, run_id, context_snapshot: dict) -> dict:
    """Resolve logical required_inputs names to artifact paths from runs.current_artifacts.

    Args:
        conn:             DB connection (read-only, no transaction needed — READ COMMITTED safe)
        run_id:           UUID of the run
        context_snapshot: immutable snapshot from task_runs.context_snapshot

    Returns:
        Copy of context_snapshot with required_inputs enriched:
        [{"name": "solution_design.md", "artifact_id": "uuid", "path": "/data/.../v1/..."}]

    Raises:
        ArtifactResolutionError: if a required input cannot be resolved (upstream task
        did not complete successfully or artifact was not promoted to current_artifacts).
        This is NOT a retryable error — it indicates a graph dependency misconfiguration.
    """
    current = conn.execute("""
        SELECT current_artifacts FROM runs WHERE run_id = %s
    """, (run_id,)).scalar()

    resolved = []
    for logical_name in context_snapshot.get("required_inputs", []):
        # Try to match logical name to a known artifact type key
        artifact_id = _match_artifact(logical_name, current)
        if artifact_id is None:
            # Input not yet available (upstream task may not have run)
            raise ArtifactResolutionError(
                f"Required input '{logical_name}' not found in current_artifacts for run {run_id}. "
                f"Dependency graph may be incorrect or upstream task did not complete."
            )
        artifact = artifact_repo.get(conn, artifact_id)
        resolved.append({
            "name": logical_name,
            "artifact_id": artifact_id,
            "path": artifact["content_ref"],
        })

    ctx = copy.deepcopy(context_snapshot)
    ctx["required_inputs"] = resolved
    return ctx


def _match_artifact(logical_name: str, current_artifacts: dict) -> Optional[str]:
    """Map a logical input name to an artifact_id from current_artifacts.
    v1: simple keyword matching against artifact type keys.
    v2: explicit mapping table in task graph spec.
    """
    name_lower = logical_name.lower()
    for key, artifact_id in current_artifacts.items():
        if artifact_id and key.replace("_id", "").replace("_", "") in name_lower:
            return artifact_id
    return None
```

**Failure mode**: If `ArtifactResolutionError` is raised, the worker treats it as `EXECUTION_ERROR` and applies the retry policy. After retries exhaust, task becomes `FAILED_FINAL` and `propagate_failure` runs. This covers the case where an upstream task completed but did not promote its output correctly.

---

## 13. File Structure

```
src/ai_dev_system/
    engine/
        runner.py        # run_execution() — entry point, thread orchestration
        materializer.py  # materialize_task_runs(), _build_context(), _resolve_artifact_paths()
        background.py    # background_loop(), mark_ready_tasks(), recover_dead_tasks(), check_completion()
        worker.py        # worker_loop(), pickup_task(), execute_and_promote() [extend existing]
        heartbeat.py     # HeartbeatThread (conn_factory pattern)
        failure.py       # propagate_failure(), _handle_failure()
        escalation.py    # resolve_escalation(), _unblock_downstream_bfs()
    db/repos/
        escalations.py   # EscalationRepo: upsert_open(), get_and_lock(), mark_resolved()
        task_runs.py     # Extend: create_retry(), mark_failed_final(), mark_failed_retryable()

tests/
    unit/
        test_materializer.py       # graph → task_runs, idempotency, ON CONFLICT guard
        test_background_jobs.py    # mark_ready, recover_dead, check_completion, retry_at filter
        test_failure.py            # propagate_failure BFS, escalation dedup, terminal state skip
        test_heartbeat.py          # conn_factory pattern, stop race
        test_escalation.py         # retry/skip/abort resolution, BFS unblock
    integration/
        test_runner_golden.py      # full happy path (see golden run below)
        test_runner_escalation.py  # failure → escalation → skip/retry
        test_runner_abort.py       # mid-run abort, worker promotion guard
```

---

## 14. Golden Run Test Scenarios

### Scenario A — Happy Path

```
Graph: PARSE → DESIGN → [IMPL.BACKEND ‖ IMPL.FRONTEND] → VALIDATE
       (5 atomic tasks, IMPL split parallel)

T=0   materialize: 5 PENDING task_runs
T=1   bg: mark_ready → PARSE READY
T=2   worker: pickup PARSE → RUNNING; heartbeat starts
T=5   PARSE → SUCCESS; artifact promoted
T=6   bg: mark_ready → DESIGN READY
T=7   worker: pickup DESIGN → RUNNING → SUCCESS
T=8   bg: mark_ready → IMPL.BACKEND + IMPL.FRONTEND both READY
T=9   worker: pickup IMPL.BACKEND → RUNNING → SUCCESS
T=10  worker: pickup IMPL.FRONTEND → RUNNING → SUCCESS
T=11  bg: mark_ready → VALIDATE READY
T=12  worker: pickup VALIDATE → RUNNING → SUCCESS
T=13  bg: check_completion → active=0, failed=0 → run SUCCESS ✅

Verify:
- 5 task_runs all SUCCESS
- 5 artifacts promoted
- runs.status = SUCCESS
- events: 5x TASK_STARTED, 5x TASK_COMPLETED, RUN_COMPLETED
```

### Scenario B — Retry then Success

```
DESIGN fails attempt 1 (EXECUTION_ERROR), succeeds attempt 2

T=7   DESIGN attempt 1 → RUNNING → FAILED_RETRYABLE
      new attempt 2 created (retry_count=1, status=PENDING)
T=8   bg: mark_ready → attempt 2 READY
T=9   worker: pickup attempt 2 → SUCCESS

Verify:
- attempt 1: FAILED_RETRYABLE; attempt 2: SUCCESS
- attempt_number chain: 1 → 2 via previous_attempt_id
```

### Scenario C — Failure → Escalation → Skip (C-mode behavior)

```
IMPL.BACKEND fails 3 times (FAILED_FINAL)

Step 1: propagate_failure
  IMPL.BACKEND → FAILED_FINAL
  VALIDATE → BLOCKED_BY_FAILURE (depends on both IMPL tasks)
  IMPL.FRONTEND still READY → runs independently → SUCCESS

Step 2: check_completion
  running=0, ready=0, blocked=1 → PAUSED_FOR_DECISION

Step 3: human calls resolve_escalation(resolution="skip")
  IMPL.BACKEND → SKIPPED
  _unblock_downstream_bfs → VALIDATE → PENDING
  bg: mark_ready → VALIDATE READY (IMPL.FRONTEND already SUCCESS, IMPL.BACKEND SKIPPED)
  worker: VALIDATE → SUCCESS
  run → SUCCESS

Verify:
- IMPL.BACKEND: SKIPPED; IMPL.FRONTEND: SUCCESS; VALIDATE: SUCCESS
- run: SUCCESS (not FAILED)
- escalation record: RESOLVED, resolution="skip"
- events: ESCALATION_RAISED, HUMAN_DECISION_RECORDED, RUN_COMPLETED
```

---

## 15. What This Does NOT Include

- **Multi-worker / distributed mode** — single worker thread per runner process in v1. Horizontal scaling is v2 (replace thread with process + job queue).
- **Real agent implementations** — runner uses stub agents in all tests. Actual LLM/tool-using agents are separate concern.
- **Escalation UI** — CLI hook only (`resolve_escalation()` is callable but no interactive shell in this spec). Future: web UI or CLI command.
- **Spec invalidation on SPEC_CONTRADICTION** — error type handled by retry policy (0 retries → escalation), but invalidating SPEC_BUNDLE artifact and routing back to Gate 1 is out of scope for v1.
- **Parallel workers** — single WorkerThread processes tasks sequentially. IMPL.BACKEND and IMPL.FRONTEND in Scenario A are picked up one after the other, not truly simultaneously. Parallelism within a run is v2.
- **Task timeout enforcement** — `timeout_s` is passed to agent but enforcement (SIGALRM / threading.Timer) is agent responsibility in v1.

---

## 16. Success Criteria

1. `run_execution(run_id, graph_artifact_id)` completes a 5-task graph from PENDING → all SUCCESS
2. Dead worker detection: task stuck at RUNNING → recovered within 2× heartbeat_timeout
3. Retry: EXECUTION_ERROR → new attempt created, old row FAILED_RETRYABLE (immutable)
4. Failure propagation: FAILED_FINAL → downstream BLOCKED_BY_FAILURE (BFS, not just direct children)
5. Escalation: run PAUSED_FOR_DECISION when stuck; `resolve_escalation("skip")` resumes execution
6. Idempotency: calling `materialize_task_runs` twice creates no duplicates (ON CONFLICT guard)
7. Abort guard: worker does not promote output after run enters ABORTED state
8. Thread safety: no shared connections; each thread owns its connection lifecycle
