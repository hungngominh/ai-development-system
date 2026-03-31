-- =============================================================================
-- AI Development System — Control Layer Schema
-- PostgreSQL v15+
--
-- SCOPE: Orchestration state, execution metadata, audit trail.
--        Content (spec files, task graph JSON, debate reports) lives on disk.
--        DB chỉ lưu metadata + pointers, KHÔNG lưu nội dung tài liệu.
--
-- MAPPING với file system:
--   artifact.content_ref  → /runs/{run_id}/artifacts/{type}/v{version}/
--   task_run.output_ref   → /runs/{run_id}/tasks/{task_id}/attempt-{n}/
-- =============================================================================


-- =============================================================================
-- ENUMS
-- =============================================================================

CREATE TYPE run_status AS ENUM (
    'CREATED',
    'RUNNING_PHASE_1A',    -- Normalize + sinh câu hỏi
    'RUNNING_PHASE_1B',    -- AI Debate
    'PAUSED_AT_GATE_1',    -- Chờ human duyệt debate report
    'RUNNING_PHASE_1D',    -- Build spec bundle
    'RUNNING_PHASE_2A',    -- Sinh task graph
    'PAUSED_AT_GATE_2',    -- Chờ human duyệt task graph
    'RUNNING_PHASE_3',     -- Execution
    'COMPLETED',
    'ABORTED',
    'FAILED'
);

CREATE TYPE artifact_type AS ENUM (
    'INITIAL_BRIEF',
    'DEBATE_REPORT',
    'DECISION_LOG',
    'APPROVED_ANSWERS',
    'SPEC_BUNDLE',
    'TASK_GRAPH_GENERATED',
    'TASK_GRAPH_APPROVED',
    'EXECUTION_LOG'
);

CREATE TYPE artifact_status AS ENUM (
    'DRAFT',       -- đang sinh, chưa dùng được
    'ACTIVE',      -- đang là source of truth
    'SUPERSEDED',  -- đã bị version mới thay
    'FAILED'       -- attempt sinh artifact nhưng thất bại
    -- ARCHIVED: dự phòng, chưa implement v1
);

CREATE TYPE created_by_type AS ENUM (
    'system',
    'user_override',   -- user chủ động thay đổi
    'user_patch'       -- user patch một phần nhỏ
);

CREATE TYPE checksum_scope AS ENUM (
    'raw_input',        -- INITIAL_BRIEF: hash trực tiếp từ user text
    'artifact_inputs',  -- hash từ input_artifact_ids content checksums
    'composed'          -- kết hợp nhiều nguồn
);

CREATE TYPE task_run_status AS ENUM (
    'PENDING',   -- chưa đủ điều kiện
    'READY',     -- tất cả dependencies SUCCESS, chờ worker pick up
    'RUNNING',   -- worker đang xử lý
    'SUCCESS',
    'FAILED',
    'SKIPPED',   -- human quyết định skip khi escalate
    'ABORTED'    -- run bị abort trong khi task đang chạy
);

CREATE TYPE error_type AS ENUM (
    'EXECUTION_ERROR',     -- worker làm sai → retry
    'SPEC_AMBIGUITY',      -- spec không rõ → escalate lên spec layer
    'SPEC_CONTRADICTION',  -- spec mâu thuẫn nội bộ → invalidate spec + escalate
    'ENVIRONMENT_ERROR',   -- infra/tool fail → retry worker khác
    'UNKNOWN'              -- retry 1 lần → escalate
);

CREATE TYPE event_type AS ENUM (
    'RUN_CREATED',
    'RUN_COMPLETED',
    'RUN_ABORTED',
    'PHASE_STARTED',
    'PHASE_COMPLETED',
    'GATE_ENTERED',
    'GATE_APPROVED',
    'GATE_REJECTED',
    'ARTIFACT_CREATED',
    'ARTIFACT_APPROVED',
    'ARTIFACT_SUPERSEDED',
    'ARTIFACT_FAILED',
    'TASK_READY',
    'TASK_STARTED',
    'TASK_COMPLETED',
    'TASK_FAILED',
    'TASK_RETRYING',
    'TASK_SKIPPED',
    'VERIFICATION_PASSED',
    'VERIFICATION_FAILED',
    'ESCALATION_RAISED',
    'HUMAN_DECISION_RECORDED'
);


