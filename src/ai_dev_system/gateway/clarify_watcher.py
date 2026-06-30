"""ClarifyWatcher — swept once per daemon poll loop, alongside RunStatusWatcher.

For each pending single-task chat record whose spec finished with blocking
findings, push the pre-generated questions to the chat ONCE and flip the record to
phase='awaiting_clarify'. Reads JSON + sends only — never calls an LLM (it runs on
the single-threaded daemon loop). One bad record never kills the sweep. Delivery is
at-least-once: if the process dies after platform.reply but before the phase flip,
the next sweep re-sends (a duplicate is possible, but a question is never silently lost).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from ai_dev_system.task_graph.clarify_questions import format_questions

logger = logging.getLogger(__name__)

_ROUND_CAP = 2


class ClarifyWatcher:
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
                logger.exception("clarify: error on record %s", rec.get("spec_id"))
        return pushed

    def _check(self, rec: dict) -> int:
        if rec.get("phase") == "awaiting_clarify":
            return 0
        if rec.get("round", 0) >= _ROUND_CAP:
            return 0
        spec_path = self._specs_dir / f"{rec.get('spec_id')}.json"
        if not spec_path.exists():
            return 0
        try:
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return 0
        clarify = spec.get("clarify") or {}
        if not clarify.get("needed"):
            return 0
        surface, chat_id = rec.get("surface"), rec.get("chat_id")
        platform = self._platforms.get(surface)
        if platform is None:
            return 0
        questions = clarify.get("questions") or []
        if not questions:
            return 0
        msg = format_questions(questions)
        platform.reply(int(chat_id), msg)
        sid = self._sessions.load_or_create(surface, chat_id)
        self._sessions.append(sid, "assistant", msg)
        self._store.update(surface, chat_id, phase="awaiting_clarify",
                           clarify_questions=questions)
        return 1
