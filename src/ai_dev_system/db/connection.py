"""SQLite-backed connection layer.

Replaces psycopg with sqlite3 (stdlib) for zero-install local dev.

Public API matches what existing repos use:
    conn = get_connection(database_url)
    row = conn.execute("SELECT ... WHERE x = ?", (val,)).fetchone()
    row["col"]               # dict-like access
    conn.commit()
    conn.close()

Differences from psycopg backend:
- Parameter style is `?` (not `%s`)
- JSON columns return TEXT; use helpers in db/helpers.py to parse/dump
- UUIDs generated app-side; pass uuid.uuid4().hex strings
- Foreign keys enforced via PRAGMA foreign_keys=ON (set on every connection)
- WAL mode enabled by default for better concurrency
"""
from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Any


SQLITE_SCHEME_PATTERN = re.compile(r"^sqlite:(///?)(.+)$")


def _parse_sqlite_url(url: str) -> Path:
    """Parse a SQLite URL into a filesystem path.

    Accepted forms:
        sqlite:///absolute/path.db           (Unix)
        sqlite:///C:/Users/foo/file.db       (Windows absolute)
        sqlite:///~/.ai-dev-system/store.db  (~ expanded)
        sqlite://relative/path.db            (relative — discouraged but supported)
        sqlite:///:memory:                   (in-memory, for tests)

    Raises ValueError if URL is not a sqlite:// scheme.
    """
    m = SQLITE_SCHEME_PATTERN.match(url)
    if not m:
        raise ValueError(
            f"Unsupported DATABASE_URL: {url!r}. "
            f"This system uses SQLite only — URL must start with 'sqlite://'."
        )
    raw_path = m.group(2)

    # In-memory special case
    if raw_path in (":memory:", "/:memory:"):
        return Path(":memory:")

    # Expand ~ and env vars
    raw_path = os.path.expandvars(os.path.expanduser(raw_path))
    return Path(raw_path)


def get_connection(database_url: str) -> sqlite3.Connection:
    """Open a SQLite connection.

    Side-effects:
    - Creates parent directories if path is absolute and doesn't exist (except :memory:)
    - Enables foreign keys (FK constraints OFF by default in SQLite)
    - Enables WAL journal mode (better concurrent reads)
    - Sets row_factory to sqlite3.Row (dict-like access)
    """
    path = _parse_sqlite_url(database_url)

    if str(path) != ":memory:":
        # Ensure parent dir exists
        path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(
        str(path) if str(path) != ":memory:" else ":memory:",
        # Detect TEXT timestamps if needed; we manage time as ISO strings explicitly
        detect_types=0,
        # Allow use across threads (CLI is single-thread anyway, but tests sometimes share)
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row

    # Enforce FKs (off by default in SQLite!)
    conn.execute("PRAGMA foreign_keys = ON")
    # Better concurrency for our read-heavy workload
    conn.execute("PRAGMA journal_mode = WAL")
    # Wait up to 5s if DB is locked by another writer
    conn.execute("PRAGMA busy_timeout = 5000")

    return conn


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    """Convert a sqlite3.Row to plain dict (None passes through)."""
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    """Convert list of Rows to list of dicts."""
    return [row_to_dict(r) for r in rows]