-- =============================================================================
-- TABLE: runs
-- Một pipeline execution từ đầu đến cuối.
-- =============================================================================

CREATE TABLE runs (
    run_id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID            NOT NULL,
    parent_run_id       UUID            REFERENCES runs(run_id),  -- fork / re-run

    schema_version      TEXT            NOT NULL DEFAULT '1.0.0',
    status              run_status      NOT NULL DEFAULT 'CREATED',
    is_resumable        BOOLEAN         NOT NULL DEFAULT TRUE,
    current_phase       TEXT,           -- tường minh hơn status, dùng cho display

    -- Snapshot nhanh artifact ACTIVE hiện tại.
    -- INVARIANT: phải update trong cùng transaction với artifact.status → ACTIVE.
    -- Luôn khởi tạo với đủ keys (null), không để thiếu key — code đọc state không cần
    -- xử lý "missing key" lẫn null.
    current_artifacts   JSONB           NOT NULL DEFAULT '{
        "initial_brief_id":       null,
        "debate_report_id":       null,
        "decision_log_id":        null,
        "approved_answers_id":    null,
        "spec_bundle_id":         null,
        "task_graph_gen_id":      null,
        "task_graph_approved_id": null
    }'::jsonb,

    -- Timestamps
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT now(),
    last_activity_at    TIMESTAMPTZ     NOT NULL DEFAULT now(),
    paused_at           TIMESTAMPTZ,
    paused_reason       TEXT,
    completed_at        TIMESTAMPTZ,

    -- Timeout policy — null = chưa bật, shape cố định để tránh drift
    timeout_policy      JSONB,
    -- Shape:
    -- {
    --   "gate_1_stale_after_hours": integer | null,
    --   "gate_2_stale_after_hours": integer | null,
    --   "auto_abort_after_days":    integer | null
    -- }

    metadata            JSONB           NOT NULL DEFAULT '{}'::jsonb
);

-- Indexes
CREATE INDEX idx_runs_project_id         ON runs(project_id);
CREATE INDEX idx_runs_status             ON runs(status);
CREATE INDEX idx_runs_last_activity_at   ON runs(last_activity_at DESC);
CREATE INDEX idx_runs_parent_run_id      ON runs(parent_run_id) WHERE parent_run_id IS NOT NULL;


-- =============================================================================
-- TABLE: artifacts
-- Mọi output có ý nghĩa của từng phase. Content nằm trên disk, không nằm ở đây.
-- =============================================================================

CREATE TABLE artifacts (
    artifact_id         UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id              UUID            NOT NULL REFERENCES runs(run_id),

    artifact_type       artifact_type   NOT NULL,
    version             INTEGER         NOT NULL CHECK (version >= 1),
    status              artifact_status NOT NULL DEFAULT 'DRAFT',

    created_by          created_by_type NOT NULL DEFAULT 'system',
    change_reason       TEXT,           -- "user changed auth method", "spec ambiguity fixed"

    -- Explicit lineage — biết artifact này được sinh từ artifact nào.
    -- Tra bảng input checksum contract trong design doc để biết mỗi type dùng input gì.
    input_artifact_ids  UUID[]          NOT NULL DEFAULT '{}',

    -- Idempotency: hash của inputs. Nếu gọi lại với cùng input_checksum → trả về artifact đã có.
    input_checksum      TEXT,
    checksum_scope      checksum_scope,

    -- File system reference
    content_ref         TEXT            NOT NULL,   -- path tới file/folder trên disk
    content_checksum    TEXT            NOT NULL,   -- SHA-256 của content, dùng detect corruption
    content_size        BIGINT          NOT NULL DEFAULT 0,  -- bytes

    -- Lifecycle
    superseded_by       UUID            REFERENCES artifacts(artifact_id),
    -- INVARIANTS (enforce ở application layer, DB chỉ giữ FK):
    --   superseded_by.run_id = this.run_id          (không supersede artifact khác run)
    --   superseded_by.artifact_type = this.type     (không supersede artifact khác loại)
    --   superseded_by.version > this.version        (phiên bản mới hơn mới supersede được)
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT now(),

    annotations         JSONB           NOT NULL DEFAULT '{}'::jsonb
);

