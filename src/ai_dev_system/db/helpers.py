"""DB helpers for JSON column read/write + array column conversion.

SQLite stores JSON as TEXT. These helpers centralize parsing/serialization so
repos don't sprinkle json.loads/dumps everywhere.

Conventions:
- JSON object columns:  on read → dict, on write → TEXT
- Array columns (TEXT containing JSON array): on read → list, on write → TEXT
- Boolean columns (INTEGER 0/1): on read → bool, on write → 0/1
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any


# ============================================================================
# JSON column helpers
# ============================================================================

def load_json(value: str | None, default: Any = None) -> Any:
    """Parse a JSON TEXT column. None/empty returns default."""
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value  # already parsed (in-memory fixture etc)
    return json.loads(value)


def dump_json(value: Any) -> str:
    """Serialize a Python value to JSON TEXT for storage."""
    if value is None:
        return "null"
    return json.dumps(value, ensure_ascii=False, default=str)


def parse_row_json(row: sqlite3.Row | dict | None, *cols: str) -> dict[str, Any] | None:
    """Convert a row to dict, parsing specified columns as JSON.

    Example:
        row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (rid,)).fetchone()
        parsed = parse_row_json(row, "current_artifacts", "timeout_policy", "metadata")
        parsed["current_artifacts"]  # → dict, not str
    """
    if row is None:
        return None
    if isinstance(row, sqlite3.Row):
        d = {k: row[k] for k in row.keys()}
    else:
        d = dict(row)
    for col in cols:
        if col in d and d[col] is not None:
            try:
                d[col] = json.loads(d[col])
            except (TypeError, json.JSONDecodeError):
                # Leave as-is if already parsed or malformed (defensive)
                pass
    return d


# ============================================================================
# UUID
# ============================================================================

def new_uuid() -> str:
    """Generate a fresh UUID string (no dashes, lowercase hex)."""
    return uuid.uuid4().hex


# ============================================================================
# Timestamps — SQLite stores as ISO 8601 TEXT (UTC)
# ============================================================================

def utc_now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_iso(s: str | None) -> datetime | None:
    """Parse an ISO timestamp string from a DB row. None passes through."""
    if s is None:
        return None
    # SQLite CURRENT_TIMESTAMP returns "YYYY-MM-DD HH:MM:SS" (space, no TZ).
    # Convert to ISO 8601 with UTC suffix.
    if " " in s and "T" not in s:
        s = s.replace(" ", "T") + "+00:00"
    return datetime.fromisoformat(s)


# ============================================================================
# Boolean
# ============================================================================

def to_db_bool(v: bool | int | None) -> int | None:
    """Convert Python bool to SQLite INTEGER (0/1)."""
    if v is None:
        return None
    return 1 if v else 0


def from_db_bool(v: int | bool | None) -> bool | None:
    """Convert SQLite INTEGER (0/1) to Python bool."""
    if v is None:
        return None
    return bool(v)


# ============================================================================
# Array column read helper
# ============================================================================

def load_array(value: str | None, default: list | None = None) -> list:
    """Parse a TEXT-encoded JSON array column.

    Returns empty list if value is NULL/empty, unless default is given.
    """
    if value is None or value == "":
        return [] if default is None else default
    if isinstance(value, list):
        return value
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        raise ValueError(f"Expected JSON array, got {type(parsed).__name__}: {parsed!r}")
    return parsed
