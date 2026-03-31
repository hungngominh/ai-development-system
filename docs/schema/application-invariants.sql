-- =============================================================================
-- Application-Layer Invariants
-- Những rule này KHÔNG enforce được bằng DB constraint, phải enforce trong code.
-- =============================================================================

-- INVARIANT 1: current_artifacts consistency
-- Khi artifact.status → ACTIVE, phải thực hiện tất cả bước sau trong cùng transaction:
--   1. Set artifact mới → ACTIVE
--   2. Set TẤT CẢ artifact cùng run + cùng type đang ACTIVE khác → SUPERSEDED
--      (DB unique partial index uq_artifacts_one_active_per_type sẽ chặn nếu bỏ sót bước này)
--   3. Update runs.current_artifacts[key tương ứng] = artifact_id mới
--   4. Update runs.last_activity_at = now()
--
-- Pseudo-code:
--   BEGIN;
--     UPDATE artifacts
--       SET status = 'SUPERSEDED'
--       WHERE run_id = $run AND artifact_type = $type AND status = 'ACTIVE';
--
--     UPDATE artifacts
--       SET status = 'ACTIVE'
--       WHERE artifact_id = $new_id;
--
--     UPDATE runs
--       SET current_artifacts = jsonb_set(current_artifacts, '{spec_bundle_id}', to_jsonb($new_id::text)),
--           last_activity_at  = now()
--       WHERE run_id = $run;
--   COMMIT;
--
-- Lưu ý: current_phase cũng phải update cùng lúc nếu phase thay đổi.
-- current_phase là "display hint", không phải source of truth — nhưng phải nhất quán với status.


-- INVARIANT 2: output promotion atomicity
-- Khi task_run.status → SUCCESS và task có promoted_outputs:
--   1. Create artifact record với status = 'ACTIVE'
--   2. Update task_run.output_artifact_id
--   3. Update runs.current_artifacts nếu type cần thiết
-- Tất cả trong cùng transaction.


-- INVARIANT 3: attempt chain integrity
-- Khi tạo attempt mới cho một task đã fail, phải đảm bảo:
--   1. previous_attempt_id.run_id  = new attempt's run_id     (cùng run)
--   2. previous_attempt_id.task_id = new attempt's task_id   (cùng task)
--   3. previous_attempt_id.status IN ('FAILED', 'ABORTED')   (không retry task đang SUCCESS)
--   4. new attempt_number = previous attempt_number + 1       (liên tục, không nhảy cóc)
--   5. task_graph_artifact_id giữ nguyên từ attempt đầu tiên
--      (tất cả attempt của một task phải chạy theo cùng graph version)


-- INVARIANT 4: spec invalidation khi SPEC_CONTRADICTION
-- Khi task_run fail với error_type = 'SPEC_CONTRADICTION':
--   1. Set spec_bundle artifact → SUPERSEDED
--   2. Xóa current_artifacts.spec_bundle_id (set null)
--   3. Set run.status → PAUSED_AT_GATE_2 (hoặc phase tương ứng)
--   4. Insert ESCALATION_RAISED event
-- Tất cả trong cùng transaction.


-- INVARIANT 5: worker lock atomicity
-- Khi worker pick up task:
--   UPDATE task_runs
--   SET status = 'RUNNING', worker_id = $worker, locked_at = now(), heartbeat_at = now()
--   WHERE task_run_id = $id AND status = 'READY' AND worker_id IS NULL
--   RETURNING task_run_id;
-- Nếu RETURNING trả về 0 row → task đã bị pick bởi worker khác → không chạy.


-- =============================================================================
-- Retry / Escalate Policy
-- Encode trong execution engine config, không hardcode.
-- =============================================================================

-- error_type         | max_retries | retry_same_worker | escalate_action
-- -------------------|-------------|-------------------|------------------
-- EXECUTION_ERROR    | 2           | yes (first retry) | ESCALATION_RAISED
-- ENVIRONMENT_ERROR  | 3           | no (new worker)   | ESCALATION_RAISED
-- SPEC_AMBIGUITY     | 0           | —                 | ESCALATION_RAISED + pause
-- SPEC_CONTRADICTION | 0           | —                 | ESCALATION_RAISED + invalidate spec
-- UNKNOWN            | 1           | yes               | ESCALATION_RAISED


