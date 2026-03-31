# Execution Engine State Machine

Tài liệu này define formal state machine cho run-level và task-level,
cộng với pseudo-code cho 5 job của execution engine.

Schema backing: `control-layer-schema.sql` + `application-invariants.sql`

---

## Quyết định kiến trúc đã chốt

| Quyết định | Lựa chọn | Lý do |
|---|---|---|
| Trigger mechanism | Event-driven sau mỗi transition + 30s polling safety net | Event-driven nhanh; polling tránh stuck state khi event bị sót |
| Idempotency | Status guard trong mỗi UPDATE (`WHERE status = 'EXPECTED'`) | Tránh double transition; unique constraint là lớp bảo vệ thứ hai |
| Transaction boundary | Mỗi transition = 1 transaction | Đã chốt trong application-invariants.sql |
| correlation_id | Retry chain: sinh khi TASK_FAILED đầu tiên, tái dùng cho toàn chain. Gate action: mới. Escalation: mới. | Dễ group event theo một "incident" |
| Worker concurrency | `max_concurrent_tasks_per_run = 4` (default), config qua `runs.metadata` | Tránh quá tải; dễ tune per project |

---

## 1. Run-Level State Machine

### Transition Table

| From | To | Trigger | Guard | Action |
|---|---|---|---|---|
| `CREATED` | `RUNNING_PHASE_1A` | `run_start()` | — | emit `PHASE_STARTED(1A)` |
| `RUNNING_PHASE_1A` | `RUNNING_PHASE_1B` | `INITIAL_BRIEF` → ACTIVE | — | emit `PHASE_COMPLETED(1A)`, emit `PHASE_STARTED(1B)` |
| `RUNNING_PHASE_1B` | `PAUSED_AT_GATE_1` | `DEBATE_REPORT` → ACTIVE | — | emit `PHASE_COMPLETED(1B)`, emit `GATE_ENTERED(1)`, set `paused_at`, `paused_reason` |
| `PAUSED_AT_GATE_1` | `RUNNING_PHASE_1D` | Human approves Gate 1 | `APPROVED_ANSWERS` artifact created | emit `GATE_APPROVED(1)`, emit `PHASE_STARTED(1D)` |
| `PAUSED_AT_GATE_1` | `PAUSED_AT_GATE_1` | Spec conflict trong 1D | conflict detected | emit `ESCALATION_RAISED`, update `paused_reason = "spec_conflict"` |
| `RUNNING_PHASE_1D` | `RUNNING_PHASE_2A` | `SPEC_BUNDLE` → ACTIVE | no conflict | emit `PHASE_COMPLETED(1D)`, emit `PHASE_STARTED(2A)` |
| `RUNNING_PHASE_2A` | `PAUSED_AT_GATE_2` | `TASK_GRAPH_GENERATED` → ACTIVE | — | emit `PHASE_COMPLETED(2A)`, emit `GATE_ENTERED(2)`, set `paused_at` |
| `PAUSED_AT_GATE_2` | `PAUSED_AT_GATE_2` | Human rejects Gate 2 | — | emit `GATE_REJECTED(2)`, trigger `regenerate_task_graph` |
| `PAUSED_AT_GATE_2` | `RUNNING_PHASE_3` | Human approves Gate 2 | `TASK_GRAPH_APPROVED` created | emit `GATE_APPROVED(2)`, create task_run records (all `PENDING`), emit `PHASE_STARTED(3)` |
| `RUNNING_PHASE_3` | `RUNNING_PHASE_3` | Escalation resolved | — | clear `paused_reason`, emit `HUMAN_DECISION_RECORDED` |
| `RUNNING_PHASE_3` | `COMPLETED` | Run State Updater: all tasks SUCCESS | tất cả task_run SUCCESS | emit `PHASE_COMPLETED(3)`, emit `RUN_COMPLETED`, set `completed_at` |
| Any | `PAUSED` (tại phase hiện tại) | `ESCALATION_RAISED` | — | set `paused_at`, `paused_reason` |
| Any | `ABORTED` | `abort()` | — | set all RUNNING → ABORTED, emit `RUN_ABORTED` |

### Invariants của Run State Machine

- `PAUSED_AT_GATE_X`: run không tự chạy tiếp; chỉ transition khi có human action
- `COMPLETED`: terminal state, không transition tiếp
- `ABORTED`: terminal state
- `current_phase` phải update cùng transaction với `status`

