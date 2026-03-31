"""
Unit tests for CrewAIAgent and make_crewai_agent().

crewai is faked via conftest.py (sys.modules injection) so no real LLM is ever called.
All tests patch crewai.* at the module level used by crewai_agent.py.
"""
import concurrent.futures
import os
import pytest

from ai_dev_system.agents.base import AgentResult, PromotedOutput
from ai_dev_system.agents.crewai_agent import CrewAIAgent, make_crewai_agent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_agent():
    return CrewAIAgent(llm_model="anthropic/claude-opus-4-5", llm_api_key="test-key")


def _patch_crewai_success(mocker):
    """Patch crewai classes so kickoff() returns 'done'."""
    mock_crew_cls = mocker.patch("ai_dev_system.agents.crewai_agent.crewai.Crew")
    mock_crew_cls.return_value.kickoff.return_value = "done"
    mocker.patch("ai_dev_system.agents.crewai_agent.crewai.Agent")
    mocker.patch("ai_dev_system.agents.crewai_agent.crewai.Task")
    mocker.patch("ai_dev_system.agents.crewai_agent.crewai.LLM")
    return mock_crew_cls


# ---------------------------------------------------------------------------
# test_run_creates_output_directory
# ---------------------------------------------------------------------------

def test_run_creates_output_directory(tmp_path, mocker):
    """Output directory is created even if it did not exist before run()."""
    output_path = str(tmp_path / "new_dir" / "nested")
    assert not os.path.exists(output_path)

    _patch_crewai_success(mocker)

    agent = _make_agent()
    result = agent.run(task_id="T-1", output_path=output_path)

    assert os.path.isdir(output_path)


# ---------------------------------------------------------------------------
# test_run_success_no_promoted
# ---------------------------------------------------------------------------

def test_run_success_no_promoted(tmp_path, mocker):
    """kickoff() succeeds with no promoted outputs → AgentResult.success is True."""
    _patch_crewai_success(mocker)

    agent = _make_agent()
    result = agent.run(task_id="T-2", output_path=str(tmp_path))

    assert isinstance(result, AgentResult)
    assert result.success is True
    assert result.output_path == str(tmp_path)
    assert result.error is None


# ---------------------------------------------------------------------------
# test_run_success_with_promoted
# ---------------------------------------------------------------------------

def test_run_success_with_promoted(tmp_path, mocker):
    """kickoff() succeeds and promoted output file exists → success with promoted list."""
    _patch_crewai_success(mocker)

    # Simulate crewai writing the file
    (tmp_path / "result.json").write_text('{"ok": true}')

    po = PromotedOutput(name="result.json", artifact_type="data")
    agent = _make_agent()
    result = agent.run(
        task_id="T-3",
        output_path=str(tmp_path),
        promoted_outputs=[po],
    )

    assert result.success is True
    assert result.promoted_outputs == [po]


# ---------------------------------------------------------------------------
# test_run_missing_promoted_output_returns_error
# ---------------------------------------------------------------------------

def test_run_missing_promoted_output_returns_error(tmp_path, mocker):
    """kickoff() succeeds but promoted file is absent → error result."""
    _patch_crewai_success(mocker)

    po = PromotedOutput(name="result.json", artifact_type="data")
    agent = _make_agent()
    result = agent.run(
        task_id="T-4",
        output_path=str(tmp_path),
        promoted_outputs=[po],
    )

    assert result.success is False
    assert "Missing promoted output: result.json" in result.error


# ---------------------------------------------------------------------------
# test_run_kickoff_exception_returns_error
# ---------------------------------------------------------------------------

def test_run_kickoff_exception_returns_error(tmp_path, mocker):
    """Exception raised inside kickoff() → AgentResult with error, no re-raise."""
    mock_crew_cls = mocker.patch("ai_dev_system.agents.crewai_agent.crewai.Crew")
    mock_crew_cls.return_value.kickoff.side_effect = RuntimeError("crew failed")
    mocker.patch("ai_dev_system.agents.crewai_agent.crewai.Agent")
    mocker.patch("ai_dev_system.agents.crewai_agent.crewai.Task")
    mocker.patch("ai_dev_system.agents.crewai_agent.crewai.LLM")

    agent = _make_agent()
    result = agent.run(task_id="T-5", output_path=str(tmp_path))

    assert result.success is False
    assert result.error == "crew failed"


