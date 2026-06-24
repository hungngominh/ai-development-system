"""Regression: `ai-dev start` must COMMIT the run so it survives conn.close().

get_connection() opens the SQLite connection with autocommit OFF. Before the
fix, the (deprecated) start command ran the whole debate pipeline but never
committed, so on conn.close() the run + artifact rows were rolled back — the
on-disk artifacts survived but `ai-dev info` and Gate 1 review could not find
the run on a fresh connection.

Uses the stub LLM (AI_DEV_STUB_LLM=1) so no real model calls are made.
"""
import pytest

from ai_dev_system.db.connection import get_connection


@pytest.mark.integration
def test_start_persists_run_across_connections(tmp_path, file_db_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", file_db_url)
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.setenv("AI_DEV_STUB_LLM", "1")

    from ai_dev_system.cli.start_project import main

    rc = main(["--project-name", "persist-check", "--idea", "an idea worth debating"])
    assert rc == 0

    # Fresh connection: only COMMITTED rows are visible here.
    verify = get_connection(file_db_url)
    try:
        statuses = [r["status"] for r in verify.execute("SELECT status FROM runs").fetchall()]
        artifact_types = [
            r["artifact_type"]
            for r in verify.execute("SELECT artifact_type FROM artifacts").fetchall()
        ]
    finally:
        verify.close()

    assert "PAUSED_AT_GATE_1" in statuses, f"run was not committed; statuses={statuses}"
    assert "DEBATE_REPORT" in artifact_types, f"artifact not committed; types={artifact_types}"
