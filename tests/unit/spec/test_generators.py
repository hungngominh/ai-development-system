"""Unit tests for spec generators SP2-SP4.

Tests cover:
- SectionDraft returned by each of the 5 generators when stub LLM succeeds
- Degraded draft returned when LLM raises / returns empty
- build_user_payload includes brief, decisions, outline items
- build_common_system_prompt includes section name and length guide
- generate_with_retry retries up to max_retries, then returns degraded
- run_spec_pipeline writes all 5 files and returns SpecBundle
- run_spec_pipeline marks degraded sections but still writes placeholder files
- SpecPipelineConfig.parallel_sections=False runs sequentially
"""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import MagicMock

from ai_dev_system.debate.questions.models import Decision
from ai_dev_system.spec.generators.base import (
    SectionDraft,
    SECTION_FILES,
    build_common_system_prompt,
    build_user_payload,
    generate_with_retry,
)
from ai_dev_system.spec.generators.proposal import generate_proposal
from ai_dev_system.spec.generators.design import generate_design
from ai_dev_system.spec.generators.functional import generate_functional
from ai_dev_system.spec.generators.non_functional import generate_non_functional
from ai_dev_system.spec.generators.acceptance_criteria import generate_acceptance_criteria
from ai_dev_system.spec.pipeline import run_spec_pipeline, SpecPipelineConfig
from ai_dev_system.spec.planner import SectionOutline, build_outlines


# ---- helpers ----


def _stub_llm(content: str = "## Section\n\nContent here.") -> MagicMock:
    """Return a stub LLM that always returns a fixed string."""
    client = MagicMock()
    client.complete.return_value = content
    return client


def _failing_llm(exc: Exception = RuntimeError("LLM down")) -> MagicMock:
    """Return a stub LLM that always raises."""
    client = MagicMock()
    client.complete.side_effect = exc
    return client


def _empty_llm() -> MagicMock:
    """Return a stub LLM that always returns empty string."""
    client = MagicMock()
    client.complete.return_value = ""
    return client


def _outline(section: str = "proposal") -> SectionOutline:
    return SectionOutline(
        section=section,
        must_cover=["Problem definition", "Stakeholders"],
        must_reference=["problem_statement", "who_feels_pain"],
        must_not_mention=["Implementation details"],
        assumptions_for_this_section=["Team size TBD"],
    )


