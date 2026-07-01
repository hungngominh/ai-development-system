import sqlite3
from pathlib import Path

import pytest

from ai_dev_system.gateway.project_registry import ProjectRegistry, ProjectResources


def test_get_returns_usable_resources(tmp_path):
    reg = ProjectRegistry()
    try:
        res = reg.get(str(tmp_path / "repo"))
        assert isinstance(res, ProjectResources)
        # schema applied → can query a control-layer table
        rows = res.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='runs'"
        ).fetchall()
        assert len(rows) == 1
        # conn_factory returns the SAME cached connection
        assert res.conn_factory() is res.conn
    finally:
        reg.close_all()


def test_get_caches_per_repo(tmp_path):
    reg = ProjectRegistry()
    try:
        r1 = reg.get(str(tmp_path / "repo"))
        r2 = reg.get(str(tmp_path / "repo"))
        assert r1 is r2  # same cached ProjectResources
    finally:
        reg.close_all()


def test_two_repos_independent_dbs(tmp_path):
    reg = ProjectRegistry()
    try:
        a = reg.get(str(tmp_path / "a"))
        b = reg.get(str(tmp_path / "b"))
        assert a.conn is not b.conn
        assert a.paths.database_url != b.paths.database_url
        assert Path(a.paths.root, "control.db").exists()
        assert Path(b.paths.root, "control.db").exists()
    finally:
        reg.close_all()


def test_close_all_closes_conns_and_is_safe_twice(tmp_path):
    reg = ProjectRegistry()
    res = reg.get(str(tmp_path / "repo"))
    reg.close_all()
    with pytest.raises(sqlite3.ProgrammingError):
        res.conn.execute("SELECT 1")
    reg.close_all()  # second call must not raise
