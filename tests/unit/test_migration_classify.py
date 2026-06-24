"""Unit tests for the Phase 1 v2 run classifier (S8)."""
from __future__ import annotations

import pytest

from ai_dev_system.db.helpers import dump_json, new_uuid
from ai_dev_system.migration.classify import (
    V5_MIGRATION_VERSION,
    classify_all_runs,
    classify_run,
    is_legacy_run,
    summarise,
    write_audit,
)


def _seed_run(conn, *, status="CREATED", pipeline_version=1, legacy=0):
    rid = new_uuid()
    conn.execute(
        """
        INSERT INTO runs (run_id, project_id, status, title, current_artifacts,
                          metadata, pipeline_version, legacy)
        VALUES (?, ?, ?, 'T', ?, '{}', ?, ?)
        """,
        (rid, "p1", status, dump_json({}), pipeline_version, legacy),
    )
    return rid


@pytest.mark.parametrize(
    "status,pv,legacy,expected",
    [
        ("CREATED",       1, 1, "v1_continue"),
        ("COMPLETED",     1, 1, "v1_continue"),
        ("FAILED",        1, 1, "v1_continue"),
        ("ABORTED",       1, 0, "v1_continue"),
        ("RUNNING_PHASE_1A", 1, 0, "v1_continue"),
        ("COLLECTING_INTAKE", 2, 0, "v2_resume"),
        ("READY_FOR_DEBATE",  2, 0, "v2_new"),
        ("COMPLETED",         2, 0, "v2_new"),
    ],
)
def test_classify_run_truth_table(status, pv, legacy, expected):
    row = {"run_id": "R", "status": status, "pipeline_version": pv, "legacy": legacy}
    assert classify_run(row).classification == expected


def test_classify_run_unknown_pipeline_version_routes_to_abort():
    row = {"run_id": "R", "status": "CREATED", "pipeline_version": 99, "legacy": 0}
    assert classify_run(row).classification == "abort"


def test_classify_run_missing_pipeline_version_defaults_to_v1():
    """If pipeline_version comes back NULL (very old row), treat as v1."""
    row = {"run_id": "R", "status": "CREATED", "pipeline_version": None, "legacy": 0}
    assert classify_run(row).classification == "v1_continue"


def test_classify_all_runs_writes_audit(conn):
    rid_old = _seed_run(conn, status="COMPLETED", pipeline_version=1, legacy=1)
    rid_paused = _seed_run(conn, status="COLLECTING_INTAKE", pipeline_version=2, legacy=0)
    rid_new = _seed_run(conn, status="READY_FOR_DEBATE", pipeline_version=2, legacy=0)

    classified = classify_all_runs(conn)
    conn.commit()

    by_id = {c.run_id: c.classification for c in classified}
    assert by_id[rid_old] == "v1_continue"
    assert by_id[rid_paused] == "v2_resume"
    assert by_id[rid_new] == "v2_new"

    audit_rows = conn.execute(
        "SELECT run_id, classification, migration_version FROM migration_audit"
    ).fetchall()
    assert len(audit_rows) == 3
    assert all(r["migration_version"] == V5_MIGRATION_VERSION for r in audit_rows)


def test_classify_all_runs_is_idempotent(conn):
    """Re-running the classifier must not double-insert audit rows."""
    _seed_run(conn, status="COMPLETED", pipeline_version=1, legacy=1)
    classify_all_runs(conn); conn.commit()
    classify_all_runs(conn); conn.commit()

    count = conn.execute("SELECT COUNT(*) AS n FROM migration_audit").fetchone()["n"]
    assert count == 1


def test_write_audit_skips_existing_row(conn):
    rid = _seed_run(conn, status="COMPLETED", pipeline_version=1, legacy=1)
    c = classify_run({
        "run_id": rid, "status": "COMPLETED", "pipeline_version": 1, "legacy": 1,
    })
    write_audit(conn, c)
    write_audit(conn, c)  # second call must be no-op
    conn.commit()

    count = conn.execute(
        "SELECT COUNT(*) AS n FROM migration_audit WHERE run_id = ?", (rid,)
    ).fetchone()["n"]
    assert count == 1


def test_is_legacy_run_true_when_legacy_flag_set(conn):
    rid = _seed_run(conn, pipeline_version=2, legacy=1)  # flagged manually
    assert is_legacy_run(conn, rid) is True


def test_is_legacy_run_true_when_pipeline_version_is_1(conn):
    rid = _seed_run(conn, pipeline_version=1, legacy=0)
    assert is_legacy_run(conn, rid) is True


def test_is_legacy_run_false_when_v2(conn):
    rid = _seed_run(conn, pipeline_version=2, legacy=0)
    assert is_legacy_run(conn, rid) is False


def test_is_legacy_run_unknown_id_returns_false(conn):
    assert is_legacy_run(conn, "does-not-exist") is False


def test_load_intake_brief_short_circuits_for_legacy_runs(conn, tmp_path):
    """Dispatcher guard: even if a legacy run somehow has an intake_brief_id set,
    `_load_intake_brief` returns None so finalize_spec stays on the v1 path."""
    import json
    from pathlib import Path
    from ai_dev_system.debate_pipeline import _load_intake_brief

    # Seed a legacy run with a "stray" intake_brief_id pointing at a real artifact
    rid = _seed_run(conn, status="COMPLETED", pipeline_version=1, legacy=1)
    aid = new_uuid()
    art_dir = tmp_path / "brief"
    art_dir.mkdir()
    (art_dir / "brief.json").write_text(json.dumps({"brief_version": 2}), encoding="utf-8")
    conn.execute(
        """
        INSERT INTO artifacts (artifact_id, run_id, artifact_type, version, status,
                               created_by, input_artifact_ids, content_ref,
                               content_checksum, content_size)
        VALUES (?, ?, 'INTAKE_BRIEF', 1, 'ACTIVE', 'system', '[]', ?, 'x', 0)
        """,
        (aid, rid, str(art_dir)),
    )
    conn.execute(
        "UPDATE runs SET intake_brief_id = ? WHERE run_id = ?", (aid, rid),
    )
    conn.commit()

    assert _load_intake_brief(conn, rid) is None


def test_summarise_counts_each_classification():
    rows = [
        {"run_id": "a", "status": "COMPLETED",          "pipeline_version": 1, "legacy": 1},
        {"run_id": "b", "status": "COMPLETED",          "pipeline_version": 1, "legacy": 1},
        {"run_id": "c", "status": "COLLECTING_INTAKE",  "pipeline_version": 2, "legacy": 0},
        {"run_id": "d", "status": "READY_FOR_DEBATE",   "pipeline_version": 2, "legacy": 0},
        {"run_id": "e", "status": "CREATED",            "pipeline_version": 99, "legacy": 0},
    ]
    classified = [classify_run(r) for r in rows]
    counts = summarise(classified)
    assert counts == {"v1_continue": 2, "v2_resume": 1, "v2_new": 1, "abort": 1}