---

## 2. Task-Level State Machine

### Transition Table

| From | To | Trigger | Guard | Action |
|---|---|---|---|---|
| `PENDING` | `READY` | Dependency Resolver | all deps `SUCCESS` | snapshot `resolved_dependencies`, emit `TASK_READY` |
| `READY` | `RUNNING` | Worker Pickup | `FOR UPDATE SKIP LOCKED` thành công | set `worker_id`, `locked_at`, `heartbeat_at`, `started_at`; emit `TASK_STARTED` |
| `RUNNING` | `SUCCESS` | Task Executor: task hoàn thành | — | set `completed_at`; promote outputs nếu có; emit `TASK_COMPLETED`; trigger Dependency Resolver + Run State Updater |
| `RUNNING` | `FAILED` | Task Executor: task thất bại | — | set `error_type`, `error_detail`, `completed_at`; emit `TASK_FAILED`; trigger Failure Handler |
| `FAILED` | `READY` (attempt mới) | Failure Handler: retry allowed | `attempt_number < max_retries` | tạo task_run mới, emit `TASK_RETRYING` |
| `FAILED` | (escalation) | Failure Handler: retry exhausted | `attempt_number >= max_retries` | emit `ESCALATION_RAISED`; run → `PAUSED` |
| `RUNNING` | `FAILED` | Dead Worker Recovery | `heartbeat_at` stale | mark FAILED (ENVIRONMENT_ERROR), tạo attempt mới |
| `PENDING`/`READY`/`RUNNING` | `ABORTED` | Run abort | — | set `completed_at`; emit event |
| (escalation) | `SKIPPED` | Human quyết định skip | — | emit `TASK_SKIPPED`; trigger Dependency Resolver (treat như SUCCESS cho dependency check) |

### Retry Policy Mapping

```
error_type          max_retries  same_worker   escalate_action
EXECUTION_ERROR     2            yes (first)   ESCALATION_RAISED
ENVIRONMENT_ERROR   3            no (new)      ESCALATION_RAISED
SPEC_AMBIGUITY      0            —             ESCALATION_RAISED + run pause
SPEC_CONTRADICTION  0            —             ESCALATION_RAISED + invalidate spec + run pause
UNKNOWN             1            yes           ESCALATION_RAISED
```

### Dependency Resolution: SKIPPED tasks

Khi human quyết định skip một task, Dependency Resolver coi SKIPPED = SUCCESS
khi kiểm tra dependencies. Tức là:

```
deps_satisfied = all deps have status IN ('SUCCESS', 'SKIPPED')
```

Không phải mọi task đều nên bị unblock khi dep bị skip — đây là trade-off.
Nếu task bị skip là critical path, downstream task vẫn READY nhưng sẽ thiếu input.
Behavior này phải được document rõ trong escalation UX.

---

## 3. Job Definitions

### Job A — Dependency Resolver

**Trigger**: Sau mỗi task → `SUCCESS` hoặc `SKIPPED`. Fallback: polling mỗi 10s.

```python
def resolve_dependencies(run_id: UUID) -> None:
    pending_tasks = db.query("""
        SELECT task_run_id, resolved_dependencies
        FROM task_runs
        WHERE run_id = $run_id AND status = 'PENDING'
    """, run_id=run_id)

    for task in pending_tasks:
        # Kiểm tra tất cả dependencies đã SUCCESS hoặc SKIPPED
        all_satisfied = db.query_one("""
            SELECT NOT EXISTS (
                SELECT 1 FROM unnest($deps::text[]) AS dep(task_id)
                WHERE NOT EXISTS (
                    SELECT 1 FROM task_runs
                    WHERE run_id = $run_id
                      AND task_id = dep.task_id
                      AND status IN ('SUCCESS', 'SKIPPED')
                )
            )
        """, deps=task.resolved_dependencies, run_id=run_id)

        if all_satisfied:
            with db.transaction():
                updated = db.execute("""
                    UPDATE task_runs
                    SET status = 'READY'
                    WHERE task_run_id = $id AND status = 'PENDING'
                """, id=task.task_run_id)

                if updated.rowcount == 1:  # idempotency: chỉ emit nếu thật sự update
                    db.execute("""
                        INSERT INTO events (run_id, task_run_id, event_type, actor)
                        VALUES ($run_id, $task_run_id, 'TASK_READY', 'system')
                    """, run_id=run_id, task_run_id=task.task_run_id)
```

