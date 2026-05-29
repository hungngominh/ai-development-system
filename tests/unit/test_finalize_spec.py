import json

import pytest
from pathlib import Path
from ai_dev_system.finalize_spec import (
    finalize_spec,
    SYSTEM_PROMPT,
    SYSTEM_PROMPT_BRIEF_V2,
)
from ai_dev_system.spec_bundle import SpecBundle
from ai_dev_system.debate.llm import StubDebateLLMClient

APPROVED_ANSWERS = {
    "Q1": "Use JWT with short expiry (15 min access, 7 day refresh)",
    "Q2": "PostgreSQL with connection pooling",
    "Q3": "REST API with OpenAPI spec",
}


BRIEF_V2 = {
    "brief_version": 2,
    "template_id": "generic_v1",
    "schema_hash": "abc123",
    "run_id": "r1",
    "project_id": "p1",
    "source_hash": "deadbeef",
    "fields": {
        "problem_statement": {"value": "Users can't reset passwords", "source": "user", "rationale": None},
        "scope_in": {"value": ["password reset", "email verify"], "source": "user", "rationale": None},
        "scope_out": {"value": ["MFA"], "source": "user", "rationale": None},
        "success_metric": {"value": "95% self-serve reset success", "source": "user", "rationale": None},
        "primary_user": {"value": None, "source": "skipped", "rationale": None},
    },
    "assumptions": ["primary_user"],
    "audit": [],
}


class _CapturingLLM:
    """Records the last (system, user) pair passed to .complete()."""

    def __init__(self, response: str):
        self._response = response
        self.last_system: str | None = None
        self.last_user: str | None = None

    def complete(self, system: str, user: str) -> str:
        self.last_system = system
        self.last_user = user
        return self._response


_SPEC_RESPONSE = json.dumps({
    "proposal": "# Proposal\nx",
    "design": "# Design\nx",
    "functional": "# Functional\nx",
    "non_functional": "# Non-Functional\nx",
    "acceptance_criteria": "# Acceptance Criteria\nx",
})


def test_finalize_spec_returns_spec_bundle(tmp_path):
    client = StubDebateLLMClient()
    bundle = finalize_spec(APPROVED_ANSWERS, "r1", client, output_dir=tmp_path)
    assert isinstance(bundle, SpecBundle)


def test_finalize_spec_writes_five_files(tmp_path):
    client = StubDebateLLMClient()
    bundle = finalize_spec(APPROVED_ANSWERS, "r1", client, output_dir=tmp_path)
    expected = {"proposal.md", "design.md", "functional.md", "non-functional.md", "acceptance-criteria.md"}
    assert set(bundle.files.keys()) == expected


def test_finalize_spec_files_nonempty(tmp_path):
    client = StubDebateLLMClient()
    bundle = finalize_spec(APPROVED_ANSWERS, "r1", client, output_dir=tmp_path)
    for name, path in bundle.files.items():
        assert path.exists(), f"{name} not written"
        assert path.stat().st_size > 0, f"{name} is empty"


def test_finalize_spec_without_brief_uses_legacy_prompt(tmp_path):
    """No brief_v2 → legacy SYSTEM_PROMPT + payload contains only run_id + approved_answers."""
    llm = _CapturingLLM(_SPEC_RESPONSE)
    finalize_spec(APPROVED_ANSWERS, "r1", llm, output_dir=tmp_path)
    assert llm.last_system == SYSTEM_PROMPT
    payload = json.loads(llm.last_user)
    assert payload["run_id"] == "r1"
    assert payload["approved_answers"] == APPROVED_ANSWERS
    assert "brief" not in payload
    assert "assumptions" not in payload


def test_finalize_spec_with_brief_v2_uses_v2_prompt(tmp_path):
    """brief_v2 provided → SYSTEM_PROMPT_BRIEF_V2 + brief + assumptions in payload."""
    llm = _CapturingLLM(_SPEC_RESPONSE)
    finalize_spec(APPROVED_ANSWERS, "r1", llm, output_dir=tmp_path, brief_v2=BRIEF_V2)
    assert llm.last_system == SYSTEM_PROMPT_BRIEF_V2
    payload = json.loads(llm.last_user)
    assert payload["brief"]["brief_version"] == 2
    assert payload["brief"]["fields"]["problem_statement"]["value"] == "Users can't reset passwords"
    assert payload["approved_answers"] == APPROVED_ANSWERS
    assert payload["assumptions"] == ["primary_user"]


def test_finalize_spec_with_brief_v2_missing_version_falls_back(tmp_path):
    """A dict that lacks brief_version=2 is treated as no brief (defensive)."""
    llm = _CapturingLLM(_SPEC_RESPONSE)
    finalize_spec(APPROVED_ANSWERS, "r1", llm, output_dir=tmp_path, brief_v2={"some": "garbage"})
    assert llm.last_system == SYSTEM_PROMPT
