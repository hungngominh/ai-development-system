from unittest.mock import MagicMock
from ai_dev_system.agents.base import AgentResult
from ai_dev_system.agents.phase_routing_agent import PhaseRoutingAgent


def _agent(repo="/repo"):
    a = PhaseRoutingAgent(repo, "ai-dev/task-x", "main")
    a.test_agent = MagicMock()
    a.impl_agent = MagicMock()
    a.test_agent.run.return_value = AgentResult(output_path="out")
    a.impl_agent.run.return_value = AgentResult(output_path="out")
    return a


def test_phase_test_routes_to_test_agent():
    a = _agent()
    a.run("TASK-TEST", "out", context={"phase": "test"})
    a.test_agent.run.assert_called_once()
    a.impl_agent.run.assert_not_called()


def test_phase_implementation_routes_to_impl_agent():
    a = _agent()
    a.run("TASK-IMPL", "out", context={"phase": "implementation"})
    a.impl_agent.run.assert_called_once()
    a.test_agent.run.assert_not_called()


def test_missing_phase_defaults_to_impl_agent():
    a = _agent()
    a.run("TASK", "out", context={})
    a.impl_agent.run.assert_called_once()
