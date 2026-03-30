"""CLI entry point for Phase 1a: normalize → debate → PAUSED_AT_GATE_1."""
from __future__ import annotations

import argparse
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


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run Phase A debate pipeline.")
    parser.add_argument("--project-name", default="", dest="project_name")
    parser.add_argument("--idea", default="")
    parser.add_argument("--constraints", default="")
    return parser.parse_args(argv)


def _validate(args) -> list[str]:
    errors = []
    if not args.project_name.strip():
        errors.append("Error: --project-name must be non-empty")
    if not args.idea.strip():
        errors.append("Error: --idea must be non-empty")
    return errors


def main(argv=None) -> int:
    args = _parse_args(argv)
    errors = _validate(args)
    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        return 1
    # (pipeline call will go here in Task 3)
    return 0


if __name__ == "__main__":
    sys.exit(main())