-- Version phải unique per type per run
-- => tránh tạo 2 artifact cùng loại cùng version trong 1 run
CREATE UNIQUE INDEX uq_artifacts_run_type_version
    ON artifacts(run_id, artifact_type, version);

-- Tìm artifact ACTIVE theo run + type (query thường xuyên nhất)
CREATE INDEX idx_artifacts_run_type_status
    ON artifacts(run_id, artifact_type, status);

-- Tìm artifact theo lineage (ai supersede tôi?)
CREATE INDEX idx_artifacts_superseded_by
    ON artifacts(superseded_by) WHERE superseded_by IS NOT NULL;

-- Idempotency check
CREATE INDEX idx_artifacts_input_checksum
    ON artifacts(run_id, artifact_type, input_checksum) WHERE input_checksum IS NOT NULL;

-- Đảm bảo mỗi run + type chỉ có đúng 1 ACTIVE artifact tại một thời điểm.
-- Bảo vệ khỏi bug transaction hoặc code path bị sót cập nhật SUPERSEDED.
-- DB enforce lớp này; application invariant 1 enforce phần transition.
CREATE UNIQUE INDEX uq_artifacts_one_active_per_type
    ON artifacts(run_id, artifact_type)
    WHERE status = 'ACTIVE';

-- GIN index cho lineage query: "artifact nào phụ thuộc vào X?"
-- Dùng khi trace ngược: spec_bundle X → task_graph nào dùng nó?
CREATE INDEX idx_artifacts_input_ids_gin
    ON artifacts USING GIN (input_artifact_ids);


-- =============================================================================
-- TABLE: task_runs
-- Mỗi attempt chạy một task. Một task có thể có nhiều attempt (retry chain).
-- Execution engine quyết định scheduling — không hardcode trong bảng này.
-- =============================================================================

CREATE TABLE task_runs (
    task_run_id             UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id                  UUID            NOT NULL REFERENCES runs(run_id),

    task_id                 TEXT            NOT NULL,  -- "TASK-1", "TASK-2", từ task_graph
    task_graph_artifact_id  UUID            NOT NULL REFERENCES artifacts(artifact_id),
    -- ^ biết rõ task này chạy theo graph version nào, kể cả khi graph bị supersede sau
    -- INVARIANT: task_graph_artifact_id.artifact_type phải = 'TASK_GRAPH_APPROVED'
    --            enforce ở application layer khi tạo task_run record.

    attempt_number          INTEGER         NOT NULL DEFAULT 1 CHECK (attempt_number >= 1),
    previous_attempt_id     UUID            REFERENCES task_runs(task_run_id),
    -- ^ chain retry: attempt 3 → attempt 2 → attempt 1

    status                  task_run_status NOT NULL DEFAULT 'PENDING',

    -- Lineage input
    input_artifact_ids      UUID[]          NOT NULL DEFAULT '{}',

    -- Snapshot dependencies tại thời điểm task được đánh dấu READY.
    -- Giữ nguyên kể cả khi task_graph bị supersede sau.
    resolved_dependencies   TEXT[]          NOT NULL DEFAULT '{}',
    -- Ví dụ: ["TASK-1", "TASK-2"]

    -- Output
    output_ref              TEXT,           -- path trên disk
    output_artifact_id      UUID            REFERENCES artifacts(artifact_id),
    -- ^ chỉ có nếu output này được promote thành artifact (promoted_outputs trong task_graph)
    -- INVARIANTS (enforce ở application layer):
    --   output_artifact_id.run_id = this.run_id
    --   output_artifact_id.status = 'ACTIVE' sau khi promote
    --   chỉ set khi task_run.status → SUCCESS
    --   phải trong cùng transaction SUCCESS (xem Invariant 2)

    -- Failure
    error_type              error_type,
    error_detail            TEXT,

    -- Concurrency control
    worker_id               TEXT,           -- identity của worker đang giữ task
    locked_at               TIMESTAMPTZ,
    heartbeat_at            TIMESTAMPTZ,    -- worker cập nhật định kỳ; nếu stale → DEAD, release lock

    -- Timestamps
    started_at              TIMESTAMPTZ,
    completed_at            TIMESTAMPTZ,

    annotations             JSONB           NOT NULL DEFAULT '{}'::jsonb
);

