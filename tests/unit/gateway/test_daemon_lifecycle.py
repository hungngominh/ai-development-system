from types import SimpleNamespace
from ai_dev_system.assistant.session import mark_clean_shutdown, clean_shutdown_path
from ai_dev_system.gateway.daemon import GatewayDaemon


class _NoPlatform:
    name = "telegram"
    def poll(self, timeout_s): return []
    def reply(self, chat_id, text): pass


def _daemon(tmp_path, recorder):
    ss = SimpleNamespace(mark_recent_resume_pending=lambda **k: recorder.append("resume") or 0)
    return GatewayDaemon(factory=SimpleNamespace(for_chat=lambda *a: None),
                         platforms=[_NoPlatform()], home=tmp_path,
                         session_store=ss, sleep_fn=lambda s: None)


def test_marks_resume_when_no_clean_marker(tmp_path):
    rec = []
    _daemon(tmp_path, rec).run(max_iterations=1)
    assert rec == ["resume"]                    # crash recovery fired
    assert clean_shutdown_path(tmp_path).exists()  # marker written in finally


def test_skips_resume_when_clean_marker_present(tmp_path):
    mark_clean_shutdown(tmp_path)
    rec = []
    _daemon(tmp_path, rec).run(max_iterations=1)
    assert rec == []                            # clean prior shutdown -> no resume flagging
