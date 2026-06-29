import json

from ai_dev_system.db.connection import get_connection
from ai_dev_system.harness.runtime import TurnResult
from ai_dev_system.assistant.memory import MemoryStore
from ai_dev_system.assistant.session import SessionStore
from ai_dev_system.assistant.budget import BudgetTracker
from ai_dev_system.assistant.factory import AssistantFactory
from ai_dev_system.gateway.platforms.telegram import TelegramAdapter
from ai_dev_system.gateway.daemon import GatewayDaemon


class _EchoRuntime:
    def run_turn(self, system_prompt, user_text):
        return TurnResult(f"reply: {user_text}", [], {"input_tokens": 1, "output_tokens": 1}, 0.0, None)


def test_telegram_to_assistant_to_reply(tmp_path, file_db_url):
    conn_factory = lambda: get_connection(file_db_url)
    factory = AssistantFactory(
        runtime=_EchoRuntime(), memory_store=MemoryStore(tmp_path / "home"),
        session_store=SessionStore(conn_factory), budget=BudgetTracker(conn_factory),
        base_prompt="BASE",
    )
    sent = []
    upd = [{"update_id": 1, "message": {"chat": {"id": 111}, "text": "hello"}}]
    transport_calls = iter([json.dumps({"ok": True, "result": upd}).encode(),
                            json.dumps({"ok": True, "result": []}).encode()])
    adapter = TelegramAdapter(
        token="TOK", allowed_chat_ids=(111,),
        transport=lambda url, data, timeout: next(transport_calls),
        sender=lambda token, chat_id, text, transport=None: sent.append((chat_id, text)),
    )
    daemon = GatewayDaemon(
        factory=factory, platforms=[adapter], home=tmp_path,
        session_store=SessionStore(conn_factory), sleep_fn=lambda s: None,
    )
    daemon.run(max_iterations=1)
    assert sent == [(111, "reply: hello")]
    # the turn was persisted in the (telegram, 111) session
    sid = SessionStore(conn_factory).load_or_create("telegram", "111")
    turns = SessionStore(conn_factory).recent(sid, 10)
    assert [(t.role, t.content) for t in turns] == [("user", "hello"), ("assistant", "reply: hello")]
