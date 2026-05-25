-- v5-phase1-v2.sql (SQLite)
--
-- Phase 1 v2 schema additions: intake wizard, brief v2, decision inventory,
-- gate 1 session state, spec gen trace map.
--
-- Idempotent + additive. Safe to re-run.
--
-- Per locked decision #16, status enums are TEXT + CHECK constraint, which
-- SQLite favors natively. No PG-specific syntax remains.

-- =============================================================================
-- 1. New columns on runs table
--    SQLite ALTER TABLE ADD COLUMN doesn't support IF NOT EXISTS pre-3.35.
--    The migration runner (db/migrator.py) checks PRAGMA table_info before issuing.
--    Here we declare the intent; runner skips existing columns.
-- =============================================================================

-- ALTER TABLE runs ADD COLUMN pipeline_version       INTEGER NOT NULL DEFAULT 1;
-- ALTER TABLE runs ADD COLUMN intake_state           TEXT;
-- ALTER TABLE runs ADD COLUMN intake_brief_id        TEXT;
-- ALTER TABLE runs ADD COLUMN gate1_session_state    TEXT;
-- ALTER TABLE runs ADD COLUMN legacy                 INTEGER NOT NULL DEFAULT 0;
-- ALTER TABLE runs ADD COLUMN feature_overrides      TEXT;

-- Runner emits each ALTER individually after checking column absence.

-- =============================================================================
-- 2. Indexes for new columns
-- =============================================================================

CREATE INDEX IF NOT EXISTS idx_runs_pipeline_version ON runs(pipeline_version);
CREATE INDEX IF NOT EXISTS idx_runs_legacy           ON runs(legacy) WHERE legacy = 1;
CREATE INDEX IF NOT EXISTS idx_runs_intake_status    ON runs(status) WHERE status = 'COLLECTING_INTAKE';

-- =============================================================================
-- 3. Status / artifact_type / event_type CHECK constraints
--    SQLite CHECK constraints on existing columns require table recreation.
--    For new SQLite-only deployments these are already in control-layer-schema.sql
--    with full v2 enum values. Old DBs (none in SQLite-only world) would need
--    table recreation, handled by runner if needed.
-- =============================================================================

-- New status values (all already in control-layer-schema.sql CHECK):
--   COLLECTING_INTAKE, READY_FOR_DEBATE,
--   RUNNING_PHASE_1B_INVENTORY, RUNNING_PHASE_1B_MATERIALIZE,
--   RUNNING_PHASE_1B_CRITIC, RUNNING_PHASE_1B_COVERAGE,
--   FAILED_AT_QUESTION_INVENTORY, FAILED_AT_QUESTION_COVERAGE

-- New artifact types (all in control-layer-schema.sql CHECK):
--   INTAKE_BRIEF, DECISION_INVENTORY, QUESTION_COVERAGE_REPORT,
--   BRIEF_EDIT_LOG, BRIEF_FINAL, SPEC_TRACE_MAP, SPEC_GROUNDING_VIOLATIONS,
--   BRIEF_DIGEST

-- New event types (all in control-layer-schema.sql CHECK):
--   INTAKE_STARTED, INTAKE_FIELD_ANSWERED, INTAKE_FIELD_SUGGESTED, INTAKE_RESUMED,
--   INTAKE_COMPLETED, INTAKE_ABORTED, QUESTION_INVENTORY_GENERATED, QUESTION_DRAFT_GENERATED,
--   CRITIC_ITERATION_DONE, COVERAGE_REPORT_GENERATED, MODERATOR_PARSE_FAIL,
--   SECTION_GENERATION_DEGRADED, SECTION_LENGTH_EXCEEDED, BRIEF_EDIT_APPLIED

-- =============================================================================
-- 4. Migration audit table
-- =============================================================================

CREATE TABLE IF NOT EXISTS migration_audit (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id             TEXT NOT NULL,
    migration_version  INTEGER NOT NULL,
    classification     TEXT NOT NULL,
    classified_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    notes              TEXT
);

CREATE INDEX IF NOT EXISTS idx_migration_audit_run_id ON migration_audit(run_id);

-- =============================================================================
-- 5. Backfill: mark all existing runs as legacy
--    Skipped here — runner does this after ADD COLUMN succeeds, since the column
--    might not exist on first apply.
-- =============================================================================

-- UPDATE runs SET legacy = 1 WHERE pipeline_version = 1 AND legacy = 0;
