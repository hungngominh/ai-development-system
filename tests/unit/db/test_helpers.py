"""Tests for db helpers (JSON, UUID, timestamps, booleans, arrays)."""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from ai_dev_system.db.helpers import (
    dump_json,
    from_db_bool,
    load_array,
    load_json,
    new_uuid,
    parse_iso,
    parse_row_json,
    to_db_bool,
    utc_now_iso,
)


class TestJson:
    def test_load_json_none(self):
        assert load_json(None) is None
        assert load_json("") is None

    def test_load_json_default(self):
        assert load_json(None, default={}) == {}
        assert load_json("", default=[]) == []

    def test_load_json_passthrough(self):
        """Already-parsed dicts/lists pass through."""
        assert load_json({"a": 1}) == {"a": 1}
        assert load_json([1, 2]) == [1, 2]

    def test_load_json_object(self):
        assert load_json('{"a": 1, "b": "x"}') == {"a": 1, "b": "x"}

    def test_load_json_array(self):
        assert load_json("[1, 2, 3]") == [1, 2, 3]

    def test_dump_json_none(self):
        assert dump_json(None) == "null"

    def test_dump_json_dict(self):
        assert json.loads(dump_json({"a": 1})) == {"a": 1}

    def test_dump_json_unicode(self):
        out = dump_json({"name": "Việt Nam"})
        assert "Việt Nam" in out  # ensure_ascii=False

    def test_dump_json_handles_unknown_types(self):
        from uuid import UUID
        u = UUID("12345678-1234-5678-1234-567812345678")
        out = dump_json({"id": u})
        assert "12345678" in out  # default=str fallback


class TestParseRowJson:
    def test_none(self):
        assert parse_row_json(None, "x") is None

    def test_dict_input(self):
        row = {"a": 1, "data": '{"x": 2}'}
        out = parse_row_json(row, "data")
        assert out["a"] == 1
        assert out["data"] == {"x": 2}

    def test_missing_column(self):
        row = {"a": 1}
        out = parse_row_json(row, "nonexistent")
        assert out == {"a": 1}

    def test_null_column(self):
        row = {"a": 1, "data": None}
        out = parse_row_json(row, "data")
        assert out["data"] is None

    def test_multiple_columns(self):
        row = {"a": '{"x": 1}', "b": "[1,2]", "c": "plain"}
        out = parse_row_json(row, "a", "b")
        assert out["a"] == {"x": 1}
        assert out["b"] == [1, 2]
        assert out["c"] == "plain"  # untouched

    def test_malformed_json_kept_as_is(self):
        row = {"data": "not valid json"}
        out = parse_row_json(row, "data")
        assert out["data"] == "not valid json"


class TestUuid:
    def test_format(self):
        u = new_uuid()
        assert len(u) == 32
        assert "-" not in u
        int(u, 16)  # valid hex

    def test_uniqueness(self):
        s = {new_uuid() for _ in range(100)}
        assert len(s) == 100


class TestTimestamps:
    def test_utc_now_iso_format(self):
        ts = utc_now_iso()
        # ISO 8601 with timezone suffix
        assert "T" in ts
        assert "+00:00" in ts or ts.endswith("Z")

    def test_parse_iso_none(self):
        assert parse_iso(None) is None

    def test_parse_iso_8601(self):
        dt = parse_iso("2026-05-23T10:30:00+00:00")
        assert isinstance(dt, datetime)
        assert dt.year == 2026

    def test_parse_sqlite_format(self):
        """SQLite CURRENT_TIMESTAMP returns 'YYYY-MM-DD HH:MM:SS' (space, no TZ)."""
        dt = parse_iso("2026-05-23 10:30:00")
        assert isinstance(dt, datetime)
        assert dt.year == 2026


class TestBool:
    def test_to_db_bool(self):
        assert to_db_bool(True) == 1
        assert to_db_bool(False) == 0
        assert to_db_bool(None) is None

    def test_from_db_bool(self):
        assert from_db_bool(1) is True
        assert from_db_bool(0) is False
        assert from_db_bool(None) is None


class TestArray:
    def test_load_array_none(self):
        assert load_array(None) == []

    def test_load_array_default(self):
        assert load_array(None, default=["x"]) == ["x"]

    def test_load_array_empty(self):
        assert load_array("") == []

    def test_load_array_normal(self):
        assert load_array('["a", "b"]') == ["a", "b"]

    def test_load_array_passthrough(self):
        assert load_array(["x", "y"]) == ["x", "y"]

    def test_load_array_non_array_raises(self):
        with pytest.raises(ValueError, match="Expected JSON array"):
            load_array('{"not": "array"}')
