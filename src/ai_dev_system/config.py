import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# Auto-load .env from ~/.ai-dev-system/.env (global config)
_GLOBAL_ENV = Path.home() / ".ai-dev-system" / ".env"
if _GLOBAL_ENV.exists():
    load_dotenv(_GLOBAL_ENV)
# Also load project-local .env if present (overrides global)
load_dotenv(override=True)


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
    retry_policy: dict = field(default_factory=_default_retry_policy)

    @classmethod
    def from_env(cls) -> "Config":
        storage_root = os.environ.get("STORAGE_ROOT")
        if not storage_root:
            raise ValueError("STORAGE_ROOT environment variable is required")
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            raise ValueError("DATABASE_URL environment variable is required")
        return cls(storage_root=storage_root, database_url=database_url)