def _brief_v2() -> dict:
    return {
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


def _decisions() -> list[Decision]:
    return [
        Decision(id="D1", summary="Auth choice", classification="REQUIRED",
                 domain_hints=["security"], blocks_what=[]),
    ]


# ---- build_common_system_prompt ----


def test_system_prompt_includes_section_name():
    prompt = build_common_system_prompt("proposal", length_guide="300-500 words")
    assert "proposal" in prompt


def test_system_prompt_includes_length_guide():
    prompt = build_common_system_prompt("design", length_guide="1000-1500 words")
    assert "1000-1500 words" in prompt


def test_system_prompt_mentions_inline_ref_format():
    prompt = build_common_system_prompt("functional")
    assert "[brief:" in prompt or "brief:field_name" in prompt


# ---- build_user_payload ----


def test_user_payload_includes_brief_fields():
    outline = _outline()
    brief = _brief_v2()
    payload = build_user_payload(outline, brief, {}, [], include_full_brief=True)
    assert "problem_statement" in payload
    assert "scope_in" in payload


def test_user_payload_includes_decisions():
    outline = _outline()
    decisions = _decisions()
    payload = build_user_payload(outline, _brief_v2(), {}, decisions, include_full_brief=True)
    assert "D1" in payload
    assert "Auth choice" in payload


def test_user_payload_includes_must_cover_items():
    outline = _outline()
    payload = build_user_payload(outline, {}, {}, [], include_full_brief=False)
    assert "Problem definition" in payload
    assert "Stakeholders" in payload


def test_user_payload_includes_must_not_mention():
    outline = _outline()
    payload = build_user_payload(outline, {}, {}, [], include_full_brief=False)
    assert "Implementation details" in payload


def test_user_payload_includes_assumptions():
    outline = _outline()
    payload = build_user_payload(outline, {}, {}, [], include_full_brief=False)
    assert "Team size TBD" in payload


def test_user_payload_includes_approved_answers():
    outline = _outline()
    payload = build_user_payload(outline, {}, {"Q1": "JWT"}, [], include_full_brief=False)
    assert "Q1" in payload
    assert "JWT" in payload


def test_user_payload_without_brief_skips_brief_block():
    outline = _outline()
    payload = build_user_payload(outline, {"problem_statement": "x"}, {}, [], include_full_brief=False)
    assert "PROJECT BRIEF" not in payload


# ---- generate_with_retry ----


def test_generate_with_retry_success():
    llm = _stub_llm("## Proposal\n\nSome content")
    draft = generate_with_retry("sys", "usr", llm, "proposal", max_retries=2)
    assert draft.section == "proposal"
    assert not draft.degraded
    assert "Some content" in draft.content


def test_generate_with_retry_on_failure_returns_degraded():
    llm = _failing_llm()
    draft = generate_with_retry("sys", "usr", llm, "design", max_retries=2)
    assert draft.degraded
    assert draft.error is not None
    assert llm.complete.call_count == 2


def test_generate_with_retry_on_empty_returns_degraded():
    llm = _empty_llm()
    draft = generate_with_retry("sys", "usr", llm, "functional", max_retries=2)
    assert draft.degraded


def test_generate_with_retry_succeeds_on_second_attempt():
    client = MagicMock()
    client.complete.side_effect = [RuntimeError("first fail"), "## Design\n\nOK"]
    draft = generate_with_retry("sys", "usr", client, "design", max_retries=2)
    assert not draft.degraded
    assert "OK" in draft.content


# ---- individual generators (smoke tests with stub) ----


@pytest.mark.parametrize("generator,section", [
    (generate_proposal, "proposal"),
    (generate_design, "design"),
    (generate_functional, "functional"),
    (generate_non_functional, "non_functional"),
    (generate_acceptance_criteria, "acceptance_criteria"),
])
def test_generator_returns_section_draft(generator, section):
    llm = _stub_llm(f"## {section}\n\nSome content for {section}.")
    outline = _outline(section)
    draft = generator(outline, _brief_v2(), {"Q1": "JWT"}, _decisions(), llm)
    assert isinstance(draft, SectionDraft)
    assert draft.section == section
    assert not draft.degraded


@pytest.mark.parametrize("generator,section", [
    (generate_proposal, "proposal"),
    (generate_design, "design"),
    (generate_functional, "functional"),
    (generate_non_functional, "non_functional"),
    (generate_acceptance_criteria, "acceptance_criteria"),
])
def test_generator_degrades_gracefully_on_llm_failure(generator, section):
    llm = _failing_llm()
    outline = _outline(section)
    draft = generator(outline, {}, {}, [], llm)
    assert draft.degraded


# ---- run_spec_pipeline ----


def test_pipeline_writes_all_five_files(tmp_path):
    llm = _stub_llm("## Section\n\nContent.")
    bundle = run_spec_pipeline(
        _brief_v2(), {"Q1": "JWT"}, tmp_path, llm,
        decisions=_decisions(),
    )
    for filename in SECTION_FILES.values():
        assert (tmp_path / filename).exists(), f"Missing {filename}"


def test_pipeline_returns_spec_bundle(tmp_path):
    llm = _stub_llm("## Section\n\nContent.")
    from ai_dev_system.spec_bundle import SpecBundle
    bundle = run_spec_pipeline(_brief_v2(), {}, tmp_path, llm)
    assert isinstance(bundle, SpecBundle)
    assert bundle.version == 2


def test_pipeline_degraded_section_still_writes_placeholder(tmp_path):
    import warnings
    llm = _failing_llm()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        bundle = run_spec_pipeline(_brief_v2(), {}, tmp_path, llm)

    # All 5 files should exist even if degraded
    for filename in SECTION_FILES.values():
        assert (tmp_path / filename).exists()

    # At least one UserWarning about degraded sections
    assert any(issubclass(w.category, UserWarning) for w in caught)


def test_pipeline_sequential_mode(tmp_path):
    llm = _stub_llm("## Section\n\nContent.")
    cfg = SpecPipelineConfig(parallel_sections=False)
    bundle = run_spec_pipeline(_brief_v2(), {}, tmp_path, llm, config=cfg)
    for filename in SECTION_FILES.values():
        assert (tmp_path / filename).exists()


def test_pipeline_creates_output_dir(tmp_path):
    nested = tmp_path / "nested" / "spec"
    llm = _stub_llm("## Section\n\nContent.")
    bundle = run_spec_pipeline(_brief_v2(), {}, nested, llm)
    assert nested.is_dir()


def test_pipeline_file_content_from_llm(tmp_path):
    marker = "UNIQUE_MARKER_9876"
    llm = _stub_llm(f"## Section\n\n{marker}")
    run_spec_pipeline(_brief_v2(), {}, tmp_path, llm)
    # At least one file should contain the marker
    found = any(
        marker in (tmp_path / fn).read_text(encoding="utf-8")
        for fn in SECTION_FILES.values()
    )
    assert found


def test_pipeline_passes_decisions_to_generators(tmp_path):
    """LLM should be called with payload that includes decision IDs."""
    llm = _stub_llm("## Section\n\nContent.")
    decisions = [
        Decision(id="D99", summary="Some decision", classification="REQUIRED",
                 domain_hints=["backend"], blocks_what=[]),
    ]
    run_spec_pipeline(_brief_v2(), {}, tmp_path, llm, decisions=decisions)
    # Check that at least one call included D99 in the user payload
    all_calls = llm.complete.call_args_list
    assert any("D99" in str(call) for call in all_calls)
