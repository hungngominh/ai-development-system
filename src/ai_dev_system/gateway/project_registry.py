"""In-process cache of per-project data resources (paths + live DB connection).

One shared connection per project, mirroring the daemon's single-threaded
one-connection model. Built lazily on first get(); closed via close_all() at
daemon shutdown.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable
import sqlite3

from ai_dev_system.config import ProjectPaths, resolve_project
from ai_dev_system.db.connection import get_connection
from ai_dev_system.assistant.run_links import RunLinkStore
from ai_dev_system.assistant.session import SessionStore
from ai_dev_system.assistant.budget import BudgetTracker


@dataclass(frozen=True)
class ProjectResources:
    paths: ProjectPaths
    conn: sqlite3.Connection
    conn_factory: Callable[[], sqlite3.Connection]
    link_store: RunLinkStore
    session_store: SessionStore
    budget: BudgetTracker


class ProjectRegistry:
    """Lazily resolve + cache per-repo data resources."""

    def __init__(self) -> None:
        self._cache: dict[str, ProjectResources] = {}

    def get(self, repo_path: str) -> ProjectResources:
        key = os.path.abspath(str(repo_path).strip())
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        paths = resolve_project(key, ensure=True)
        conn = get_connection(paths.database_url)

        def _cf() -> sqlite3.Connection:
            return conn

        res = ProjectResources(
            paths=paths,
            conn=conn,
            conn_factory=_cf,
            link_store=RunLinkStore(_cf),
            session_store=SessionStore(_cf),
            budget=BudgetTracker(_cf),
        )
        self._cache[key] = res
        return res

    def close_all(self) -> None:
        for res in self._cache.values():
            try:
                res.conn.close()
            except Exception:  # noqa: BLE001 — shutdown best-effort
                pass
        self._cache.clear()
