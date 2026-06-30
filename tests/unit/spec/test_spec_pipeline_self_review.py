"""Unit tests for Stage 3.5: self-review critic wired into run_spec_pipeline.

Tests cover:
- error/placeholder finding for a real section → repair_section called, finding in bundle
- warning/scope_decomposition/global finding → surfaced in bundle, repair_section NOT called
- AI_DEV_SPEC_SELF_REVIEW=0 → self_review NOT called, bundle.self_review_findings == []
- repair_budget=0 → self_review called, finding in bundle, but repair_section NOT called
- error but non-AUTO_REPAIR dimension → finding in bundle, repair_section NOT called
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch, call

import pytest

from ai_dev_system.spec.generators.base import SectionDraft
from ai_dev_system.spec.pipeline import run_spec_pipeline, SpecPipelineConfig
from ai_dev_system.spec.self_review import Finding


# ---- helpers ----


def _brief_v2() -> dict:
    # Use empty scope_in/scope_out so grounding rules don't trigger grounding repairs,
    # isolating the repair_section calls to Stage 3.5 only.
    return {
        "brief_version": 2,
        "problem_statement": "Teams need async comms",
        "who_feels_pain": "engineers",
        "scope_in": [],
        "scope_out": [],
        "nfr_priority": ["latency", "reliability"],
        "success_metric": "DAU > 1000",
        "must_use_stack": ["FastAPI"],
        "must_not_use": ["PHP"],
        "deployment_target": "AWS",
        "compliance": ["GDPR"],
        "known_unknowns": [],
        "assumptions": [],
    }


def _stub_llm(content: str = "## Section\n\nContent here.") -> MagicMock:
    client = MagicMock()
    client.complete.return_value = content
    return client


def _placeholder_finding(section: str = "proposal") -> Finding:
    return Finding(
        section=section,
        dimension="placeholder",
        severity="error",
        message="Section contains TBD placeholder text.",
        fix="Replace TBD with concrete content.",
    )


def _warning_finding() -> Finding:
    return Finding(
        section="global",
        dimension="scope_decomposition",
        severity="warning",
        message="Spec may be too large for a single task graph.",
        fix="Consider splitting into sub-specs.",
    )


# ---- Stage 3.5 main path: error + auto-repair dimension ----


def test_error_placeholder_finding_triggers_repair_and_attaches_finding(tmp_path):
    """placeholder/error/section=proposal → repair_section called, finding in bundle."""
    llm = _stub_llm()
    finding = _placeholder_finding("proposal")

    repaired_draft = SectionDraft(
        section="proposal",
        content="## Proposal\n\nRepaired content.",
        degraded=False,
    )

    with (
        patch("ai_dev_system.spec.pipeline.self_review", return_value=[finding]) as mock_sr,
        patch("ai_dev_system.spec.pipeline.repair_section", return_value=repaired_draft) as mock_repair,
    ):
        cfg = SpecPipelineConfig(parallel_sections=False)
        bundle = run_spec_pipeline(_brief_v2(), {}, tmp_path, llm, config=cfg)

    mock_sr.assert_called_once()
    mock_repair.assert_called_once()
    # Confirm repair was called for the 'proposal' section
    call_args = mock_repair.call_args
    draft_arg = call_args.args[0] if call_args.args else call_args[0][0]
    # The draft passed should be for 'proposal'
    assert draft_arg.section == "proposal"
    assert finding in bundle.self_review_findings


def test_warning_finding_surfaced_but_no_repair(tmp_path):
    """scope_decomposition/warning/global → in bundle, repair_section NOT called."""
    llm = _stub_llm()
    finding = _warning_finding()

    with (
        patch("ai_dev_system.spec.pipeline.self_review", return_value=[finding]) as mock_sr,
        patch("ai_dev_system.spec.pipeline.repair_section") as mock_repair,
    ):
        cfg = SpecPipelineConfig(parallel_sections=False)
        bundle = run_spec_pipeline(_brief_v2(), {}, tmp_path, llm, config=cfg)

    mock_sr.assert_called_once()
    mock_repair.assert_not_called()
    assert finding in bundle.self_review_findings


# ---- Disabled: AI_DEV_SPEC_SELF_REVIEW=0 ----


def test_disabled_env_var_skips_self_review(tmp_path, monkeypatch):
    """AI_DEV_SPEC_SELF_REVIEW=0 → self_review NOT called, bundle.self_review_findings == []."""
    monkeypatch.setenv("AI_DEV_SPEC_SELF_REVIEW", "0")
    llm = _stub_llm()

    with (
        patch("ai_dev_system.spec.pipeline.self_review") as mock_sr,
        patch("ai_dev_system.spec.pipeline.repair_section") as mock_repair,
    ):
        cfg = SpecPipelineConfig(parallel_sections=False)
        bundle = run_spec_pipeline(_brief_v2(), {}, tmp_path, llm, config=cfg)

    mock_sr.assert_not_called()
    mock_repair.assert_not_called()
    assert bundle.self_review_findings == []


# ---- repair_budget=0: finding attached but no repair ----


def test_repair_budget_zero_no_repair_but_finding_attached(tmp_path):
    """repair_budget=0 (max_repair_calls=0) → finding in bundle, repair_section NOT called."""
    llm = _stub_llm()
    finding = _placeholder_finding("design")

    with (
        patch("ai_dev_system.spec.pipeline.self_review", return_value=[finding]),
        patch("ai_dev_system.spec.pipeline.repair_section") as mock_repair,
    ):
        # max_repair_calls=0 means repair_budget starts at 0
        cfg = SpecPipelineConfig(parallel_sections=False, max_repair_calls=0)
        bundle = run_spec_pipeline(_brief_v2(), {}, tmp_path, llm, config=cfg)

    mock_repair.assert_not_called()
    assert finding in bundle.self_review_findings


# ---- non-AUTO_REPAIR dimension at error severity ----


def test_error_non_auto_repair_dimension_no_repair(tmp_path):
    """internal_consistency/error is NOT in AUTO_REPAIR_DIMENSIONS → no repair."""
    llm = _stub_llm()
    finding = Finding(
        section="functional",
        dimension="internal_consistency",
        severity="error",
        message="Requirements contradict design section.",
        fix="Align sections.",
    )

    with (
        patch("ai_dev_system.spec.pipeline.self_review", return_value=[finding]),
        patch("ai_dev_system.spec.pipeline.repair_section") as mock_repair,
    ):
        cfg = SpecPipelineConfig(parallel_sections=False)
        bundle = run_spec_pipeline(_brief_v2(), {}, tmp_path, llm, config=cfg)

    mock_repair.assert_not_called()
    assert finding in bundle.self_review_findings


# ---- mixed findings: repair only eligible ones ----


def test_mixed_findings_only_eligible_repaired(tmp_path):
    """One auto-repairable error + one warning → only error section is repaired."""
    llm = _stub_llm()
    error_finding = _placeholder_finding("functional")
    warn_finding = _warning_finding()

    repaired_draft = SectionDraft(
        section="functional",
        content="## Functional\n\nRepaired.",
        degraded=False,
    )

    with (
        patch(
            "ai_dev_system.spec.pipeline.self_review",
            return_value=[error_finding, warn_finding],
        ),
        patch("ai_dev_system.spec.pipeline.repair_section", return_value=repaired_draft) as mock_repair,
    ):
        cfg = SpecPipelineConfig(parallel_sections=False)
        bundle = run_spec_pipeline(_brief_v2(), {}, tmp_path, llm, config=cfg)

    assert mock_repair.call_count == 1
    assert error_finding in bundle.self_review_findings
    assert warn_finding in bundle.self_review_findings


# ---- Important #2: persist self_review.json in bundle root_dir ----


def test_self_review_json_written_when_findings_non_empty(tmp_path):
    """run_spec_pipeline writes self_review.json in output_dir when findings exist (Important #2).

    Asserts the file exists on disk (real on-disk assertion) and contains the expected finding.
    Non-auto-repairable finding used (scope_decomposition/warning) so repair_section is not called.
    """
    llm = _stub_llm()
    finding = _warning_finding()  # scope_decomposition/warning — not auto-repaired

    with (
        patch("ai_dev_system.spec.pipeline.self_review", return_value=[finding]),
        patch("ai_dev_system.spec.pipeline.repair_section"),
    ):
        cfg = SpecPipelineConfig(parallel_sections=False)
        bundle = run_spec_pipeline(_brief_v2(), {}, tmp_path, llm, config=cfg)

    sr_file = tmp_path / "self_review.json"
    assert sr_file.exists(), "self_review.json must be written to bundle root_dir"
    import json
    data = json.loads(sr_file.read_text(encoding="utf-8"))
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["dimension"] == "scope_decomposition"
    assert data[0]["severity"] == "warning"
    assert data[0]["message"] == "Spec may be too large for a single task graph."


def test_self_review_json_not_written_when_disabled(tmp_path, monkeypatch):
    """When AI_DEV_SPEC_SELF_REVIEW=0, self_review.json must NOT be written."""
    monkeypatch.setenv("AI_DEV_SPEC_SELF_REVIEW", "0")
    llm = _stub_llm()

    with patch("ai_dev_system.spec.pipeline.self_review") as mock_sr:
        cfg = SpecPipelineConfig(parallel_sections=False)
        run_spec_pipeline(_brief_v2(), {}, tmp_path, llm, config=cfg)

    mock_sr.assert_not_called()
    assert not (tmp_path / "self_review.json").exists()


def test_self_review_json_not_written_when_findings_empty(tmp_path):
    """When self_review returns no findings, self_review.json must NOT be written (M1 parity)."""
    llm = _stub_llm()

    with (
        patch("ai_dev_system.spec.pipeline.self_review", return_value=[]),
        patch("ai_dev_system.spec.pipeline.repair_section"),
    ):
        cfg = SpecPipelineConfig(parallel_sections=False)
        run_spec_pipeline(_brief_v2(), {}, tmp_path, llm, config=cfg)

    assert not (tmp_path / "self_review.json").exists()


# ---- section not in drafts dict ----


def test_finding_for_unknown_section_no_repair(tmp_path):
    """Finding for a non-existent section key → no repair (section not in drafts)."""
    llm = _stub_llm()
    finding = Finding(
        section="nonexistent_section",
        dimension="placeholder",
        severity="error",
        message="TBD.",
        fix="Fix it.",
    )

    with (
        patch("ai_dev_system.spec.pipeline.self_review", return_value=[finding]),
        patch("ai_dev_system.spec.pipeline.repair_section") as mock_repair,
    ):
        cfg = SpecPipelineConfig(parallel_sections=False)
        bundle = run_spec_pipeline(_brief_v2(), {}, tmp_path, llm, config=cfg)

    mock_repair.assert_not_called()
    assert finding in bundle.self_review_findings
