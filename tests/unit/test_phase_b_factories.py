"""Phase B factory wiring (run_phase_b._make_agent / _make_gate2_io):
choose the Max-backed agent + interactive Gate 2 in production, stubs in stub
mode. These are the seams where Max vs API vs stub is selected.
"""
from ai_dev_system.cli.run_phase_b import _make_agent, _make_gate2_io
from ai_dev_system.agents.claude_max_agent import ClaudeMaxAgent
from ai_dev_system.agents.stub import StubAgent
from ai_dev_system.gate.stub_gate2 import StubGate2IO
from ai_dev_system.gate.terminal_gate2 import TerminalGate2IO


def _clear(monkeypatch):
    for v in ("AI_DEV_STUB_LLM", "LLM_AGENT_BACKEND", "LLM_PROVIDER", "LLM_MODEL"):
        monkeypatch.delenv(v, raising=False)


def test_agent_is_claude_max_for_claude_code_provider(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "claude_code")
    assert isinstance(_make_agent(), ClaudeMaxAgent)


def test_agent_is_claude_max_when_backend_forced(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("LLM_AGENT_BACKEND", "claude_max")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")  # backend override wins
    assert isinstance(_make_agent(), ClaudeMaxAgent)


def test_agent_is_stub_in_stub_mode(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("AI_DEV_STUB_LLM", "1")
    assert isinstance(_make_agent(), StubAgent)


def test_gate2_io_stub_in_stub_mode(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("AI_DEV_STUB_LLM", "1")
    assert isinstance(_make_gate2_io(), StubGate2IO)


def test_gate2_io_terminal_with_auto_approve(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "claude_code")
    io_ = _make_gate2_io(auto_approve=True)
    assert isinstance(io_, TerminalGate2IO)
    assert io_._auto_approve is True
