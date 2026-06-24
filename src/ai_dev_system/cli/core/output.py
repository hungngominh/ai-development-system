"""Output renderer: JSON mode (single-line stdout) + human mode (rich)."""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Any, Literal

from rich.console import Console


@dataclass
class OutputRenderer:
    """Renders CLI output in human or JSON mode.

    JSON mode (decision: CLI spec):
      - Final result: single-line JSON to stdout
      - Progress / warnings: human-readable to stderr
      - No mixed output on stdout

    Human mode (default):
      - Progress to stderr (with rich color if TTY)
      - Final result to stdout (human-readable, parseable last line)
    """

    mode: Literal["human", "json"] = "human"
    quiet: bool = False
    no_color: bool = False
    _stderr: Console = field(init=False)
    _stdout: Console = field(init=False)

    def __post_init__(self) -> None:
        # stderr always interactive (for progress); stdout depends on mode
        self._stderr = Console(stderr=True, no_color=self.no_color or self.mode == "json")
        self._stdout = Console(no_color=self.no_color or self.mode == "json")

    # ------------------------------------------------------------
    # Progress + warnings (always to stderr)
    # ------------------------------------------------------------

    def progress(self, message: str) -> None:
        """User-visible progress, suppressed if quiet."""
        if self.quiet:
            return
        self._stderr.print(message)

    def info(self, message: str) -> None:
        """Informational message to stderr."""
        if self.quiet:
            return
        self._stderr.print(f"[cyan]ℹ[/cyan] {message}")

    def warn(self, message: str) -> None:
        """Warning to stderr — always shown."""
        self._stderr.print(f"[yellow]⚠[/yellow]  {message}")

    def error(self, message: str) -> None:
        """Error to stderr — always shown."""
        self._stderr.print(f"[red]✗[/red] {message}")

    def success(self, message: str) -> None:
        """Success message to stderr, suppressed if quiet."""
        if self.quiet:
            return
        self._stderr.print(f"[green]✓[/green] {message}")

    # ------------------------------------------------------------
    # Final result (to stdout)
    # ------------------------------------------------------------

    def write(self, payload: dict[str, Any]) -> None:
        """Write final result. Format depends on mode."""
        if self.mode == "json":
            # Single-line JSON, no trailing newline beyond print's default
            print(json.dumps(payload, ensure_ascii=False), file=sys.stdout)
        else:
            # Human mode: render dict as readable summary
            self._render_human(payload)

    def write_error(self, code: int, message: str, **details: Any) -> None:
        """Write error result. JSON mode emits structured error to stdout."""
        if self.mode == "json":
            payload = {"status": "error", "code": code, "message": message}
            if details:
                payload["details"] = details
            print(json.dumps(payload, ensure_ascii=False), file=sys.stdout)
        else:
            self.error(message)
            for k, v in details.items():
                self._stderr.print(f"  {k}: {v}")

    def _render_human(self, payload: dict[str, Any]) -> None:
        """Render a result dict as human-readable text on stdout."""
        # Skip 'status' field — already conveyed via OK/error icons
        for key, value in payload.items():
            if key == "status":
                continue
            self._stdout.print(f"  {key}: {value}")