---

### Job B — Worker Pickup

**Trigger**: Worker loop liên tục. Nếu không có task: backoff ngắn (1–2s).

```python
def worker_pickup(worker_id: str, run_id: UUID) -> Optional[TaskRun]:
    max_concurrent = get_run_metadata(run_id, 'max_concurrent_tasks', default=4)

    with db.transaction():
        # Kiểm tra concurrency limit
        running_count = db.query_one("""
            SELECT COUNT(*) FROM task_runs
            WHERE run_id = $run_id AND status = 'RUNNING'
        """, run_id=run_id).count

        if running_count >= max_concurrent:
            return None  # worker idle, thử lại sau

        # Lock nguyên tử — SKIP LOCKED tránh tranh chấp giữa nhiều worker
        task = db.query_one("""
            SELECT task_run_id, task_id, run_id
            FROM task_runs
            WHERE run_id = $run_id
              AND status = 'READY'
              AND worker_id IS NULL
            ORDER BY attempt_number ASC
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        """, run_id=run_id)

        if not task:
            return None

        db.execute("""
            UPDATE task_runs
            SET status = 'RUNNING',
                worker_id = $worker_id,
                locked_at = now(),
                heartbeat_at = now(),
                started_at = now()
            WHERE task_run_id = $id
        """, worker_id=worker_id, id=task.task_run_id)

        db.execute("""
            INSERT INTO events (run_id, task_run_id, event_type, actor)
            VALUES ($run_id, $task_run_id, 'TASK_STARTED', $actor)
        """, run_id=run_id, task_run_id=task.task_run_id, actor=f"worker:{worker_id}")

    return task
```

---

### Job C — Task Executor

**Trigger**: Sau khi worker pickup thành công.