-- Attempt phải unique per task per run
CREATE UNIQUE INDEX uq_task_runs_run_task_attempt
    ON task_runs(run_id, task_id, attempt_number);

-- Tìm task READY để schedule
CREATE INDEX idx_task_runs_run_status
    ON task_runs(run_id, status);

-- Tìm task RUNNING của một worker (heartbeat check, crash recovery)
CREATE INDEX idx_task_runs_worker_status
    ON task_runs(worker_id, status) WHERE worker_id IS NOT NULL;

-- Dead worker detection — tìm RUNNING tasks có heartbeat cũ
CREATE INDEX idx_task_runs_heartbeat
    ON task_runs(heartbeat_at) WHERE status = 'RUNNING';

-- Traverse retry chain ngược
CREATE INDEX idx_task_runs_previous_attempt
    ON task_runs(previous_attempt_id) WHERE previous_attempt_id IS NOT NULL;

-- GIN index cho lineage query: "task_run nào dùng artifact X?"
CREATE INDEX idx_task_runs_input_artifact_ids_gin
    ON task_runs USING GIN (input_artifact_ids);


-- =============================================================================
-- TABLE: events
-- Append-only. Không bao giờ UPDATE hoặc DELETE.
-- Ba mục đích: debug timeline, realtime UI, audit compliance.
-- =============================================================================

CREATE TABLE events (
    event_id        UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          UUID            NOT NULL REFERENCES runs(run_id),
    task_run_id     UUID            REFERENCES task_runs(task_run_id),

    -- Group các event thuộc cùng một operation (retry flow, escalation cycle)
    correlation_id  UUID,

    event_type      event_type      NOT NULL,
    occurred_at     TIMESTAMPTZ     NOT NULL DEFAULT now(),

    -- "system" | "human" | "agent:DatabaseSpecialist" | "agent:SecuritySpecialist"
    actor           TEXT            NOT NULL DEFAULT 'system',

    payload         JSONB           NOT NULL DEFAULT '{}'::jsonb
    -- payload là event-specific, không enforce schema ở DB level
    -- Ví dụ TASK_FAILED: { "error_type": "EXECUTION_ERROR", "detail": "...", "attempt": 2 }
);

-- Query timeline của một run
CREATE INDEX idx_events_run_occurred
    ON events(run_id, occurred_at DESC);

-- Query events của một task_run (debug một task cụ thể)
CREATE INDEX idx_events_task_run_occurred
    ON events(task_run_id, occurred_at DESC) WHERE task_run_id IS NOT NULL;

-- Group theo operation
CREATE INDEX idx_events_correlation
    ON events(correlation_id) WHERE correlation_id IS NOT NULL;

-- Filter theo event type (realtime UI, monitoring)
CREATE INDEX idx_events_type_occurred
    ON events(event_type, occurred_at DESC);
