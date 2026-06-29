from ai_dev_system.harness.runtime import TurnResult
from ai_dev_system.assistant.memory import MemoryStore
from ai_dev_system.assistant.session import SessionStore
from ai_dev_system.assistant.budget import BudgetTracker
from ai_dev_system.assistant.agent import Assistant


class _RecordingRuntime:
    def __init__(self, result):
        self._result = result
        self.calls = []

    def run_turn(self, system_prompt, user_text):
        self.calls.append((system_prompt, user_text))
        return self._result


def _assistant(conn, tmp_path, runtime, **kw):
    sessions = SessionStore(lambda: conn)
    sid = sessions.load_or_create("local", "cli")
    return Assistant(
        runtime=runtime,
        memory_store=MemoryStore(tmp_path),
        session_store=sessions,
        budget=BudgetTracker(lambda: conn),
        base_prompt="BASE",
        session_id=sid,
        **kw,
    ), sid, sessions


def test_respond_persists_user_and_assistant(conn, tmp_path):
    result = TurnResult("the answer", [], {"input_tokens": 3, "output_tokens": 4}, 0.05, "x")
    runtime = _RecordingRuntime(result)
    asst, sid, sessions = _assistant(conn, tmp_path, runtime)
    out = asst.respond("the question")
    assert out is result
    turns = sessions.recent(sid, 10)
    assert [(t.role, t.content) for t in turns] == [
        ("user", "the question"), ("assistant", "the answer"),
    ]


def test_respond_feeds_history_on_second_turn(conn, tmp_path):
    runtime = _RecordingRuntime(TurnResult("a2", [], {}, None, None))
    asst, sid, sessions = _assistant(conn, tmp_path, runtime)
    sessions.append(sid, "user", "q1")
    sessions.append(sid, "assistant", "a1")
    asst.respond("q2")
    _, sent_user = runtime.calls[-1]
    assert "q1" in sent_user and "a1" in sent_user and sent_user.rstrip().endswith("q2")


def test_respond_injects_memory_into_system_prompt(conn, tmp_path):
    runtime = _RecordingRuntime(TurnResult("ok", [], {}, None, None))
    asst, sid, sessions = _assistant(conn, tmp_path, runtime)
    asst._memory_store.write("MEMORY", "add", "remember-this-fact")
    asst.respond("hi")
    sent_system, _ = runtime.calls[-1]
    assert "remember-this-fact" in sent_system


def test_respond_blocks_when_over_cap(conn, tmp_path):
    runtime = _RecordingRuntime(TurnResult("should not run", [], {}, None, None))
    asst, sid, sessions = _assistant(conn, tmp_path, runtime, cap_usd=0.01)
    sessions.append(sid, "assistant", "prior", cost_usd=0.02)  # already over
    out = asst.respond("hi")
    assert runtime.calls == []                       # model NOT called
    assert "budget" in out.final_text.lower()
