-- v4-verification.sql
-- Adds Phase 4 verification statuses, artifact type, and event types.
-- Safe to run after control-layer-schema.sql, v2-execution-runner.sql, v3-debate-engine.sql

-- New run_status values (SUCCESS = Phase 3 terminal; COMPLETED = Phase 4 terminal)
ALTER TYPE run_status ADD VALUE IF NOT EXISTS 'RUNNING_PHASE_V';
ALTER TYPE run_status ADD VALUE IF NOT EXISTS 'PAUSED_AT_GATE_3';
ALTER TYPE run_status ADD VALUE IF NOT EXISTS 'PAUSED_AT_GATE_3B';
-- Note: 'COMPLETED' and 'SUCCESS' already exist in control-layer-schema.sql

-- New artifact type
ALTER TYPE artifact_type ADD VALUE IF NOT EXISTS 'VERIFICATION_REPORT';

-- New event types
ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'VERIFICATION_STARTED';
ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'VERIFICATION_COMPLETED';
ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'REMEDIATION_CREATED';
