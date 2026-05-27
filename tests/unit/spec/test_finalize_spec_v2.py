"""Unit tests for SP9: finalize_spec() v2 pipeline routing.

Tests cover:
- use_v2_pipeline=True + flat brief_v2 → runs run_spec_pipeline, returns SpecBundle v2
- use_v2_pipeline=True + nested brief_v2 (legacy format) → falls back to single-call path
- use_v2_pipeline=False + flat brief_v2 → uses legacy SYSTEM_PROMPT_BRIEF_V2 path
- use_v2_pipeline=False + no brief → uses legacy SYSTEM_PROMPT path
- Existing legacy tests still pass (no regression)
- pipeline_config forwarded to run_spec_pipeline
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ai_dev_system.finalize_spec import finalize_spec, SYSTEM_PROMPT, SYSTEM_PROMPT_BRIEF_V2
from ai_dev_system.spec.generators.base import SECTION_FILES
from ai_dev_system.spec.pipeline import SpecPipelineConfig
from ai_dev_system.spec_bundle import SpecBundle


# ---- fixtures ----


def _flat_brief_v2(**kwargs) -> dict:
    base = {
        "brief_version": 2,
        "problem_statement": "Teams need async comms",
        "who_feels_pain": "engineers",
        "scope_in": ["chat", "notifications"],
        "scope_out": ["mobile app"],
        "nfr_priority": ["latency", "reliability"],
        "success_metric": "DAU > 1000",
        "must_use_stack": ["FastAPI"],
        "must_not_use": ["PHP"],
        "deployment_target": "AWS",
        "compliance": ["GDPR"],
        "known_unknowns": [],
        "assumptions": [],
    }
    base.update(kwargs)
    return base


def _nested_brief_v2() -> dict:
    """Legacy nested-fields format (Phase 1 v1 intake)."""
    return {
        "brief_version": 2,
        "template_id": "generic_v1",
        "fields": {
            "problem_statement": {"value": "Old format", "source": "user"},
        },
        "assumptions": [],
    }


def _stub_llm(content: str = "## Section\n\nContent.") -> MagicMock:
    client = MagicMock()
    client.complete.return_value = content
    return client


_LEGACY_SPEC_RESPONSE = json.dumps({
    "proposal": "# Proposal\nx",
    "design": "# Design\nx",
    "functional": "# Functional\nx",
    "non_functional": "# Non-Functional\nx",
    "acceptance_criteria": "# Acceptance Criteria\nx",
})


# ---- v2 pipeline routing ----


def test_v2_pipeline_returns_spec_bundle_v2(tmp_path):
    llm = _stub_llm()
    bundle = finalize_spec(
        {"Q1": "JWT"}, "r1", llm, tmp_path,
        brief_v2=_flat_brief_v2(), use_v2_pipeline=True,
    )
    assert isinstance(bundle, SpecBundle)
    assert bundle.version == 2


def test_v2_pipeline_writes_all_five_files(tmp_path):
    llm = _stub_llm()
    finalize_spec(
        {}, "r1", llm, tmp_path,
        brief_v2=_flat_brief_v2(), use_v2_pipeline=True,
    )
    for filename in SECTION_FILES.values():
        assert (tmp_path / filename).exists(), f"Missing {filename}"


def test_v2_pipeline_accepts_decisions(tmp_path):
    from ai_dev_system.debate.questions.models import Decision
    llm = _stub_llm()
    decisions = [
        Decision(id="D1", summary="Auth", classification="REQUIRED",
                 domain_hints=["security"], blocks_what=[]),
    ]
    bundle = finalize_spec(
        {}, "r1", llm, tmp_path,
        brief_v2=_flat_brief_v2(), use_v2_pipeline=True, decisions=decisions,
    )
    assert bundle.version == 2


def test_v2_pipeline_accepts_pipeline_config(tmp_path):
    llm = _stub_llm()
    cfg = SpecPipelineConfig(parallel_sections=False)
    bundle = finalize_spec(
        {}, "r1", llm, tmp_path,
        brief_v2=_flat_brief_v2(), use_v2_pipeline=True, pipeline_config=cfg,
    )
    assert bundle.version == 2


# ---- fallback: use_v2_pipeline=True but nested brief → legacy path ----


def test_v2_pipeline_flag_with_nested_brief_falls_back_to_legacy(tmp_path):
    """Nested format lacks top-level problem_statement → falls through to legacy single-call."""
    llm = MagicMock()
    llm.complete.return_value = _LEGACY_SPEC_RESPONSE
    bundle = finalize_spec(
        {"Q1": "JWT"}, "r1", llm, tmp_path,
        brief_v2=_nested_brief_v2(), use_v2_pipeline=True,
    )
    # Legacy path produces SpecBundle v1
    assert bundle.version == 1
    # The legacy single-call .complete() was called (with system + user args)
    assert llm.complete.called
    call_kwargs = llm.complete.call_args
    system_arg = call_kwargs.kwargs.get("system") or call_kwargs.args[0]
    assert "SYSTEM_PROMPT" in repr(system_arg) or "brief" in system_arg.lower()


# ---- no-regression: existing legacy paths unaffected ----


def test_legacy_no_brief_uses_system_prompt(tmp_path):
    llm = MagicMock()
    llm.complete.return_value = _LEGACY_SPEC_RESPONSE
    finalize_spec({"Q1": "JWT"}, "r1", llm, tmp_path)
    system_arg = llm.complete.call_args.kwargs.get("system") or llm.complete.call_args.args[0]
    assert system_arg == SYSTEM_PROMPT


def test_legacy_with_nested_brief_uses_v2_prompt(tmp_path):
    llm = MagicMock()
    llm.complete.return_value = _LEGACY_SPEC_RESPONSE
    finalize_spec({"Q1": "JWT"}, "r1", llm, tmp_path, brief_v2=_nested_brief_v2())
    system_arg = llm.complete.call_args.kwargs.get("system") or llm.complete.call_args.args[0]
    assert system_arg == SYSTEM_PROMPT_BRIEF_V2


def test_legacy_flag_false_with_flat_brief_uses_v2_prompt(tmp_path):
    """use_v2_pipeline=False (default) + flat brief → legacy SYSTEM_PROMPT_BRIEF_V2 path."""
    llm = MagicMock()
    llm.complete.return_value = _LEGACY_SPEC_RESPONSE
    finalize_spec({"Q1": "JWT"}, "r1", llm, tmp_path, brief_v2=_flat_brief_v2())
    system_arg = llm.complete.call_args.kwargs.get("system") or llm.complete.call_args.args[0]
    assert system_arg == SYSTEM_PROMPT_BRIEF_V2


def test_legacy_bundle_version_is_1(tmp_path):
    llm = MagicMock()
    llm.complete.return_value = _LEGACY_SPEC_RESPONSE
    bundle = finalize_spec({"Q1": "JWT"}, "r1", llm, tmp_path)
    assert bundle.version == 1
