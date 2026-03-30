import pytest
from pathlib import Path
from ai_dev_system.finalize_spec import finalize_spec
from ai_dev_system.spec_bundle import SpecBundle
from ai_dev_system.debate.llm import StubDebateLLMClient

APPROVED_ANSWERS = {
    "Q1": "Use JWT with short expiry (15 min access, 7 day refresh)",
    "Q2": "PostgreSQL with connection pooling",
    "Q3": "REST API with OpenAPI spec",
}


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
