import json
from pathlib import Path

from ai_dev_system.gateway.spec_status_watcher import SpecStatusWatcher
from ai_dev_system.harness.tools.chat_task_store import ChatTaskStore


class FakePlatform:
    def __init__(self): self.sent = []
    def reply(self, chat_id, text): self.sent.append((chat_id, text))


class FakeSessions:
    def __init__(self): self.appended = []
    def load_or_create(self, surface, chat_id): return f"sid-{surface}-{chat_id}"
    def append(self, sid, role, content): self.appended.append((sid, role, content))


def _write_spec(root, spec_id, payload):
    d = Path(root) / "task_specs"; d.mkdir(parents=True, exist_ok=True)
    (d / f"{spec_id}.json").write_text(json.dumps(payload), encoding="utf-8")


def _store(tmp_path, **over):
    s = ChatTaskStore(str(tmp_path))
    s.set_pending("Sigo", "5913", spec_id="ab", repo="/r", base_branch="main", idea="add X")
    if over:
        s.update("Sigo", "5913", **over)
    return s


def _watcher(tmp_path, store, plat, sess=None):
    return SpecStatusWatcher(store, {"Sigo": plat}, sess or FakeSessions(),
                             str(tmp_path))


def test_pushes_error_and_clears_pending(tmp_path):
    s = _store(tmp_path)
    _write_spec(tmp_path, "ab", {"status": "error", "error": "TimeoutExpired: 300s"})
    plat = FakePlatform(); sess = FakeSessions()
    w = _watcher(tmp_path, s, plat, sess)
    assert w.check_once() == 1
    assert "❌" in plat.sent[0][1] and "TimeoutExpired" in plat.sent[0][1]
    assert sess.appended and sess.appended[0][1] == "assistant"
    assert s.get_pending("Sigo", "5913") is None          # cleared → retry possible
    assert w.check_once() == 0                            # no re-push


def test_pushes_spec_ready_once_and_flips_phase(tmp_path):
    s = _store(tmp_path)
    _write_spec(tmp_path, "ab", {"status": "done",
                                 "spec_doc_url": "https://x/blob/b/s.md",
                                 "clarify": {"needed": False, "questions": []}})
    plat = FakePlatform()
    w = _watcher(tmp_path, s, plat)
    assert w.check_once() == 1
    assert "📄 Spec sẵn sàng." in plat.sent[0][1] and "https://x" in plat.sent[0][1]
    assert s.get_pending("Sigo", "5913")["phase"] == "awaiting_spec_approval"
    assert w.check_once() == 0                            # dedup: phase moved on
    assert len(plat.sent) == 1


def test_leaves_clarify_needed_to_clarify_watcher(tmp_path):
    s = _store(tmp_path)
    _write_spec(tmp_path, "ab", {"status": "done",
                                 "clarify": {"needed": True, "questions": ["Q?"]}})
    plat = FakePlatform()
    assert _watcher(tmp_path, s, plat).check_once() == 0 and plat.sent == []


def test_silent_while_worker_running_or_phase_advanced(tmp_path):
    s = _store(tmp_path)                                   # no spec json yet
    plat = FakePlatform()
    w = _watcher(tmp_path, s, plat)
    assert w.check_once() == 0 and plat.sent == []
    _write_spec(tmp_path, "ab", {"status": "done", "clarify": {"needed": False}})
    s.update("Sigo", "5913", phase="awaiting_spec_approval")   # progress tool got there first
    assert w.check_once() == 0 and plat.sent == []


def test_unregistered_surface_is_skipped(tmp_path):
    s = _store(tmp_path)
    _write_spec(tmp_path, "ab", {"status": "error", "error": "x"})
    w = SpecStatusWatcher(s, {}, FakeSessions(), str(tmp_path))
    assert w.check_once() == 0
    assert s.get_pending("Sigo", "5913") is not None       # NOT cleared without delivery
