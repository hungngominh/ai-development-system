from typing import Literal, Protocol


class VerificationLLMClient(Protocol):
    def judge_criterion(
        self,
        criterion_id: str,
        criterion_text: str,
        evidence: list[str],
    ) -> tuple[Literal["PASS", "FAIL"], float, str]:
        """Returns: (verdict, confidence, reasoning)"""
        ...


class StubVerificationLLMClient:
    """Returns configurable verdicts per criterion_id — deterministic for tests."""

    def __init__(self, verdicts: dict[str, tuple[Literal["PASS", "FAIL"], float, str]]):
        # verdicts: {"AC-1": ("PASS", 0.95, "looks good"), ...}
        self.verdicts = verdicts

    def judge_criterion(
        self,
        criterion_id: str,
        criterion_text: str,
        evidence: list[str],
    ) -> tuple[Literal["PASS", "FAIL"], float, str]:
        return self.verdicts.get(criterion_id, ("PASS", 1.0, "stub default"))
