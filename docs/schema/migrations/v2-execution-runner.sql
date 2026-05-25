-- v2-execution-runner.sql (SQLite)
--
-- Adds execution runner support: task_runs additional columns + escalations table.
--
-- These additions are ALSO present in the current control-layer-schema.sql, so this
-- migration is idempotent — running on a fresh DB (control-layer applied) is a no-op.
-- Running on an old DB (pre-v2) adds the missing pieces.
--
-- SQLite note: ALTER TABLE ADD COLUMN doesn't support IF NOT EXISTS in versions before
-- 3.35. App-layer migration runner should check column existence via PRAGMA table_info
-- before issuing ALTER. The raw ADD COLUMN statements below are commented out for that
-- reason — uncomment + skip-on-exists in the runner.

-- ALTER TABLE task_runs ADD COLUMN retry_count        INTEGER NOT NULL DEFAULT 0;
-- ALTER TABLE task_runs ADD COLUMN retry_at           TEXT;
-- ALTER TABLE task_runs ADD COLUMN agent_routing_key  TEXT;
-- ALTER TABLE task_runs ADD COLUMN context_snapshot   TEXT;
-- ALTER TABLE task_runs ADD COLUMN materialized_at    TEXT;

-- escalations table (CREATE TABLE IF NOT EXISTS is supported; no-op if already present)
CREATE TABLE IF NOT EXISTS escalations (
    escalation_id   TEXT            PRIMARY KEY,
    run_id          TEXT            NOT NULL REFERENCES runs(run_id),
    task_run_id     TEXT            NOT NULL REFERENCES task_runs(task_run_id),
    status          TEXT            NOT NULL DEFAULT 'OPEN',
    reason          TEXT            NOT NULL,
    options         TEXT            NOT NULL CHECK (json_valid(options)),
    resolution      TEXT,
    resolved_at     TEXT,
    created_at      TEXT            NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (status IN ('OPEN', 'RESOLVED')),
    UNIQUE (run_id, task_run_id, reason, status)
);

CREATE INDEX IF NOT EXISTS idx_escalations_run_open
    ON escalations (run_id) WHERE status = 'OPEN';

-- Status enum values (RUNNING_EXECUTION, PAUSED_FOR_DECISION) and task_run_status values
-- (FAILED_RETRYABLE, FAILED_FINAL, BLOCKED_BY_FAILURE) are merged into the CHECK
-- constraints in control-layer-schema.sql. SQLite has no ALTER TYPE.
