from dataclasses import dataclass, field
from typing import Optional, Protocol


@dataclass
class PromotedOutput:
    name: str
    artifact_type: str
    description: str = ""


@dataclass
class AgentResult:
    output_path: str
    promoted_outputs: list["PromotedOutput"] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.error is None


class Agent(Protocol):
    def run(
        self,
        task_id: str,
        output_path: str,
        promoted_outputs=(),
        context: Optional[dict] = None,
        timeout_s: float = 3600.0,
        file_rules: list = (),
    ) -> AgentResult:
        ...
