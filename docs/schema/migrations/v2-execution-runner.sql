-- v2-execution-runner.sql
-- Requires PostgreSQL 15+ (NULLS NOT DISTINCT)

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

-- 4. Idempotency constraint for materializer
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_task_runs_attempt'
    ) THEN
        ALTER TABLE task_runs
            ADD CONSTRAINT uq_task_runs_attempt
            UNIQUE (run_id, task_id, attempt_number);
    END IF;
END $$;

-- 5. Escalations table
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
    CONSTRAINT uq_escalation_open_dedup
        UNIQUE (run_id, task_run_id, reason, status)
);

CREATE INDEX IF NOT EXISTS idx_escalations_run_open
    ON escalations (run_id) WHERE status = 'OPEN';