```python
def execute_task(task_run: TaskRun, worker_id: str) -> None:
    try:
        # Gọi agent thực thi (CrewAI / agency-agents)
        result = run_agent(task_run)

        with db.transaction():
            output_artifact_id = None

            if task_run.has_promoted_outputs:
                # Promote output thành artifact (Invariant 2)
                output_artifact_id = promote_output_artifact(task_run, result)
                # promote_output_artifact() xử lý: create artifact ACTIVE +
                # update current_artifacts trong cùng transaction này

            db.execute("""
                UPDATE task_runs
                SET status = 'SUCCESS',
                    output_ref = $output_ref,
                    output_artifact_id = $artifact_id,
                    completed_at = now()
                WHERE task_run_id = $id AND status = 'RUNNING'
            """, output_ref=result.path, artifact_id=output_artifact_id, id=task_run.id)

            db.execute("""
                INSERT INTO events (run_id, task_run_id, event_type, actor, payload)
                VALUES ($run_id, $id, 'TASK_COMPLETED', $actor, $payload)
            """, ...)

        # Trigger downstream jobs (event-driven)
        resolve_dependencies(task_run.run_id)
        check_run_completion(task_run.run_id)

    except AgentError as e:
        error_type = classify_error(e)
        handle_task_failure(task_run, error_type, str(e))


def handle_task_failure(task_run: TaskRun, error_type: ErrorType, detail: str) -> None:
    correlation_id = new_uuid()  # mỗi failure chain = 1 correlation_id
    policy = RETRY_POLICY[error_type]

    with db.transaction():
        db.execute("""
            UPDATE task_runs
            SET status = 'FAILED',
                error_type = $error_type,
                error_detail = $detail,
                completed_at = now()
            WHERE task_run_id = $id AND status = 'RUNNING'
        """, error_type=error_type, detail=detail, id=task_run.id)

        db.execute("""
            INSERT INTO events (run_id, task_run_id, correlation_id, event_type, actor, payload)
            VALUES ($run_id, $id, $cid, 'TASK_FAILED', 'system',
                    $payload::jsonb)
        """, payload=json.dumps({"error_type": error_type, "attempt": task_run.attempt_number}), ...)

    can_retry = task_run.attempt_number < policy.max_retries

    if can_retry:
        # Tạo attempt mới — cùng correlation_id để group event
        with db.transaction():
            new_attempt_id = db.query_one("""
                INSERT INTO task_runs (
                    run_id, task_id, task_graph_artifact_id,
                    attempt_number, previous_attempt_id, status,
                    input_artifact_ids, resolved_dependencies
                )
                VALUES (
                    $run_id, $task_id, $graph_artifact_id,
                    $attempt_number, $prev_id, 'READY',
                    $inputs, $deps
                )
                RETURNING task_run_id
            """,
                attempt_number=task_run.attempt_number + 1,
                prev_id=task_run.id,
                ...
            ).task_run_id

            db.execute("""
                INSERT INTO events (run_id, task_run_id, correlation_id, event_type, actor)
                VALUES ($run_id, $new_id, $cid, 'TASK_RETRYING', 'system')
            """, ...)

        # Nếu SPEC_CONTRADICTION: invalidate spec trước khi tạo attempt mới
        # (thực ra SPEC_CONTRADICTION không retry — xem policy — nhưng để rõ logic)

    else:
        # Escalate
        escalate_run(task_run, error_type, correlation_id)


def escalate_run(task_run: TaskRun, error_type: ErrorType, correlation_id: UUID) -> None:
    with db.transaction():
        if error_type == 'SPEC_CONTRADICTION':
            # Invalidate spec_bundle (Invariant 4)
            db.execute("""
                UPDATE artifacts
                SET status = 'SUPERSEDED'
                WHERE run_id = $run_id
                  AND artifact_type = 'SPEC_BUNDLE'
                  AND status = 'ACTIVE'
            """, run_id=task_run.run_id)

            db.execute("""
                UPDATE runs
                SET current_artifacts = jsonb_set(current_artifacts, '{spec_bundle_id}', 'null')
                WHERE run_id = $run_id
            """, run_id=task_run.run_id)

        db.execute("""
            UPDATE runs
            SET paused_at = now(),
                paused_reason = $reason,
                last_activity_at = now()
            WHERE run_id = $run_id
        """, reason=f"escalation:{error_type}:{task_run.task_id}", run_id=task_run.run_id)

        db.execute("""
            INSERT INTO events (run_id, task_run_id, correlation_id, event_type, actor, payload)
            VALUES ($run_id, $task_id, $cid, 'ESCALATION_RAISED', 'system', $payload)
        """, payload=json.dumps({"error_type": error_type, "task_id": task_run.task_id}), ...)
```

---

### Job D — Dead Worker Recovery

**Trigger**: Background polling mỗi 30 giây. Không trigger event-driven.

```python
def dead_worker_recovery() -> None:
    stale_tasks = db.query("""
        SELECT task_run_id, run_id, task_id, task_graph_artifact_id,
               attempt_number, input_artifact_ids, resolved_dependencies,
               worker_id
        FROM task_runs
        WHERE status = 'RUNNING'
          AND heartbeat_at < now() - INTERVAL '2 minutes'
    """)

    for task in stale_tasks:
        correlation_id = new_uuid()
        policy = RETRY_POLICY['ENVIRONMENT_ERROR']
        can_retry = task.attempt_number < policy.max_retries

        with db.transaction():
            # Guard: kiểm tra vẫn còn RUNNING (tránh race với worker hồi phục)
            updated = db.execute("""
                UPDATE task_runs
                SET status = 'FAILED',
                    error_type = 'ENVIRONMENT_ERROR',
                    error_detail = 'worker_dead: heartbeat timeout',
                    completed_at = now()
                WHERE task_run_id = $id AND status = 'RUNNING'
            """, id=task.task_run_id)

            if updated.rowcount == 0:
                continue  # worker đã tự recover — skip

            db.execute("INSERT INTO events ... TASK_FAILED ...", correlation_id=correlation_id, ...)

            if can_retry:
                new_id = db.query_one("""
                    INSERT INTO task_runs (
                        run_id, task_id, task_graph_artifact_id,
                        attempt_number, previous_attempt_id, status,
                        input_artifact_ids, resolved_dependencies
                    ) VALUES (..., 'READY', ...) RETURNING task_run_id
                """, attempt_number=task.attempt_number + 1, prev_id=task.task_run_id, ...).task_run_id

                db.execute("INSERT INTO events ... TASK_RETRYING ...", task_run_id=new_id, correlation_id=correlation_id)

            else:
                db.execute("""
                    UPDATE runs
                    SET paused_at = now(),
                        paused_reason = 'worker_death_max_retries',
                        last_activity_at = now()
                    WHERE run_id = $run_id
                """, run_id=task.run_id)

                db.execute("INSERT INTO events ... ESCALATION_RAISED ...", correlation_id=correlation_id)
```

