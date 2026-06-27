"""Routes a task to the test-authoring or implementation agent by phase.

`run_execution` takes a single agent used for every task in the graph. This
adapter inspects the per-task context and delegates: phase=="test" goes to
TestAuthorAgent, everything else goes to RepoBranchAgent. Both sub-agents share
the same repo / branch / base / live-log.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from ai_dev_system.agents.base import AgentResult
from ai_dev_system.agents.repo_branch_agent import RepoBranchAgent
from ai_dev_system.agents.test_author_agent import TestAuthorAgent


class PhaseRoutingAgent:
    """Implements the Agent protocol; dispatches by context['phase']."""

    def __init__(
        self,
        repo_path: str,
        branch_name: str,
        base_branch: str,
        live_log_path: Optional[Path] = None,
    ) -> None:
        self.repo_path = repo_path
        self.branch_name = branch_name
        self.base_branch = base_branch
        self.live_log_path = live_log_path
        self.test_agent = TestAuthorAgent(repo_path, branch_name, base_branch, live_log_path)
        self.impl_agent = RepoBranchAgent(repo_path, branch_name, base_branch, live_log_path)

    def run(
        self,
        task_id: str,
        output_path: str,
        promoted_outputs=(),
        context: Optional[dict] = None,
        timeout_s: float = 1800.0,
        file_rules: list = (),
    ) -> AgentResult:
        phase = (context or {}).get("phase")
        target = self.test_agent if phase == "test" else self.impl_agent
        return target.run(
            task_id=task_id,
            output_path=output_path,
            promoted_outputs=promoted_outputs,
            context=context,
            timeout_s=timeout_s,
            file_rules=file_rules,
        )
