"""File-backed pending single-task state per (surface, chat_id). Lives under
storage_root (the mounted /data volume in Docker), so it survives daemon
restarts. One pending task per chat (the vertical slice)."""
from __future__ import annotations

import json
import re
from pathlib import Path


def _safe(part: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(part))


class ChatTaskStore:
    def __init__(self, storage_root: str) -> None:
        self._dir = Path(storage_root) / "chat_tasks"

    def _path(self, surface: str, chat_id: str) -> Path:
        return self._dir / f"{_safe(surface)}__{_safe(chat_id)}.json"

    def set_pending(self, surface, chat_id, *, spec_id, repo, base_branch) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path(surface, chat_id).write_text(
            json.dumps({"spec_id": spec_id, "repo": repo,
                        "base_branch": base_branch, "pr_url": None}),
            encoding="utf-8",
        )

    def get_pending(self, surface, chat_id) -> dict | None:
        p = self._path(surface, chat_id)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None

    def set_pr_url(self, surface, chat_id, pr_url) -> None:
        cur = self.get_pending(surface, chat_id) or {}
        cur["pr_url"] = pr_url
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path(surface, chat_id).write_text(json.dumps(cur), encoding="utf-8")

    def clear(self, surface, chat_id) -> None:
        p = self._path(surface, chat_id)
        if p.exists():
            p.unlink()
