"""CLIContext — shared state passed to every command."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ai_dev_system.cli.core.output import OutputRenderer

if TYPE_CHECKING:
    from ai_dev_system.config import Config


@dataclass
class CLIContext:
    """Per-invocation context for all CLI commands.

    Constructed by the root callback in `main.py` from parsed global flags.
    Passed (via typer Context or explicit arg) into every subcommand.
    """

    output: OutputRenderer
    quiet: bool = False
    verbose_level: int = 0
    dry_run: bool = False
    config_path: str | None = None
    feature_overrides: dict[str, bool] = field(default_factory=dict)

    # Lazy-init: only when subcommand needs them
    _config: "Config | None" = None
    _conn: Any = None  # sqlite3.Connection, lazy

    @property
    def config(self) -> "Config":
        """Lazy-load config on first access."""
        if self._config is None:
            from ai_dev_system.config import Config
            self._config = Config.from_env()
        return self._config

    @property
    def conn(self) -> Any:
        """Lazy-open DB connection on first access."""
        if self._conn is None:
            from ai_dev_system.db.connection import get_connection
            self._conn = get_connection(self.config.database_url)
        return self._conn

    def close(self) -> None:
        """Close connection if opened. Called at command end."""
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