# ---------------------------------------------------------------------------
# test_run_timeout_returns_error
# ---------------------------------------------------------------------------

def test_run_timeout_returns_error(tmp_path, mocker):
    """future.result() raising TimeoutError → error result containing 'timed out'."""
    _patch_crewai_success(mocker)

    # Patch ThreadPoolExecutor so future.result() raises TimeoutError
    mock_executor_cls = mocker.patch(
        "ai_dev_system.agents.crewai_agent.concurrent.futures.ThreadPoolExecutor"
    )
    mock_future = mocker.MagicMock()
    mock_future.result.side_effect = concurrent.futures.TimeoutError()
    mock_executor_cls.return_value.submit.return_value = mock_future

    agent = _make_agent()
    result = agent.run(task_id="T-6", output_path=str(tmp_path), timeout_s=1.0)

    assert result.success is False
    assert "timed out" in result.error.lower()


# ---------------------------------------------------------------------------
# test_run_uses_context_task_description
# ---------------------------------------------------------------------------

def test_run_uses_context_task_description(tmp_path, mocker):
    """context['task_description'] is used as the task description."""
    _patch_crewai_success(mocker)

    mock_task_cls = mocker.patch("ai_dev_system.agents.crewai_agent.crewai.Task")

    agent = _make_agent()
    agent.run(
        task_id="T-7",
        output_path=str(tmp_path),
        context={"task_description": "Build auth module"},
    )

    # First Task call should be impl_task; check its description kwarg
    first_call_kwargs = mock_task_cls.call_args_list[0].kwargs
    assert "Build auth module" in first_call_kwargs["description"]


# ---------------------------------------------------------------------------
# test_run_uses_fallback_description_when_no_context
# ---------------------------------------------------------------------------

def test_run_uses_fallback_description_when_no_context(tmp_path, mocker):
    """context=None → fallback description contains 'Task {task_id}'."""
    _patch_crewai_success(mocker)

    mock_task_cls = mocker.patch("ai_dev_system.agents.crewai_agent.crewai.Task")

    agent = _make_agent()
    agent.run(
        task_id="TASK-99",
        output_path=str(tmp_path),
        context=None,
    )

    first_call_kwargs = mock_task_cls.call_args_list[0].kwargs
    assert "Task TASK-99" in first_call_kwargs["description"]


# ---------------------------------------------------------------------------
# test_make_crewai_agent_anthropic
# ---------------------------------------------------------------------------

def test_make_crewai_agent_anthropic(monkeypatch):
    """LLM_PROVIDER=anthropic builds agent with 'anthropic/<model>' litellm string."""
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_MODEL", "claude-opus-4-5")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    agent = make_crewai_agent()

    assert isinstance(agent, CrewAIAgent)
    assert agent._llm_model == "anthropic/claude-opus-4-5"
    assert agent._llm_api_key == "test-key"


# ---------------------------------------------------------------------------
# test_make_crewai_agent_openai
# ---------------------------------------------------------------------------

def test_make_crewai_agent_openai(monkeypatch):
    """LLM_PROVIDER=openai builds agent with bare model name (no prefix)."""
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    agent = make_crewai_agent()

    assert isinstance(agent, CrewAIAgent)
    assert agent._llm_model == "gpt-4o"
    assert agent._llm_api_key == "test-key"


# ---------------------------------------------------------------------------
# test_make_crewai_agent_missing_provider
# ---------------------------------------------------------------------------

def test_make_crewai_agent_missing_provider(monkeypatch):
    """Absent LLM_PROVIDER raises RuntimeError."""
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("LLM_MODEL", "some-model")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")

    with pytest.raises(RuntimeError, match="LLM_PROVIDER"):
        make_crewai_agent()


# ---------------------------------------------------------------------------
# test_make_crewai_agent_missing_key
# ---------------------------------------------------------------------------

def test_make_crewai_agent_missing_key(monkeypatch):
    """LLM_PROVIDER=anthropic but no ANTHROPIC_API_KEY raises RuntimeError."""
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_MODEL", "claude-opus-4-5")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        make_crewai_agent()
