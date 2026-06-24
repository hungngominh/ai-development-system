"""Tests for SQLite connection layer."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ai_dev_system.db.connection import (
    _parse_sqlite_url,
    get_connection,
    row_to_dict,
    rows_to_dicts,
)


class TestParseSqliteUrl:
    def test_memory(self):
        assert str(_parse_sqlite_url("sqlite:///:memory:")) == ":memory:"

    def test_three_slashes_is_relative(self):
        """SQLAlchemy convention: sqlite:///path = relative path."""
        p = _parse_sqlite_url("sqlite:///tmp/test.db")
        assert str(p) in ("tmp/test.db", "tmp\\test.db")  # platform-dep separator

    def test_four_slashes_is_absolute_unix(self):
        """SQLAlchemy convention: sqlite:////path = absolute on Unix."""
        p = _parse_sqlite_url("sqlite:////tmp/test.db")
        # On Windows this becomes \tmp\test.db (no drive letter); on Unix /tmp/test.db
        assert "tmp" in str(p) and "test.db" in str(p)

    def test_windows_absolute(self):
        p = _parse_sqlite_url("sqlite:///C:/Users/foo/db.sqlite")
        assert str(p).endswith("db.sqlite")

    def test_tilde_expanded(self):
        p = _parse_sqlite_url("sqlite:///~/test.db")
        assert "~" not in str(p)
        assert str(p).endswith("test.db")

    def test_relative(self):
        p = _parse_sqlite_url("sqlite://relative/path.db")
        assert "relative" in str(p)

    def test_wrong_scheme_raises(self):
        with pytest.raises(ValueError, match="SQLite only"):
            _parse_sqlite_url("postgresql://user@host/db")

    def test_empty_url_raises(self):
        with pytest.raises(ValueError):
            _parse_sqlite_url("")


class TestGetConnection:
    def test_in_memory(self):
        conn = get_connection("sqlite:///:memory:")
        row = conn.execute("SELECT 1 AS x").fetchone()
        assert row["x"] == 1
        conn.close()

    def test_foreign_keys_enabled(self):
        conn = get_connection("sqlite:///:memory:")
        fk = conn.execute("PRAGMA foreign_keys").fetchone()
        assert fk[0] == 1
        conn.close()

    def test_wal_mode(self, tmp_path):
        """In-memory ignores WAL; use a real file."""
        db_path = tmp_path / "wal.db"
        conn = get_connection(f"sqlite:///{db_path}")
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
        conn.close()

    def test_creates_parent_directory(self, tmp_path):
        nested = tmp_path / "a" / "b" / "c.db"
        assert not nested.parent.exists()
        conn = get_connection(f"sqlite:///{nested}")
        assert nested.parent.exists()
        conn.close()

    def test_row_factory_dict_like(self):
        conn = get_connection("sqlite:///:memory:")
        conn.execute("CREATE TABLE t (id INTEGER, name TEXT)")
        conn.execute("INSERT INTO t VALUES (?, ?)", (1, "alice"))
        row = conn.execute("SELECT * FROM t").fetchone()
        assert row["id"] == 1
        assert row["name"] == "alice"
        conn.close()


class TestRowConverters:
    def test_row_to_dict_none(self):
        assert row_to_dict(None) is None

    def test_row_to_dict_basic(self):
        conn = get_connection("sqlite:///:memory:")
        conn.execute("CREATE TABLE t (a INTEGER, b TEXT)")
        conn.execute("INSERT INTO t VALUES (1, 'x')")
        row = conn.execute("SELECT * FROM t").fetchone()
        d = row_to_dict(row)
        assert d == {"a": 1, "b": "x"}
        conn.close()

    def test_rows_to_dicts(self):
        conn = get_connection("sqlite:///:memory:")
        conn.execute("CREATE TABLE t (a INTEGER)")
        conn.executemany("INSERT INTO t VALUES (?)", [(1,), (2,), (3,)])
        rows = conn.execute("SELECT * FROM t ORDER BY a").fetchall()
        out = rows_to_dicts(rows)
        assert out == [{"a": 1}, {"a": 2}, {"a": 3}]
        conn.close()
