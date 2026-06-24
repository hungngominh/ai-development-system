"""Tests for schema migration runner — verifies SQLite schema is correct.

These tests are the safety net for the SQLite migration. They prove:
1. control-layer-schema.sql applies cleanly to a fresh DB
2. All v2-v5 migrations apply idempotently
3. Re-running migrations is a no-op
4. Tables, indexes, constraints are created correctly
"""
from __future__ import annotations

import json

import pytest

from ai_dev_system.db.connection import get_connection
from ai_dev_system.db.helpers import dump_json, new_uuid, utc_now_iso
from ai_dev_system.db.migrator import (
    V5_ADD_COLUMNS,
    apply_schema,
    get_applied_migrations,
)


@pytest.fixture
def fresh_conn():
    """Fresh in-memory SQLite connection."""
    conn = get_connection("sqlite:///:memory:")
    yield conn
    conn.close()


class TestApplySchema:
    def test_fresh_db_applies_clean(self, fresh_conn):
        results = apply_schema(fresh_conn)
        # control-layer should apply, plus migrations
        applied_names = [r.name for r in results if r.applied]
        assert "control-layer-schema.sql" in applied_names

    def test_no_errors(self, fresh_conn):
        results = apply_schema(fresh_conn)
        errors = [r for r in results if r.error]
        assert errors == [], f"Migration errors: {errors}"

    def test_v5_columns_added(self, fresh_conn):
        apply_schema(fresh_conn)
        # Check each v5 column on runs
        cur = fresh_conn.execute("PRAGMA table_info(runs)")
        cols = {row["name"] for row in cur.fetchall()}
        for col_name, _ in V5_ADD_COLUMNS["runs"]:
            assert col_name in cols, f"v5 column missing: {col_name}"

    def test_tables_created(self, fresh_conn):
        apply_schema(fresh_conn)
        cur = fresh_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row["name"] for row in cur.fetchall()}
        expected = {
            "runs", "artifacts", "task_runs", "events",
            "escalations", "schema_migrations", "migration_audit",
        }
        assert expected.issubset(tables), f"Missing tables: {expected - tables}"

    def test_idempotent_rerun(self, fresh_conn):
        """Applying twice must not error."""
        results1 = apply_schema(fresh_conn)
        results2 = apply_schema(fresh_conn)

        # Second run: control-layer reapplies (uses IF NOT EXISTS), migrations skip
        errors = [r for r in results2 if r.error]
        assert errors == [], f"Idempotent errors: {errors}"

    def test_migration_tracking(self, fresh_conn):
        apply_schema(fresh_conn)
        applied = get_applied_migrations(fresh_conn)
        # We should track v2, v3, v4, v5
        names = " ".join(applied)
        assert "v2-execution-runner.sql" in names
        assert "v5-phase1-v2.sql" in names


