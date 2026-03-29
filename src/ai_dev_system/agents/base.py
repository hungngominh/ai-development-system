from dataclasses import dataclass, field
from typing import Optional


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
