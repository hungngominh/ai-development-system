import json
import os
from typing import Optional
from ai_dev_system.agents.base import AgentResult, PromotedOutput


class StubAgent:
    """Test double that writes expected output files deterministically."""

    def run(
        self,
        task_id: str,
        output_path: str,
        promoted_outputs=(),
        context: Optional[dict] = None,
        timeout_s: float = 3600.0,
    ) -> AgentResult:
        os.makedirs(output_path, exist_ok=True)
        promoted_list = list(promoted_outputs)
        if promoted_list:
            for po in promoted_list:
                filepath = os.path.join(output_path, po.name)
                with open(filepath, "w") as f:
                    json.dump({"task_id": task_id, "status": "stub_complete"}, f)
        else:
            with open(os.path.join(output_path, "output.txt"), "w") as f:
                f.write(f"stub output for {task_id}")
        return AgentResult(
            output_path=output_path,
            promoted_outputs=promoted_list,
        )