class TestSchemaInvariants:
    """Verify CHECK constraints + FK enforcement work on real inserts."""

    def test_runs_insert_valid_status(self, fresh_conn):
        apply_schema(fresh_conn)
        rid = new_uuid()
        fresh_conn.execute(
            "INSERT INTO runs (run_id, project_id, status) VALUES (?, ?, ?)",
            (rid, new_uuid(), "RUNNING_PHASE_1A"),
        )
        fresh_conn.commit()
        row = fresh_conn.execute(
            "SELECT * FROM runs WHERE run_id = ?", (rid,)
        ).fetchone()
        assert row["status"] == "RUNNING_PHASE_1A"

    def test_runs_invalid_status_rejected(self, fresh_conn):
        import sqlite3 as sq3
        apply_schema(fresh_conn)
        with pytest.raises(sq3.IntegrityError):
            fresh_conn.execute(
                "INSERT INTO runs (run_id, project_id, status) VALUES (?, ?, ?)",
                (new_uuid(), new_uuid(), "NOT_A_STATUS"),
            )

    def test_runs_new_v2_status_accepted(self, fresh_conn):
        """COLLECTING_INTAKE is a v2 status added by v5; must be valid after migration."""
        apply_schema(fresh_conn)
        fresh_conn.execute(
            "INSERT INTO runs (run_id, project_id, status) VALUES (?, ?, ?)",
            (new_uuid(), new_uuid(), "COLLECTING_INTAKE"),
        )
        fresh_conn.commit()

    def test_artifacts_new_v2_type_accepted(self, fresh_conn):
        """INTAKE_BRIEF is a v2 artifact_type."""
        apply_schema(fresh_conn)
        rid = new_uuid()
        fresh_conn.execute(
            "INSERT INTO runs (run_id, project_id, status) VALUES (?, ?, ?)",
            (rid, new_uuid(), "CREATED"),
        )
        # CHECK uq_artifacts_one_active_per_type: status=ACTIVE constrained
        fresh_conn.execute(
            "INSERT INTO artifacts "
            "(artifact_id, run_id, artifact_type, version, status, content_ref, content_checksum) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (new_uuid(), rid, "INTAKE_BRIEF", 1, "DRAFT", "/tmp/a", "deadbeef"),
        )
        fresh_conn.commit()

    def test_artifacts_invalid_type_rejected(self, fresh_conn):
        import sqlite3 as sq3
        apply_schema(fresh_conn)
        rid = new_uuid()
        fresh_conn.execute(
            "INSERT INTO runs (run_id, project_id, status) VALUES (?, ?, ?)",
            (rid, new_uuid(), "CREATED"),
        )
        with pytest.raises(sq3.IntegrityError):
            fresh_conn.execute(
                "INSERT INTO artifacts "
                "(artifact_id, run_id, artifact_type, version, status, content_ref, content_checksum) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (new_uuid(), rid, "MADE_UP_TYPE", 1, "DRAFT", "/tmp/x", "abc"),
            )

    def test_one_active_artifact_per_type_invariant(self, fresh_conn):
        """Partial unique index prevents 2 ACTIVE artifacts of same type per run."""
        import sqlite3 as sq3
        apply_schema(fresh_conn)
        rid = new_uuid()
        fresh_conn.execute(
            "INSERT INTO runs (run_id, project_id, status) VALUES (?, ?, ?)",
            (rid, new_uuid(), "CREATED"),
        )
        fresh_conn.execute(
            "INSERT INTO artifacts "
            "(artifact_id, run_id, artifact_type, version, status, content_ref, content_checksum) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (new_uuid(), rid, "INITIAL_BRIEF", 1, "ACTIVE", "/tmp/a", "h1"),
        )
        # Second ACTIVE of same type → fail
        with pytest.raises(sq3.IntegrityError):
            fresh_conn.execute(
                "INSERT INTO artifacts "
                "(artifact_id, run_id, artifact_type, version, status, content_ref, content_checksum) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (new_uuid(), rid, "INITIAL_BRIEF", 2, "ACTIVE", "/tmp/b", "h2"),
            )

    def test_fk_artifacts_run_id_enforced(self, fresh_conn):
        import sqlite3 as sq3
        apply_schema(fresh_conn)
        with pytest.raises(sq3.IntegrityError):
            fresh_conn.execute(
                "INSERT INTO artifacts "
                "(artifact_id, run_id, artifact_type, version, status, content_ref, content_checksum) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (new_uuid(), "no-such-run", "INITIAL_BRIEF", 1, "DRAFT", "/x", "h"),
            )

    def test_json_validation_on_metadata(self, fresh_conn):
        import sqlite3 as sq3
        apply_schema(fresh_conn)
        rid = new_uuid()
        with pytest.raises(sq3.IntegrityError):
            fresh_conn.execute(
                "INSERT INTO runs (run_id, project_id, status, metadata) VALUES (?, ?, ?, ?)",
                (rid, new_uuid(), "CREATED", "not-json{"),
            )

    def test_default_current_artifacts_valid_json(self, fresh_conn):
        apply_schema(fresh_conn)
        rid = new_uuid()
        fresh_conn.execute(
            "INSERT INTO runs (run_id, project_id) VALUES (?, ?)",
            (rid, new_uuid()),
        )
        row = fresh_conn.execute(
            "SELECT current_artifacts FROM runs WHERE run_id = ?", (rid,)
        ).fetchone()
        parsed = json.loads(row["current_artifacts"])
        assert "initial_brief_id" in parsed
        assert "spec_bundle_id" in parsed


