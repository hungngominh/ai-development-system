"""Unit tests for spec.repair (SP6).

Tests cover:
- repair_section returns a new SectionDraft
- repair payload includes REPAIR TASK block with violation messages
- repair payload includes original draft content
- repair payload includes section outline (must_cover items)
- repair returns degraded draft when LLM fails
- pipeline integration: repair is triggered when grounding finds errors
- pipeline integration: repair budget limits total repair calls
- pipeline integration: max_repair_iterations=0 disables repair
"""

from __future__ import annotations

import warnings
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

from ai_dev_system.spec.generators.base import SectionDraft, SECTION_FILES
from ai_dev_system.spec.grounding import GroundingViolation
from ai_dev_system.spec.planner import SectionOutline
from ai_dev_system.spec.repair import repair_section
from ai_dev_system.spec.pipeline import run_spec_pipeline, SpecPipelineConfig


# ---- fixtures ----


def _outline(section: str = "acceptance_criteria") -> SectionOutline:
    return SectionOutline(
        section=section,
        must_cover=["Measurable AC", "Scope coverage"],
        must_reference=["success_metric"],
        must_not_mention=["Implementation"],
        assumptions_for_this_section=[],
    )


def _violations() -> list[GroundingViolation]:
    return [
        GroundingViolation(rule="measurable_ac", message="Vague words: [fast]", severity="error"),
        GroundingViolation(rule="inline_refs", message="Missing [brief:success_metric]", severity="warning"),
    ]


def _stub_llm(content: str = "## Fixed\n\nRepaired content with < 200ms threshold.") -> MagicMock:
    client = MagicMock()
    client.complete.return_value = content
    return client


def _failing_llm() -> MagicMock:
    client = MagicMock()
    client.complete.side_effect = RuntimeError("LLM down")
    return client


def _brief() -> dict:
    return {
        "brief_version": 2,
        "problem_statement": "Teams need async comms",
        "scope_in": ["chat", "notifications"],
        "scope_out": ["mobile app"],
        "nfr_priority": ["latency"],
        "success_metric": "DAU > 1000",
        "assumptions": [],
        "known_unknowns": [],
    }


# ---- repair_section ----


def test_repair_returns_section_draft():
    draft = SectionDraft(section="acceptance_criteria", content="## AC\n\nUsers can do stuff fast.")
    llm = _stub_llm()
    result = repair_section(
        draft, _violations(), _outline(), _brief(), {}, [],
        llm, system_prompt="You are a writer.",
    )
    assert isinstance(result, SectionDraft)
    assert result.section == "acceptance_criteria"


def test_repair_payload_includes_repair_task_block():
    draft = SectionDraft(section="acceptance_criteria", content="## AC\n\nOrig content.")
    captured_user = {}

    def _capture(system, user):
        captured_user["val"] = user
        return "## AC\n\nFixed content < 200ms."

    llm = MagicMock()
    llm.complete.side_effect = _capture

    repair_section(
        draft, _violations(), _outline(), _brief(), {}, [],
        llm, system_prompt="sys",
    )
    assert "REPAIR TASK" in captured_user["val"]
    assert "Vague words" in captured_user["val"]


def test_repair_payload_includes_original_draft():
    draft = SectionDraft(section="acceptance_criteria", content="## AC\n\nORIG_UNIQUE_987.")
    captured_user = {}

    def _capture(system, user):
        captured_user["val"] = user
        return "## AC\n\nFixed."

    llm = MagicMock()
    llm.complete.side_effect = _capture

    repair_section(
        draft, _violations(), _outline(), _brief(), {}, [],
        llm, system_prompt="sys",
    )
    assert "ORIG_UNIQUE_987" in captured_user["val"]


def test_repair_payload_includes_must_cover_items():
    draft = SectionDraft(section="acceptance_criteria", content="## AC\n\nOriginal.")
    captured_user = {}

    def _capture(system, user):
        captured_user["val"] = user
        return "## Fixed."

    llm = MagicMock()
    llm.complete.side_effect = _capture

    repair_section(
        draft, _violations(), _outline(), _brief(), {}, [],
        llm, system_prompt="sys",
    )
    assert "Measurable AC" in captured_user["val"]


