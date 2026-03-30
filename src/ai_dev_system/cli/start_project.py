"""CLI entry point for Phase 1a: normalize → debate → PAUSED_AT_GATE_1."""
from __future__ import annotations

import json
import re
import sys
import uuid


def name_to_slug(name: str) -> str:
    """Convert a project name to a URL-safe slug (max 40 chars)."""
    s = name.strip().lower()
    # Strip diacritics: prefer unidecode, fall back to ascii-ignore
    try:
        from unidecode import unidecode  # type: ignore
        s = unidecode(s)
    except ImportError:
        s = s.encode("ascii", "ignore").decode()
    # Replace non-alphanumeric runs with a single dash
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:40]


def make_project_id(slug: str) -> str:
    """Deterministic UUID from slug (uuid5). Same slug → same UUID."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, slug))