class TestEvents:
    def test_event_v2_types_accepted(self, fresh_conn):
        apply_schema(fresh_conn)
        rid = new_uuid()
        fresh_conn.execute(
            "INSERT INTO runs (run_id, project_id, status) VALUES (?, ?, ?)",
            (rid, new_uuid(), "CREATED"),
        )
        # All v2 event types must validate
        for evt in [
            "INTAKE_STARTED", "INTAKE_COMPLETED", "QUESTION_INVENTORY_GENERATED",
            "CRITIC_ITERATION_DONE", "MODERATOR_PARSE_FAIL", "BRIEF_EDIT_APPLIED",
        ]:
            fresh_conn.execute(
                "INSERT INTO events (event_id, run_id, event_type) VALUES (?, ?, ?)",
                (new_uuid(), rid, evt),
            )
        fresh_conn.commit()


class TestEscalations:
    def test_escalation_create(self, fresh_conn):
        apply_schema(fresh_conn)
        rid = new_uuid()
        tr_id = new_uuid()
        art_id = new_uuid()

        fresh_conn.execute(
            "INSERT INTO runs (run_id, project_id, status) VALUES (?, ?, ?)",
            (rid, new_uuid(), "CREATED"),
        )
        # Need task_graph_artifact_id → create an artifact first
        fresh_conn.execute(
            "INSERT INTO artifacts "
            "(artifact_id, run_id, artifact_type, version, status, content_ref, content_checksum) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (art_id, rid, "TASK_GRAPH_APPROVED", 1, "ACTIVE", "/x", "h"),
        )
        fresh_conn.execute(
            "INSERT INTO task_runs "
            "(task_run_id, run_id, task_id, task_graph_artifact_id) "
            "VALUES (?, ?, ?, ?)",
            (tr_id, rid, "TASK-1", art_id),
        )
        fresh_conn.execute(
            "INSERT INTO escalations "
            "(escalation_id, run_id, task_run_id, reason, options) "
            "VALUES (?, ?, ?, ?, ?)",
            (new_uuid(), rid, tr_id, "test", dump_json([])),
        )
        fresh_conn.commit()

    def test_escalation_dedup(self, fresh_conn):
        import sqlite3 as sq3
        apply_schema(fresh_conn)
        rid = new_uuid()
        tr_id = new_uuid()
        art_id = new_uuid()
        fresh_conn.execute(
            "INSERT INTO runs (run_id, project_id, status) VALUES (?, ?, ?)",
            (rid, new_uuid(), "CREATED"),
        )
        fresh_conn.execute(
            "INSERT INTO artifacts "
            "(artifact_id, run_id, artifact_type, version, status, content_ref, content_checksum) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (art_id, rid, "TASK_GRAPH_APPROVED", 1, "ACTIVE", "/x", "h"),
        )
        fresh_conn.execute(
            "INSERT INTO task_runs "
            "(task_run_id, run_id, task_id, task_graph_artifact_id) "
            "VALUES (?, ?, ?, ?)",
            (tr_id, rid, "TASK-1", art_id),
        )
        # First OPEN escalation
        fresh_conn.execute(
            "INSERT INTO escalations "
            "(escalation_id, run_id, task_run_id, reason, options, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (new_uuid(), rid, tr_id, "same-reason", dump_json([]), "OPEN"),
        )
        fresh_conn.commit()
        # Second OPEN with same reason → UNIQUE constraint hit
        with pytest.raises(sq3.IntegrityError):
            fresh_conn.execute(
                "INSERT INTO escalations "
                "(escalation_id, run_id, task_run_id, reason, options, status) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (new_uuid(), rid, tr_id, "same-reason", dump_json([]), "OPEN"),
            )
