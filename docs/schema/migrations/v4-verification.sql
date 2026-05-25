-- v4-verification.sql (SQLite)
--
-- Original v4 added run_status values (RUNNING_PHASE_V, PAUSED_AT_GATE_3, PAUSED_AT_GATE_3B),
-- artifact_type VERIFICATION_REPORT, and event types (VERIFICATION_STARTED, COMPLETED,
-- REMEDIATION_CREATED). All now in CHECK constraints in control-layer-schema.sql.
--
-- This file is a no-op for SQLite-only deployments.

SELECT 1;
