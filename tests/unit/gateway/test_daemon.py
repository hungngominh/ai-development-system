import threading
from types import SimpleNamespace
from ai_dev_system.harness.runtime import TurnResult
from ai_dev_system.gateway.base import Inbound
from ai_dev_system.gateway.daemon import GatewayDaemon


class _FakeAssistant:
    def __init__(self, sid):
        self._session_id = sid
    def respond(self, text):
        return TurnResult(f"echo:{text}", [], {}, None, self._session_id)


class _FakeFactory:
    def __init__(self):
        self.made = []
    def for_chat(self, surface, chat_id):
        self.made.append((surface, chat_id))
        return _FakeAssistant(f"{surface}:{chat_id}")


class _FakePlatform:
    name = "telegram"
    def __init__(self, batches):
        self._batches = list(batches)
        self.sent = []
    def poll(self, timeout_s):
        return self._batches.pop(0) if self._batches else []
    def reply(self, chat_id, text):
        self.sent.append((chat_id, text))


def _daemon(platform, tmp_path, **kw):
    return GatewayDaemon(factory=_FakeFactory(), platforms=[platform],
                         home=tmp_path,
                         session_store=SimpleNamespace(mark_recent_resume_pending=lambda **k: 0),
                         sleep_fn=lambda s: None, **kw)


def test_dispatches_and_replies(tmp_path):
    p = _FakePlatform([[Inbound("telegram", 111, "hi")]])
    _daemon(p, tmp_path).run(max_iterations=1)
    assert p.sent == [(111, "echo:hi")]


def test_caches_assistant_per_chat(tmp_path):
    p = _FakePlatform([[Inbound("telegram", 111, "a"), Inbound("telegram", 111, "b")]])
    d = _daemon(p, tmp_path)
    d.run(max_iterations=1)
    assert d._factory.made == [("telegram", "111")]   # for_chat called once for chat 111


def test_one_bad_message_does_not_kill_loop(tmp_path):
    class _Boom(_FakePlatform):
        def reply(self, chat_id, text):
            if chat_id == 1:
                raise RuntimeError("boom")
            super().reply(chat_id, text)
    p = _Boom([[Inbound("telegram", 1, "x"), Inbound("telegram", 111, "ok")]])
    _daemon(p, tmp_path).run(max_iterations=1)
    assert p.sent == [(111, "echo:ok")]   # second message still handled


def test_stop_event_ends_loop(tmp_path):
    ev = threading.Event(); ev.set()
    p = _FakePlatform([[Inbound("telegram", 111, "hi")]])
    _daemon(p, tmp_path, stop_event=ev).run(max_iterations=None)
    assert p.sent == []   # stopped before polling


def test_idle_iteration_sleeps_with_backoff(tmp_path):
    """When a poll returns no messages (idle), daemon must sleep with idle_backoff (> 0).
    Two iterations: first is idle (sleep fires), second hits max_iterations break."""
    sleep_calls = []
    p = _FakePlatform([[], []])  # two iterations, both zero messages
    d = GatewayDaemon(
        factory=_FakeFactory(), platforms=[p], home=tmp_path,
        session_store=SimpleNamespace(mark_recent_resume_pending=lambda **k: 0),
        sleep_fn=lambda s: sleep_calls.append(s),
        idle_backoff=2.5,
    )
    d.run(max_iterations=2)
    # sleep fires after iteration 1 (idle), not after iteration 2 (break before sleep)
    assert sleep_calls == [2.5], f"expected sleep(2.5) on idle iteration, got {sleep_calls}"


def test_busy_iteration_sleeps_zero(tmp_path):
    """When a poll returns at least one message, daemon must sleep with 0 (immediate).
    Two iterations: first is busy (sleep fires with 0), second hits max_iterations break."""
    sleep_calls = []
    p = _FakePlatform([[Inbound("telegram", 111, "hi")], []])  # iteration 1 has message
    d = GatewayDaemon(
        factory=_FakeFactory(), platforms=[p], home=tmp_path,
        session_store=SimpleNamespace(mark_recent_resume_pending=lambda **k: 0),
        sleep_fn=lambda s: sleep_calls.append(s),
        idle_backoff=2.5,
    )
    d.run(max_iterations=2)
    # sleep fires after iteration 1 (busy=0), not after iteration 2 (break before sleep)
    assert sleep_calls == [0], f"expected sleep(0) on busy iteration, got {sleep_calls}"