-- =============================================================================
-- Dead Worker Detection
-- Background job chạy định kỳ (khuyến nghị: mỗi 30 giây)
-- =============================================================================

-- Bước 1: Tìm task_run RUNNING có heartbeat stale
-- SELECT task_run_id, run_id, task_id, worker_id, attempt_number, heartbeat_at
-- FROM task_runs
-- WHERE status = 'RUNNING'
--   AND heartbeat_at < now() - INTERVAL '2 minutes';

-- Bước 2: Với mỗi task_run tìm được, thực hiện trong 1 transaction:
--   a. Mark attempt hiện tại → FAILED với error_type = 'ENVIRONMENT_ERROR'
--      error_detail = 'worker_dead: heartbeat timeout'
--      completed_at = now()
--      (KHÔNG reset về READY — giữ nguyên lịch sử audit)
--
--   b. Kiểm tra retry policy: attempt_number < max_retries cho ENVIRONMENT_ERROR (= 3)?
--      - Có: tạo attempt mới (Invariant 3), status = 'READY'
--        Emit TASK_RETRYING event với correlation_id mới
--      - Không: emit ESCALATION_RAISED event, run → PAUSED
--
-- Pseudo-code:
--   BEGIN;
--     UPDATE task_runs
--       SET status = 'FAILED', error_type = 'ENVIRONMENT_ERROR',
--           error_detail = 'worker_dead: heartbeat timeout', completed_at = now()
--       WHERE task_run_id = $dead_id AND status = 'RUNNING';
--
--     INSERT INTO task_runs (run_id, task_id, task_graph_artifact_id,
--                            attempt_number, previous_attempt_id, status, ...)
--     VALUES ($run, $task, $graph, $attempt + 1, $dead_id, 'READY', ...);
--
--     INSERT INTO events (run_id, task_run_id, correlation_id, event_type, ...)
--     VALUES ($run, $new_attempt_id, $correlation, 'TASK_RETRYING', ...);
--   COMMIT;


-- =============================================================================
-- READY Task Scheduling
-- Execution engine gồm 2 job tách biệt — không phải 1.
-- =============================================================================

-- JOB 1: Dependency Resolver
-- Chuyển PENDING → READY khi tất cả dependencies đã SUCCESS.
-- Chạy sau mỗi khi có task_run → SUCCESS.
--
-- SELECT tr.task_run_id
-- FROM task_runs tr
-- WHERE tr.run_id = $run_id
--   AND tr.status = 'PENDING'
--   AND NOT EXISTS (
--     SELECT 1 FROM unnest(tr.resolved_dependencies) AS dep(task_id)
--     WHERE NOT EXISTS (
--       SELECT 1 FROM task_runs dep_tr
--       WHERE dep_tr.run_id  = tr.run_id
--         AND dep_tr.task_id = dep.task_id
--         AND dep_tr.status  = 'SUCCESS'
--     )
--   );
-- → UPDATE task_runs SET status = 'READY' WHERE task_run_id IN (...);
-- → INSERT events: TASK_READY


-- JOB 2: Worker Pickup
-- Worker tìm task READY và lock nguyên tử bằng FOR UPDATE SKIP LOCKED.
-- SKIP LOCKED tránh nhiều worker tranh chấp cùng row.
--
-- BEGIN;
--   SELECT task_run_id, task_id, run_id
--   FROM task_runs
--   WHERE run_id = $run_id
--     AND status = 'READY'
--     AND worker_id IS NULL
--   ORDER BY attempt_number ASC
--   LIMIT 1
--   FOR UPDATE SKIP LOCKED;
--
--   -- Nếu có row: lock và update
--   UPDATE task_runs
--   SET status = 'RUNNING', worker_id = $worker, locked_at = now(), heartbeat_at = now(),
--       started_at = now()
--   WHERE task_run_id = $selected_id;
--
--   INSERT INTO events (run_id, task_run_id, event_type, actor, ...)
--   VALUES ($run, $selected_id, 'TASK_STARTED', $worker, ...);
-- COMMIT;
--
-- Nếu SKIP LOCKED trả về 0 row → không có task nào available → worker idle.
