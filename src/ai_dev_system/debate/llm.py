# src/ai_dev_system/debate/llm.py
import json
from typing import Protocol


class DebateLLMClient(Protocol):
    def complete(self, system: str, user: str) -> str:
        """Call LLM with system + user prompt. Returns raw string response."""
        ...


class StubDebateLLMClient:
    """Deterministic stub for testing. Returns fixture JSON based on role keyword in system prompt."""

    def complete(self, system: str, user: str) -> str:
        system_lower = system.lower()
        if "moderator" in system_lower or "synthesis" in system_lower:
            return json.dumps({
                "status": "RESOLVED",
                "confidence": 0.9,
                "summary": "Both agents agree on the proposed approach.",
                "caveat": None,
            })
        if "generate" in system_lower and "question" in system_lower:
            return json.dumps([
                {
                    "id": "Q1",
                    "text": "Should authentication use JWT tokens?",
                    "classification": "REQUIRED",
                    "domain": "security",
                    "agent_a": "SecuritySpecialist",
                    "agent_b": "BackendArchitect",
                },
                {
                    "id": "Q2",
                    "text": "Which database engine?",
                    "classification": "STRATEGIC",
                    "domain": "database",
                    "agent_a": "DatabaseSpecialist",
                    "agent_b": "BackendArchitect",
                },
            ])
        if "finalize" in system_lower or "spec" in system_lower:
            return json.dumps({
                "proposal": "# Proposal\nThis system solves the stated problem.",
                "design": "# Design\nUse standard MVC patterns.",
                "functional": "# Functional Requirements\nCore CRUD operations.",
                "non_functional": "# Non-Functional\nResponse time under 200ms.",
                "acceptance_criteria": "# Acceptance Criteria\nAll tests pass.",
            })
        # Default: agent position
        return "This approach is preferred because it balances trade-offs effectively."
