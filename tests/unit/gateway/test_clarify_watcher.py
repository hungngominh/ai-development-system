# tests/unit/gateway/test_clarify_watcher.py
import json
from pathlib import Path

from ai_dev_system.gateway.clarify_watcher import ClarifyWatcher
from ai_dev_system.harness.tools.chat_task_store import ChatTaskStore


class FakePlatform:
    def __init__(self): self.sent = []
    def reply(self, chat_id, text): self.sent.append((chat_id, text))


class FakeSessions:
    def __init__(self): self.appended = []
    def load_or_create(self, surface, chat_id): return f"sid-{surface}-{chat_id}"
    def append(self, sid, role, content): self.appended.append((sid, role, content))


def _write_spec(root, spec_id, clarify):
    d = Path(root) / "task_specs"; d.mkdir(parents=True, exist_ok=True)
    (d / f"{spec_id}.json").write_text(json.dumps({"clarify": clarify}), encoding="utf-8")


def _store(tmp_path, **over):
    s = ChatTaskStore(str(tmp_path))
    s.set_pending("Sigo", "5913", spec_id="ab", repo="/r", base_branch="main", idea="add X")
    if over:
        s.update("Sigo", "5913", **over)
    return s


def test_pushes_question_once_and_marks_awaiting(tmp_path):
    s = _store(tmp_path)
    _write_spec(tmp_path, "ab", {"needed": True, "questions": ["GUID hay PK?"]})
    plat = FakePlatform(); sess = FakeSessions()
    w = ClarifyWatcher(s, {"Sigo": plat}, sess, str(tmp_path))

    assert w.check_once() == 1
    assert plat.sent and "GUID hay PK?" in plat.sent[0][1]
    assert sess.appended and sess.appended[0][1] == "assistant"
    assert s.get_pending("Sigo", "5913")["phase"] == "awaiting_clarify"

    assert w.check_once() == 0                    # dedup: already awaiting
    assert len(plat.sent) == 1


def test_no_push_when_not_needed(tmp_path):
    s = _store(tmp_path)
    _write_spec(tmp_path, "ab", {"needed": False, "questions": []})
    plat = FakePlatform()
    w = ClarifyWatcher(s, {"Sigo": plat}, FakeSessions(), str(tmp_path))
    assert w.check_once() == 0 and plat.sent == []


def test_no_push_when_round_cap_reached(tmp_path):
    s = _store(tmp_path, round=2)
    _write_spec(tmp_path, "ab", {"needed": True, "questions": ["Q?"]})
    plat = FakePlatform()
    w = ClarifyWatcher(s, {"Sigo": plat}, FakeSessions(), str(tmp_path))
    assert w.check_once() == 0 and plat.sent == []


def test_no_spec_file_yet_is_silent(tmp_path):
    s = _store(tmp_path)                          # worker still running, no spec json
    plat = FakePlatform()
    w = ClarifyWatcher(s, {"Sigo": plat}, FakeSessions(), str(tmp_path))
    assert w.check_once() == 0 and plat.sent == []
