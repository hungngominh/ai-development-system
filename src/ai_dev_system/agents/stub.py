import json
import os
from ai_dev_system.agents.base import AgentResult, PromotedOutput

class StubAgent:
    """Test double — writes expected output files deterministically."""

    def run(
        self,
        task_id: str,
        output_path: str,
        promoted_outputs: list[PromotedOutput],
    ) -> AgentResult:
        os.makedirs(output_path, exist_ok=True)
        for po in promoted_outputs:
            filepath = os.path.join(output_path, po.name)
            with open(filepath, "w") as f:
                json.dump({"task_id": task_id, "status": "stub_complete"}, f)
        return AgentResult(output_path=output_path, promoted_outputs=promoted_outputs)
