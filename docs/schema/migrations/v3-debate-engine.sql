-- v3-debate-engine.sql (SQLite)
--
-- The original v3 migration added event types RULES_APPLIED + BEADS_SYNC_WARNING
-- via ALTER TYPE on a PG ENUM. In SQLite these are part of the CHECK constraint
-- in control-layer-schema.sql; this file is intentionally a no-op for new DBs.
--
-- For DBs that predate the SQLite migration (i.e., none, since the SQLite rewrite is
-- the only supported path), no action is needed.

-- intentional no-op
SELECT 1;
