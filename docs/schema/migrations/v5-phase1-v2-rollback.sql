-- v5-phase1-v2-rollback.sql
-- Rollback for v5-phase1-v2.sql
--
-- LIMITATIONS:
-- - PG enum values added via ALTER TYPE ADD VALUE cannot be removed (PG limitation).
--   The new event_type values (INTAKE_STARTED, etc.) will persist in the type.
--   This is acceptable: they are unused if v2 code path is disabled.
-- - The runs_status_check_v2 / artifacts_type_check_v2 constraints are dropped, restoring
--   the column to plain TEXT without restriction.
-- - Data in new columns is preserved (intake_state, gate1_session_state) — drops would
--   destroy in-flight wizard sessions.
--
-- For full rollback to pre-v5 state, see v6 cleanup migration (planned T+12w).

BEGIN;

-- Drop new check constraints (revert to unrestricted TEXT)
ALTER TABLE runs DROP CONSTRAINT IF EXISTS runs_status_check_v2;
ALTER TABLE artifacts DROP CONSTRAINT IF EXISTS artifacts_type_check_v2;

-- Drop new indexes
DROP INDEX IF EXISTS idx_runs_pipeline_version;
DROP INDEX IF EXISTS idx_runs_legacy;
DROP INDEX IF EXISTS idx_runs_intake_status;
DROP INDEX IF EXISTS idx_migration_audit_run_id;

-- Drop new FK (best-effort)
ALTER TABLE runs DROP CONSTRAINT IF EXISTS runs_intake_brief_id_fkey;

-- Drop migration audit table
DROP TABLE IF EXISTS migration_audit;

-- NOTE: We do NOT drop the new columns (pipeline_version, intake_state, intake_brief_id,
-- gate1_session_state, legacy, feature_overrides). Doing so would destroy in-flight
-- wizard state. Re-applying v5 will re-add constraints; columns remain.
--
-- To re-enable old enum-based columns:
-- ALTER TABLE runs ALTER COLUMN status TYPE run_status USING status::run_status;
-- Note: This will FAIL if any row has a status value not present in the old enum.
-- Must first UPDATE such rows to legacy-compatible values.

COMMIT;