def test_repair_returns_degraded_on_llm_failure():
    draft = SectionDraft(section="acceptance_criteria", content="## AC\n\nOriginal.")
    llm = _failing_llm()
    result = repair_section(
        draft, _violations(), _outline(), _brief(), {}, [],
        llm, system_prompt="sys",
    )
    assert result.degraded


def test_repair_passes_violations_as_error_lines():
    draft = SectionDraft(section="acceptance_criteria", content="## AC\n\nOriginal.")
    captured_user = {}

    def _capture(system, user):
        captured_user["val"] = user
        return "## Fixed."

    llm = MagicMock()
    llm.complete.side_effect = _capture

    violations = [
        GroundingViolation(rule="measurable_ac", message="Vague word X", severity="error"),
        GroundingViolation(rule="inline_refs", message="Missing Y", severity="warning"),
    ]
    repair_section(
        draft, violations, _outline(), _brief(), {}, [],
        llm, system_prompt="sys",
    )
    assert "[ERROR]" in captured_user["val"]
    assert "[WARNING]" in captured_user["val"]


# ---- pipeline integration ----


def test_pipeline_triggers_repair_on_grounding_errors(tmp_path):
    """When first draft has vague words (no numbers), pipeline calls LLM again for repair."""
    call_count = {"n": 0}

    def _llm_side_effect(system, user):
        call_count["n"] += 1
        if call_count["n"] <= 5:
            # First 5 calls (one per section) — return vague content for AC
            if "acceptance_criteria" in system or "acceptance_criteria" in user:
                return "## AC\n\nThe system responds fast to user actions."
        # Repair call — return fixed content
        return "## AC\n\nResponse time < 200ms. Error rate < 0.1%. [brief:success_metric]."

    llm = MagicMock()
    llm.complete.side_effect = _llm_side_effect

    brief = _brief()
    cfg = SpecPipelineConfig(parallel_sections=False, max_repair_iterations=1, max_repair_calls=5)
    run_spec_pipeline(brief, {}, tmp_path, llm, config=cfg)
    # LLM was called more than 5 times (generation + at least 1 repair)
    assert llm.complete.call_count > 5


def test_pipeline_repair_budget_zero_skips_repair(tmp_path, monkeypatch):
    """max_repair_calls=0 means no repair attempts even if violations found."""
    # This test is about the repair BUDGET, not the critic — pin the critic off so
    # the count stays exactly "one per section" and isn't coupled to Stage 3.5.
    monkeypatch.setenv("AI_DEV_SPEC_SELF_REVIEW", "0")

    llm = MagicMock()
    llm.complete.return_value = "## Section\n\nThe system works properly (fast)."

    cfg = SpecPipelineConfig(parallel_sections=False, max_repair_calls=0)
    run_spec_pipeline(_brief(), {}, tmp_path, llm, config=cfg)
    # 5 generator calls, no repair (budget=0), no critic (pinned off)
    assert llm.complete.call_count == 5


def test_pipeline_max_repair_iterations_zero_disables_repair(tmp_path, monkeypatch):
    """max_repair_iterations=0 disables repair even with errors."""
    monkeypatch.setenv("AI_DEV_SPEC_SELF_REVIEW", "0")  # isolate the repair-budget assertion
    llm = MagicMock()
    llm.complete.return_value = "## Section\n\nWorks properly and fast."

    cfg = SpecPipelineConfig(parallel_sections=False, max_repair_iterations=0)
    run_spec_pipeline(_brief(), {}, tmp_path, llm, config=cfg)
    # 5 generator calls, no repair, no critic (pinned off)
    assert llm.complete.call_count == 5


def test_pipeline_emits_warning_on_remaining_violations_after_repair(tmp_path):
    """After repair, if violations remain, UserWarning is emitted."""
    llm = MagicMock()
    # Always return vague content so violations persist even after repair
    llm.complete.return_value = "## AC\n\nSystem responds fast. Things work properly."

    cfg = SpecPipelineConfig(
        parallel_sections=False,
        max_repair_iterations=1,
        max_repair_calls=5,
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        run_spec_pipeline(_brief(), {}, tmp_path, llm, config=cfg)

    warning_messages = [str(w.message) for w in caught if issubclass(w.category, UserWarning)]
    grounding_warnings = [m for m in warning_messages if "grounding" in m.lower() or "violation" in m.lower()]
    assert grounding_warnings, f"Expected grounding warnings, got: {warning_messages}"
