-- =============================================================================
-- AI Development System — Control Layer Schema (SQLite)
-- Target: SQLite 3.35+ (for RETURNING, JSON1 functions, partial indexes)
--
-- SCOPE: Orchestration state, execution metadata, audit trail.
--        Content (spec files, task graph JSON, debate reports) lives on disk.
--        DB chỉ lưu metadata + pointers, KHÔNG lưu nội dung tài liệu.
--
-- DESIGN CHOICES (per Phase 1 v2 locked decisions):
-- - All status enums stored as TEXT + CHECK constraint (decision #16)
-- - UUIDs stored as TEXT (32-char hex from uuid.uuid4().hex)
-- - JSON columns stored as TEXT with json_valid() CHECK
-- - Arrays stored as JSON-encoded TEXT (JSON array)
-- - Timestamps stored as TEXT in ISO 8601 (UTC, via CURRENT_TIMESTAMP or app-side)
-- - Foreign keys enforced (connection sets PRAGMA foreign_keys=ON)
-- =============================================================================

-- =============================================================================
-- TABLE: runs
-- Một pipeline execution từ đầu đến cuối.
-- =============================================================================

CREATE TABLE IF NOT EXISTS runs (
    run_id              TEXT            PRIMARY KEY,
    project_id          TEXT            NOT NULL,
    parent_run_id       TEXT            REFERENCES runs(run_id),

    schema_version      TEXT            NOT NULL DEFAULT '1.0.0',
    status              TEXT            NOT NULL DEFAULT 'CREATED',
    is_resumable        INTEGER         NOT NULL DEFAULT 1,
    current_phase       TEXT,
    title               TEXT,           -- short human label, e.g. "Pipeline: debate_pipeline"

    -- Snapshot artifact ACTIVE hiện tại. JSON object with fixed keys.
    current_artifacts   TEXT            NOT NULL DEFAULT '{"initial_brief_id":null,"debate_report_id":null,"decision_log_id":null,"approved_answers_id":null,"spec_bundle_id":null,"task_graph_gen_id":null,"task_graph_approved_id":null}'
                                        CHECK (json_valid(current_artifacts)),

    -- Timestamps (ISO 8601 TEXT)
    created_at          TEXT            NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_activity_at    TEXT            NOT NULL DEFAULT CURRENT_TIMESTAMP,
    paused_at           TEXT,
    paused_reason       TEXT,
    completed_at        TEXT,

    timeout_policy      TEXT            CHECK (timeout_policy IS NULL OR json_valid(timeout_policy)),
    metadata            TEXT            NOT NULL DEFAULT '{}'
                                        CHECK (json_valid(metadata)),

    -- Status check constraint covers all known statuses across v1 + v2
    CHECK (status IN (
        'CREATED',
        'RUNNING_PHASE_1A',
        'RUNNING_PHASE_1B',
        'PAUSED_AT_GATE_1',
        'RUNNING_PHASE_1D',
        'RUNNING_PHASE_2A',
        'PAUSED_AT_GATE_2',
        'RUNNING_PHASE_3',
        'RUNNING_EXECUTION',
        'PAUSED_FOR_DECISION',
        'RUNNING_PHASE_V',
        'PAUSED_AT_GATE_3',
        'PAUSED_AT_GATE_3B',
        'COMPLETED',
        'ABORTED',
        'FAILED',
        -- v2 statuses (Phase 1 v2)
        'COLLECTING_INTAKE',
        'READY_FOR_DEBATE',
        'RUNNING_PHASE_1B_INVENTORY',
        'RUNNING_PHASE_1B_MATERIALIZE',
        'RUNNING_PHASE_1B_CRITIC',
        'RUNNING_PHASE_1B_COVERAGE',
        'FAILED_AT_QUESTION_INVENTORY',
        'FAILED_AT_QUESTION_COVERAGE'
    ))
);

CREATE INDEX IF NOT EXISTS idx_runs_project_id         ON runs(project_id);
CREATE INDEX IF NOT EXISTS idx_runs_status             ON runs(status);
CREATE INDEX IF NOT EXISTS idx_runs_last_activity_at   ON runs(last_activity_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_parent_run_id      ON runs(parent_run_id) WHERE parent_run_id IS NOT NULL;


-- =============================================================================
-- TABLE: artifacts
-- =============================================================================

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id         TEXT            PRIMARY KEY,
    run_id              TEXT            NOT NULL REFERENCES runs(run_id),

    artifact_type       TEXT            NOT NULL,
    version             INTEGER         NOT NULL CHECK (version >= 1),
    status              TEXT            NOT NULL DEFAULT 'DRAFT',

    created_by          TEXT            NOT NULL DEFAULT 'system',
    change_reason       TEXT,

    -- UUID[] → JSON array of UUIDs as TEXT
    input_artifact_ids  TEXT            NOT NULL DEFAULT '[]'
                                        CHECK (json_valid(input_artifact_ids)),

    -- Idempotency
    input_checksum      TEXT,
    checksum_scope      TEXT,

    -- File system reference
    content_ref         TEXT            NOT NULL,
    content_checksum    TEXT            NOT NULL,
    content_size        INTEGER         NOT NULL DEFAULT 0,

    -- Lifecycle
    superseded_by       TEXT            REFERENCES artifacts(artifact_id),
    created_at          TEXT            NOT NULL DEFAULT CURRENT_TIMESTAMP,

    annotations         TEXT            NOT NULL DEFAULT '{}'
                                        CHECK (json_valid(annotations)),

    CHECK (artifact_type IN (
        'INITIAL_BRIEF',
        'APPROVED_BRIEF',
        'DEBATE_REPORT',
        'DECISION_LOG',
        'APPROVED_ANSWERS',
        'SPEC_BUNDLE',
        'TASK_GRAPH_GENERATED',
        'TASK_GRAPH_APPROVED',
        'EXECUTION_LOG',
        'VERIFICATION_REPORT',
        -- v2 artifact types (Phase 1 v2)
        'INTAKE_BRIEF',
        'DECISION_INVENTORY',
        'QUESTION_COVERAGE_REPORT',
        'BRIEF_EDIT_LOG',
        'BRIEF_FINAL',
        'SPEC_TRACE_MAP',
        'SPEC_GROUNDING_VIOLATIONS',
        'BRIEF_DIGEST'
    )),
    CHECK (status IN ('DRAFT', 'ACTIVE', 'SUPERSEDED', 'FAILED')),
    CHECK (created_by IN ('system', 'user_override', 'user_patch')),
    CHECK (checksum_scope IS NULL OR checksum_scope IN ('raw_input', 'artifact_inputs', 'composed'))
);

-- Version phải unique per type per run
CREATE UNIQUE INDEX IF NOT EXISTS uq_artifacts_run_type_version
    ON artifacts(run_id, artifact_type, version);

CREATE INDEX IF NOT EXISTS idx_artifacts_run_type_status
    ON artifacts(run_id, artifact_type, status);

CREATE INDEX IF NOT EXISTS idx_artifacts_superseded_by
    ON artifacts(superseded_by) WHERE superseded_by IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_artifacts_input_checksum
    ON artifacts(run_id, artifact_type, input_checksum) WHERE input_checksum IS NOT NULL;

-- Đảm bảo mỗi run + type chỉ có đúng 1 ACTIVE artifact tại một thời điểm.
CREATE UNIQUE INDEX IF NOT EXISTS uq_artifacts_one_active_per_type
    ON artifacts(run_id, artifact_type)
    WHERE status = 'ACTIVE';

-- Note: PG had a GIN index on input_artifact_ids for fast reverse-lineage queries.
-- SQLite has no GIN. Reverse-lineage queries should use json_each() at query time,
-- which is acceptable given expected dataset size.


-- =============================================================================
-- TABLE: task_runs
-- =============================================================================

CREATE TABLE IF NOT EXISTS task_runs (
    task_run_id             TEXT            PRIMARY KEY,
    run_id                  TEXT            NOT NULL REFERENCES runs(run_id),

    task_id                 TEXT            NOT NULL,
    -- task_graph_artifact_id is nullable for synchronous pipeline tasks
    -- (e.g., RunRepo.create_sync) which don't belong to a graph.
    task_graph_artifact_id  TEXT            REFERENCES artifacts(artifact_id),

    attempt_number          INTEGER         NOT NULL DEFAULT 1 CHECK (attempt_number >= 1),
    previous_attempt_id     TEXT            REFERENCES task_runs(task_run_id),

    status                  TEXT            NOT NULL DEFAULT 'PENDING',
    agent_type              TEXT,           -- which agent class handles this task

    -- promoted_outputs: list of output names to promote to artifacts (set by graph or task config)
    promoted_outputs        TEXT            NOT NULL DEFAULT '[]'
                                            CHECK (json_valid(promoted_outputs)),

    -- UUID[] → JSON array
    input_artifact_ids      TEXT            NOT NULL DEFAULT '[]'
                                            CHECK (json_valid(input_artifact_ids)),

    -- TEXT[] → JSON array
    resolved_dependencies   TEXT            NOT NULL DEFAULT '[]'
                                            CHECK (json_valid(resolved_dependencies)),

    -- Output
    output_ref              TEXT,
    output_artifact_id      TEXT            REFERENCES artifacts(artifact_id),

    -- Failure
    error_type              TEXT,
    error_detail            TEXT,

    -- Concurrency control
    worker_id               TEXT,
    locked_at               TEXT,
    heartbeat_at            TEXT,

    -- Timestamps
    started_at              TEXT,
    completed_at            TEXT,

    annotations             TEXT            NOT NULL DEFAULT '{}'
                                            CHECK (json_valid(annotations)),

    -- v2 execution runner additions (merged from v2-execution-runner migration)
    retry_count             INTEGER         NOT NULL DEFAULT 0,
    retry_at                TEXT,
    agent_routing_key       TEXT,
    context_snapshot        TEXT            CHECK (context_snapshot IS NULL OR json_valid(context_snapshot)),
    materialized_at         TEXT,

    CHECK (status IN (
        'PENDING', 'READY', 'RUNNING', 'SUCCESS', 'FAILED', 'SKIPPED', 'ABORTED',
        'FAILED_RETRYABLE', 'FAILED_FINAL', 'BLOCKED_BY_FAILURE'
    )),
    CHECK (error_type IS NULL OR error_type IN (
        'EXECUTION_ERROR', 'SPEC_AMBIGUITY', 'SPEC_CONTRADICTION', 'ENVIRONMENT_ERROR', 'UNKNOWN'
    ))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_task_runs_run_task_attempt
    ON task_runs(run_id, task_id, attempt_number);

CREATE INDEX IF NOT EXISTS idx_task_runs_run_status
    ON task_runs(run_id, status);

CREATE INDEX IF NOT EXISTS idx_task_runs_worker_status
    ON task_runs(worker_id, status) WHERE worker_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_task_runs_heartbeat
    ON task_runs(heartbeat_at) WHERE status = 'RUNNING';

CREATE INDEX IF NOT EXISTS idx_task_runs_previous_attempt
    ON task_runs(previous_attempt_id) WHERE previous_attempt_id IS NOT NULL;


-- =============================================================================
-- TABLE: events
-- Append-only audit trail.
-- =============================================================================

CREATE TABLE IF NOT EXISTS events (
    event_id        TEXT            PRIMARY KEY,
    run_id          TEXT            NOT NULL REFERENCES runs(run_id),
    task_run_id     TEXT            REFERENCES task_runs(task_run_id),

    correlation_id  TEXT,

    event_type      TEXT            NOT NULL,
    occurred_at     TEXT            NOT NULL DEFAULT CURRENT_TIMESTAMP,

    actor           TEXT            NOT NULL DEFAULT 'system',

    payload         TEXT            NOT NULL DEFAULT '{}'
                                    CHECK (json_valid(payload)),

    CHECK (event_type IN (
        'RUN_CREATED', 'RUN_COMPLETED', 'RUN_ABORTED',
        'PHASE_STARTED', 'PHASE_COMPLETED',
        'GATE_ENTERED', 'GATE_APPROVED', 'GATE_REJECTED',
        'ARTIFACT_CREATED', 'ARTIFACT_APPROVED', 'ARTIFACT_SUPERSEDED', 'ARTIFACT_FAILED',
        'TASK_READY', 'TASK_STARTED', 'TASK_COMPLETED', 'TASK_FAILED', 'TASK_RETRYING', 'TASK_SKIPPED',
        'VERIFICATION_PASSED', 'VERIFICATION_FAILED',
        'VERIFICATION_STARTED', 'VERIFICATION_COMPLETED', 'REMEDIATION_CREATED',
        'ESCALATION_RAISED', 'HUMAN_DECISION_RECORDED',
        'RULES_APPLIED', 'BEADS_SYNC_WARNING',
        -- v2 events
        'INTAKE_STARTED', 'INTAKE_FIELD_ANSWERED', 'INTAKE_FIELD_SUGGESTED',
        'INTAKE_RESUMED', 'INTAKE_COMPLETED', 'INTAKE_ABORTED',
        'QUESTION_INVENTORY_GENERATED', 'QUESTION_DRAFT_GENERATED',
        'CRITIC_ITERATION_DONE', 'COVERAGE_REPORT_GENERATED',
        'MODERATOR_PARSE_FAIL',
        'SECTION_GENERATION_DEGRADED', 'SECTION_LENGTH_EXCEEDED',
        'BRIEF_EDIT_APPLIED',
        -- G8 brief edit re-trigger events
        'G8_RETRIGGER_STARTED', 'G8_NOOP', 'G8_RETRIGGER_COMPLETED',
        'BRIEF_EDIT_THRESHOLD_EXCEEDED'
    ))
);

CREATE INDEX IF NOT EXISTS idx_events_run_occurred
    ON events(run_id, occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_events_task_run_occurred
    ON events(task_run_id, occurred_at DESC) WHERE task_run_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_events_correlation
    ON events(correlation_id) WHERE correlation_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_events_type_occurred
    ON events(event_type, occurred_at DESC);


-- =============================================================================
-- TABLE: escalations (merged from v2-execution-runner migration)
-- =============================================================================

CREATE TABLE IF NOT EXISTS escalations (
    escalation_id   TEXT            PRIMARY KEY,
    run_id          TEXT            NOT NULL REFERENCES runs(run_id),
    task_run_id     TEXT            NOT NULL REFERENCES task_runs(task_run_id),
    status          TEXT            NOT NULL DEFAULT 'OPEN',
    reason          TEXT            NOT NULL,
    options         TEXT            NOT NULL
                                    CHECK (json_valid(options)),
    resolution      TEXT,
    resolved_at     TEXT,
    created_at      TEXT            NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CHECK (status IN ('OPEN', 'RESOLVED')),

    -- Prevent duplicate open escalations for same (run, task, reason)
    UNIQUE (run_id, task_run_id, reason, status)
);

CREATE INDEX IF NOT EXISTS idx_escalations_run_open
    ON escalations (run_id) WHERE status = 'OPEN';


-- =============================================================================
-- TABLE: artifact_version_locks
-- Tracks the next-available version number per (run, artifact_type).
-- Atomic increment used by ArtifactRepo when promoting outputs to artifacts.
-- =============================================================================

CREATE TABLE IF NOT EXISTS artifact_version_locks (
    run_id              TEXT            NOT NULL REFERENCES runs(run_id),
    artifact_type       TEXT            NOT NULL,
    current_version     INTEGER         NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, artifact_type)
);