---

### Job E — Run State Updater

**Trigger**: Sau mỗi task → `SUCCESS` hoặc `SKIPPED`. Fallback: polling mỗi 30s.

```python
def check_run_completion(run_id: UUID) -> None:
    counts = db.query_one("""
        SELECT
            COUNT(*) FILTER (WHERE status = 'SUCCESS')                      AS success_count,
            COUNT(*) FILTER (WHERE status = 'SKIPPED')                      AS skipped_count,
            COUNT(*) FILTER (WHERE status IN ('PENDING', 'READY', 'RUNNING')) AS active_count,
            COUNT(*)                                                          AS total_count
        FROM task_runs
        WHERE run_id = $run_id
    """, run_id=run_id)

    # Run hoàn thành khi không còn task active
    # (tất cả SUCCESS hoặc SKIPPED — FAILED không có ở đây vì đã escalate)
    all_done = counts.active_count == 0
    any_success = counts.success_count > 0

    if all_done and any_success:
        with db.transaction():
            updated = db.execute("""
                UPDATE runs
                SET status = 'COMPLETED', completed_at = now(), last_activity_at = now()
                WHERE run_id = $run_id AND status = 'RUNNING_PHASE_3'
            """, run_id=run_id)

            if updated.rowcount == 1:
                db.execute("""
                    INSERT INTO events (run_id, event_type, actor, payload)
                    VALUES ($run_id, 'RUN_COMPLETED', 'system',
                            jsonb_build_object('success_count', $sc, 'skipped_count', $sk))
                """, run_id=run_id, sc=counts.success_count, sk=counts.skipped_count)
```

---

## 4. Heartbeat — Worker Liveness

Worker phải cập nhật `heartbeat_at` định kỳ khi đang RUNNING.

```python
def worker_heartbeat_loop(task_run_id: UUID, worker_id: str) -> None:
    """Chạy trong background thread trong khi task đang execute."""
    while task_still_running:
        db.execute("""
            UPDATE task_runs
            SET heartbeat_at = now()
            WHERE task_run_id = $id AND status = 'RUNNING' AND worker_id = $worker
        """, id=task_run_id, worker=worker_id)
        sleep(30)  # update mỗi 30s; Dead Worker Recovery check sau 2 phút
```

---

## 5. Job Execution Order & Trigger Map

```
Trigger                          Jobs được kích hoạt
─────────────────────────────────────────────────────
run_start()                    → [E] check_run_completion (khởi tạo)
TASK_GRAPH_APPROVED created    → [A] resolve_dependencies (initial batch)
task_run → SUCCESS             → [A] resolve_dependencies, [E] check_run_completion
task_run → SKIPPED             → [A] resolve_dependencies, [E] check_run_completion
task_run → FAILED              → [C] handle_task_failure
  ↳ retry allowed              → (new task_run READY) → [B] worker_pickup
  ↳ retry exhausted            → run → PAUSED
Every 30s (background)         → [D] dead_worker_recovery, [E] check_run_completion
Every 10s (background)         → [A] resolve_dependencies (fallback)
Worker loop (continuous)       → [B] worker_pickup
  ↳ task acquired              → [C] execute_task (with heartbeat loop)
```

---

## 6. Concurrency Safety Summary

| Scenario | Mechanism |
|---|---|
| Hai worker cùng pick một task | `FOR UPDATE SKIP LOCKED` — chỉ 1 worker thắng |
| Task được transition 2 lần | `WHERE status = 'EXPECTED'` trong mọi UPDATE |
| Artifact ACTIVE trùng | `UNIQUE INDEX uq_artifacts_one_active_per_type` |
| Worker crash giữa chừng | Dead Worker Recovery (30s polling) |
| Dependency Resolver chạy 2 lần | `WHERE status = 'PENDING'` — UPDATE an toàn, emit chỉ khi `rowcount == 1` |
| Run completion check race | `WHERE status = 'RUNNING_PHASE_3'` trong UPDATE — terminal state idempotent |
