import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# Auto-load .env from ~/.ai-dev-system/.env (global config)
_GLOBAL_ENV = Path.home() / ".ai-dev-system" / ".env"
if _GLOBAL_ENV.exists():
    load_dotenv(_GLOBAL_ENV)
# Also load project-local .env if present (provides defaults — does NOT override
# env vars already set in the process environment, e.g. via subprocess tests)
load_dotenv(override=False)


# SQLite-first defaults: zero-install local dev.
DEFAULT_STORAGE_ROOT = str(Path.home() / ".ai-dev-system" / "storage")
DEFAULT_DATABASE_URL = f"sqlite:///{Path.home() / '.ai-dev-system' / 'control.db'}"


@dataclass(frozen=True)
class ProjectPaths:
    """Per-project data locations under <repo>/.ai-dev/state/."""
    repo_path: str
    root: str
    storage_root: str
    database_url: str


@dataclass(frozen=True)
class TelegramBotConfig:
    label: str
    token: str
    allowed_chat_ids: tuple[int, ...] = ()
    repo_path: str = ""
    base_branch: str = ""


def resolve_project(repo_path: str, *, ensure: bool = True) -> ProjectPaths:
    """Derive (and optionally initialize) a project's data location.

    Layout: <repo>/.ai-dev/state/{control.db, storage/}. With ensure=True this
    creates the dirs, adds a `state/` line to <repo>/.ai-dev/.gitignore, and
    applies the DB schema (idempotent). With ensure=False it is pure — no IO.
    """
    if not repo_path or not str(repo_path).strip():
        raise ValueError("resolve_project requires a non-empty repo_path")
    repo = os.path.abspath(str(repo_path).strip())
    root = os.path.join(repo, ".ai-dev", "state")
    storage_root = os.path.join(root, "storage")
    db_path = os.path.join(root, "control.db")
    paths = ProjectPaths(
        repo_path=repo,
        root=root,
        storage_root=storage_root,
        database_url=f"sqlite:///{db_path}",
    )
    if ensure:
        _ensure_project(paths)
    return paths


def _ensure_project(paths: "ProjectPaths") -> None:
    """Idempotent init for a project's data dir: mkdir, .gitignore, schema."""
    from ai_dev_system.db.connection import get_connection
    from ai_dev_system.db.migrator import apply_schema

    os.makedirs(paths.storage_root, exist_ok=True)  # also creates root/.ai-dev

    # .gitignore: add a `state/` line once, preserving any existing content.
    gi = Path(paths.repo_path) / ".ai-dev" / ".gitignore"
    gi.parent.mkdir(parents=True, exist_ok=True)
    if gi.exists():
        content = gi.read_text(encoding="utf-8")
        if "state/" not in [ln.strip() for ln in content.splitlines()]:
            sep = "" if content == "" or content.endswith("\n") else "\n"
            gi.write_text(content + sep + "state/\n", encoding="utf-8")
    else:
        gi.write_text("state/\n", encoding="utf-8")

    # Apply schema to the project DB (idempotent); fail fast on a real error.
    conn = get_connection(paths.database_url)
    try:
        results = apply_schema(conn)
        failed = [
            r for r in results
            if r.error or (not r.applied and r.skipped_reason == "file not found")
        ]
        if failed:
            details = "; ".join(f"{r.name}: {r.error or r.skipped_reason}" for r in failed)
            raise RuntimeError(f"project schema apply failed: {details}")
    finally:
        conn.close()


def _default_retry_policy() -> dict[str, dict[str, Any]]:
    return {
        "EXECUTION_ERROR":     {"max_retries": 2, "retry_delay_s": 0},
        "ENVIRONMENT_ERROR":   {"max_retries": 3, "retry_delay_s": 5.0},
        "SPEC_AMBIGUITY":      {"max_retries": 0, "retry_delay_s": 0},
        "SPEC_CONTRADICTION":  {"max_retries": 0, "retry_delay_s": 0},
        "UNKNOWN":             {"max_retries": 1, "retry_delay_s": 0},
    }


@dataclass
class Config:
    storage_root: str
    database_url: str
    poll_interval_s: float = 5.0
    heartbeat_interval_s: float = 30.0
    heartbeat_timeout_s: float = 120.0
    task_timeout_s: float = 3600.0
    max_parallel_workers: int = 4
    retry_policy: dict = field(default_factory=_default_retry_policy)
    telegram_token: str | None = None
    telegram_allowed_chat_ids: tuple[int, ...] = ()
    telegram_bots: tuple[TelegramBotConfig, ...] = ()

    @classmethod
    def from_env(cls) -> "Config":
        """Build Config from env vars, falling back to SQLite defaults.

        STORAGE_ROOT → ~/.ai-dev-system/storage
        DATABASE_URL → sqlite:///~/.ai-dev-system/control.db
        """
        storage_root = os.environ.get("STORAGE_ROOT") or DEFAULT_STORAGE_ROOT
        database_url = os.environ.get("DATABASE_URL") or DEFAULT_DATABASE_URL
        _tg_token = os.environ.get("AI_DEV_TELEGRAM_TOKEN") or None
        _tg_ids_raw = os.environ.get("AI_DEV_TELEGRAM_ALLOWED_CHAT_IDS", "")
        _tg_ids = tuple(int(x) for x in re.split(r"[,\s]+", _tg_ids_raw.strip()) if x)
        _bots_raw = os.environ.get("AI_DEV_TELEGRAM_BOTS", "").strip()
        _bots: list[TelegramBotConfig] = []
        if _bots_raw:
            try:
                for b in json.loads(_bots_raw):
                    label = str(b.get("label") or "").strip()
                    token = str(b.get("token") or "").strip()
                    ids = tuple(int(x) for x in (b.get("chat_ids") or []))
                    repo_path = str(b.get("repo_path") or "").strip()
                    base_branch = str(b.get("base_branch") or "").strip()
                    if label and token:
                        _bots.append(TelegramBotConfig(
                            label=label, token=token, allowed_chat_ids=ids,
                            repo_path=repo_path, base_branch=base_branch,
                        ))
            except Exception:  # noqa: BLE001 - malformed JSON → fall back to single-token
                _bots = []
        if not _bots and _tg_token:
            _bots.append(TelegramBotConfig(label="telegram", token=_tg_token,
                                           allowed_chat_ids=_tg_ids))
        return cls(
            storage_root=storage_root,
            database_url=database_url,
            telegram_token=_tg_token,
            telegram_allowed_chat_ids=_tg_ids,
            telegram_bots=tuple(_bots),
        )
