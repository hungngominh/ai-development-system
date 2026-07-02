# src/ai_dev_system/gateway/spec_status_watcher.py
"""SpecStatusWatcher — proactive push when a spec worker reaches a terminal state.

Swept once per daemon poll loop, alongside RunStatusWatcher and ClarifyWatcher.
For each pending chat record still in phase='generating' whose spec JSON now
exists:
- status=='error'                → push ❌ (real error) and CLEAR the record
- status=='done', clarify needed → skip (ClarifyWatcher owns that push)
- status=='done' otherwise       → push the spec-gate message and flip the
  record to phase='awaiting_spec_approval' (same transition dev_task_progress
  makes when the user asks — whichever runs first wins; the other is a no-op).

Reads JSON + sends only — never calls an LLM (single-threaded daemon loop).
One bad record never kills the sweep. Delivery is at-least-once: push happens
before the phase flip / clear, so a crash in between re-sends next sweep."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from ai_dev_system.task_graph.spec_messages import spec_error_message, spec_gate_message

logger = logging.getLogger(__name__)


class SpecStatusWatcher:
    def __init__(self, chat_task_store, platforms_by_name: dict, session_store,
                 storage_root: str) -> None:
        self._store = chat_task_store
        self._platforms = platforms_by_name
        self._sessions = session_store
        self._specs_dir = Path(storage_root) / "task_specs"

    def check_once(self) -> int:
        pushed = 0
        for rec in self._store.list_pending():
            try:
                pushed += self._check(rec)
            except Exception:  # noqa: BLE001 — one bad record never kills the sweep
                logger.exception("spec-status: error on record %s", rec.get("spec_id"))
        return pushed

    def _check(self, rec: dict) -> int:
        if rec.get("phase") != "generating":
            return 0
        spec_path = self._specs_dir / f"{rec.get('spec_id')}.json"
        if not spec_path.exists():
            return 0
        try:
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return 0
        surface, chat_id = rec.get("surface"), rec.get("chat_id")
        platform = self._platforms.get(surface)
        if platform is None:
            return 0
        status = spec.get("status")
        if status == "error":
            msg = spec_error_message(spec)
            platform.reply(int(chat_id), msg)
            self._append(surface, chat_id, msg)
            self._store.clear(surface, chat_id)
            return 1
        if status == "done":
            if (spec.get("clarify") or {}).get("needed"):
                return 0  # ClarifyWatcher pushes the questions
            msg = spec_gate_message(spec)
            platform.reply(int(chat_id), msg)
            self._append(surface, chat_id, msg)
            self._store.update(surface, chat_id, phase="awaiting_spec_approval")
            return 1
        return 0

    def _append(self, surface, chat_id, msg: str) -> None:
        sid = self._sessions.load_or_create(surface, chat_id)
        self._sessions.append(sid, "assistant", msg)
