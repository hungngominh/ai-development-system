from ai_dev_system.assistant.session import SessionStore
from ai_dev_system.assistant.budget import BudgetTracker, Budget


def test_session_total_sums_costs(conn):
    sessions = SessionStore(lambda: conn)
    sid = sessions.load_or_create("local", "cli")
    sessions.append(sid, "user", "hi")  # null tokens/cost
    sessions.append(sid, "assistant", "hello", input_tokens=10, output_tokens=5, cost_usd=0.01)
    sessions.append(sid, "assistant", "again", input_tokens=20, output_tokens=7, cost_usd=0.02)
    total = BudgetTracker(lambda: conn).session_total(sid)
    assert isinstance(total, Budget)
    assert total.input_tokens == 30
    assert total.output_tokens == 12
    assert abs(total.cost_usd - 0.03) < 1e-9


def test_over_cap(conn):
    sessions = SessionStore(lambda: conn)
    sid = sessions.load_or_create("local", "cli")
    sessions.append(sid, "assistant", "x", cost_usd=0.5)
    bt = BudgetTracker(lambda: conn)
    assert bt.over_cap(sid, None) is False
    assert bt.over_cap(sid, 1.0) is False
    assert bt.over_cap(sid, 0.4) is True
