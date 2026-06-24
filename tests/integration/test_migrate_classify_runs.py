"""Integration tests for `ai-dev migrate classify-runs` (S8)."""
from __future__ import annotations

import pytest
import typer

from ai_dev_system.db.helpers import dump_json, new_uuid


class _NonClosingConn:
    """Proxy whose .close() is a no-op so the CLI handler can't kill our
    in-memory test DB before assertions run."""

    def __init__(self, conn):
        self._c = conn

    def close(self):
        return None

    def __getattr__(self, name):
        return getattr(self._c, name)


def _seed(conn, *, status, pipeline_version=1, legacy=0):
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


def _patch_cli_db(monkeypatch, conn, config):
    proxy = _NonClosingConn(conn)
    monkeypatch.setattr("ai_dev_system.config.Config.from_env", staticmethod(lambda: config))
    monkeypatch.setattr("ai_dev_system.db.connection.get_connection", lambda _url: proxy)
    monkeypatch.setattr("ai_dev_system.db.migrator.apply_schema", lambda _c: None)


def test_classify_runs_command_writes_audit_for_each_run(conn, config, monkeypatch):
    from ai_dev_system.cli.commands.migrate import migrate_classify_runs

    _seed(conn, status="COMPLETED", pipeline_version=1, legacy=1)
    _seed(conn, status="COLLECTING_INTAKE", pipeline_version=2, legacy=0)
    _seed(conn, status="READY_FOR_DEBATE", pipeline_version=2, legacy=0)
    conn.commit()

    _patch_cli_db(monkeypatch, conn, config)

    with pytest.raises(typer.Exit) as exc_info:
        migrate_classify_runs(json_output=True, quiet=False, dry_run=False)
    assert exc_info.value.exit_code == 0

    rows = conn.execute(
        "SELECT classification FROM migration_audit ORDER BY classification"
    ).fetchall()
    classifications = sorted(r["classification"] for r in rows)
    assert classifications == ["v1_continue", "v2_new", "v2_resume"]


def test_classify_runs_dry_run_does_not_write_audit(conn, config, monkeypatch):
    from ai_dev_system.cli.commands.migrate import migrate_classify_runs

    _seed(conn, status="COMPLETED", pipeline_version=1, legacy=1)
    conn.commit()

    _patch_cli_db(monkeypatch, conn, config)

    with pytest.raises(typer.Exit) as exc_info:
        migrate_classify_runs(json_output=True, quiet=False, dry_run=True)
    assert exc_info.value.exit_code == 0

    count = conn.execute("SELECT COUNT(*) AS n FROM migration_audit").fetchone()["n"]
    assert count == 0


def test_classify_runs_command_idempotent(conn, config, monkeypatch):
    from ai_dev_system.cli.commands.migrate import migrate_classify_runs

    _seed(conn, status="COMPLETED", pipeline_version=1, legacy=1)
    conn.commit()
    _patch_cli_db(monkeypatch, conn, config)

    for _ in range(2):
        with pytest.raises(typer.Exit) as exc_info:
            migrate_classify_runs(json_output=True, quiet=False, dry_run=False)
        assert exc_info.value.exit_code == 0

    count = conn.execute("SELECT COUNT(*) AS n FROM migration_audit").fetchone()["n"]
    assert count == 1
