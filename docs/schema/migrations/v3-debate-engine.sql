-- v3-debate-engine.sql
-- Adds event types used by Debate Engine and Rule Registry.
-- Safe to run on a DB that already has the base schema (control-layer-schema.sql)
-- and v2-execution-runner.sql applied.

ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'RULES_APPLIED';
ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'BEADS_SYNC_WARNING';
