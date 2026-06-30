# src/ai_dev_system/spec/pipeline.py
"""Spec Generation v2 — Pipeline orchestrator (SP4).

Runs the 5 section generators in parallel (ThreadPoolExecutor) and
assembles the SpecBundle. Integrates with the planner (SP1) and the
grounding checker (SP5, optional).

This module is the main entry point called by the updated `finalize_spec()`
wrapper in finalize_spec.py (SP9 backward compat layer).
"""

from __future__ import annotations

import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from ai_dev_system.spec.generators.base import SECTION_FILES, SectionDraft
from ai_dev_system.spec.generators.proposal import generate_proposal
from ai_dev_system.spec.generators.design import generate_design
from ai_dev_system.spec.generators.functional import generate_functional
from ai_dev_system.spec.generators.non_functional import generate_non_functional
from ai_dev_system.spec.generators.acceptance_criteria import generate_acceptance_criteria
from ai_dev_system.spec.planner import SectionOutline, build_outlines, PlannerOutput
from ai_dev_system.spec.grounding import check_section, llm_grounding_check, GroundingReport, GroundingViolation
from ai_dev_system.spec.repair import repair_section
from ai_dev_system.spec.tracer import build_trace_map, write_trace_map
from ai_dev_system.spec_bundle import SpecBundle
from ai_dev_system.spec.self_review import self_review, self_review_enabled, AUTO_REPAIR_DIMENSIONS

# System prompts for repair — mirrors what each generator passes to LLM
from ai_dev_system.spec.generators.proposal import _SYSTEM_PROMPT as _PROMPT_PROPOSAL
from ai_dev_system.spec.generators.design import _SYSTEM_PROMPT as _PROMPT_DESIGN
from ai_dev_system.spec.generators.functional import _SYSTEM_PROMPT as _PROMPT_FUNCTIONAL
from ai_dev_system.spec.generators.non_functional import _SYSTEM_PROMPT as _PROMPT_NON_FUNCTIONAL
from ai_dev_system.spec.generators.acceptance_criteria import _SYSTEM_PROMPT as _PROMPT_AC

_SYSTEM_PROMPT_MAP = {
    "proposal": _PROMPT_PROPOSAL,
    "design": _PROMPT_DESIGN,
    "functional": _PROMPT_FUNCTIONAL,
    "non_functional": _PROMPT_NON_FUNCTIONAL,
    "acceptance_criteria": _PROMPT_AC,
}


@dataclass
class SpecPipelineConfig:
    parallel_sections: bool = True
    max_repair_iterations: int = 1
    max_repair_calls: int = 5
    grounding_llm_check: bool = False   # SP7 — off by default until implemented
    fail_on_violations: bool = False
    require_trace_map: bool = False     # SP8 — off by default until implemented
    section_max_words: dict = field(default_factory=lambda: {
        "proposal": 500, "design": 1500, "functional": 1200,
        "non_functional": 700, "acceptance_criteria": 1000,
    })


_GENERATOR_MAP = {
    "proposal": generate_proposal,
    "design": generate_design,
    "functional": generate_functional,
    "non_functional": generate_non_functional,
    "acceptance_criteria": generate_acceptance_criteria,
}


def run_spec_pipeline(
    brief: dict,
    approved_answers: dict,
    output_dir: Path,
    llm_client,
    *,
    decisions=None,
    questions=None,
    config: SpecPipelineConfig | None = None,
) -> SpecBundle:
    """Run the full Spec Generation v2 pipeline.

    Steps:
    1. Planner (deterministic) → SectionOutline × 5
    2. Parallel generators → SectionDraft × 5
    3. (SP5) Grounding checks — rule-based (deferred: runs only if grounding available)
    4. Write files → SpecBundle v2

    Returns a SpecBundle with the 5 spec files. Degraded sections are written
    with a placeholder and a warning is emitted so the caller can alert the user.
    """
    cfg = config or SpecPipelineConfig()
    decisions = decisions or []
    questions = questions or []
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Stage 1: Build outlines
    planner_out: PlannerOutput = build_outlines(brief, approved_answers, decisions, questions)
    outline_by_section = {o.section: o for o in planner_out.outlines}

    # Stage 2: Generate sections
    drafts = _run_generators(cfg, outline_by_section, brief, approved_answers, decisions, llm_client)

    # Stage 3: Grounding + repair (SP5-SP6)
    grounding_reports: dict[str, GroundingReport] = {}
    repair_budget = cfg.max_repair_calls
    for section, draft in list(drafts.items()):
        if draft.degraded or repair_budget <= 0:
            continue
        outline = outline_by_section.get(section) or _fallback_outline(section)
        report = check_section(section, draft.content, outline, brief)
        grounding_reports[section] = report

        if report.has_errors and cfg.max_repair_iterations > 0 and repair_budget > 0:
            repaired = repair_section(
                draft, report.violations, outline,
                brief, approved_answers, decisions, llm_client,
                system_prompt=_SYSTEM_PROMPT_MAP[section],
            )
            repair_budget -= 1
            drafts[section] = repaired
            # Re-check after repair (informational — no further repair attempt)
            grounding_reports[section] = check_section(
                section, repaired.content, outline, brief,
            )

    # SP7: LLM hallucination check (optional, batched single call)
    if cfg.grounding_llm_check:
        live_sections = {sec: d.content for sec, d in drafts.items() if not d.degraded}
        llm_violations = llm_grounding_check(live_sections, brief, decisions, llm_client)
        for section, viols in llm_violations.items():
            if section in grounding_reports:
                grounding_reports[section].violations.extend(viols)
            else:
                rpt = GroundingReport(section=section, violations=list(viols))
                grounding_reports[section] = rpt

    # Stage 3.5: Self-review critic + auto-repair routing
    findings = []
    if self_review_enabled():
        drafts_payload = {section: d.content for section, d in drafts.items()}
        findings = self_review(drafts_payload, "project", llm_client)
        for f in findings:
            if (
                f.severity == "error"
                and f.dimension in AUTO_REPAIR_DIMENSIONS
                and f.section in drafts
                and repair_budget > 0
            ):
                outline = outline_by_section.get(f.section) or _fallback_outline(f.section)
                violation = GroundingViolation(
                    rule=f"self_review:{f.dimension}",
                    message=f.message,
                    severity="error",
                )
                repaired = repair_section(
                    drafts[f.section], [violation], outline,
                    brief, approved_answers, decisions, llm_client,
                    system_prompt=_SYSTEM_PROMPT_MAP[f.section],
                )
                repair_budget -= 1
                drafts[f.section] = repaired

    # Warn on degraded sections and grounding errors
    for draft in drafts.values():
        if draft.degraded:
            warnings.warn(
                f"Spec section '{draft.section}' generation degraded: {draft.error}. "
                "Placeholder written — review and fill manually.",
                UserWarning,
                stacklevel=2,
            )
    for section, report in grounding_reports.items():
        if report.has_errors:
            violations_str = "; ".join(v.message for v in report.violations if v.severity == "error")
            warnings.warn(
                f"Spec section '{section}' has grounding violations after repair: {violations_str}",
                UserWarning,
                stacklevel=2,
            )

    # Collect remaining grounding violations for SpecBundle metadata
    remaining_violations = [
        v for report in grounding_reports.values()
        for v in report.violations
    ]

    # Stage 4: Write files
    files: dict[str, Path] = {}
    for section, filename in SECTION_FILES.items():
        draft = drafts.get(section)
        content = draft.content if draft else f"# {filename}\n\n*(Not generated)*"
        path = output_dir / filename
        path.write_text(content, encoding="utf-8")
        files[filename] = path

    # Persist self-review findings alongside the 5 section files so they travel
    # with the SPEC_BUNDLE artifact and are not silently discarded.
    # Only written when non-empty (disabled path: self_review_findings == []).
    import json as _json
    if findings:
        sr_path = output_dir / "self_review.json"
        sr_path.write_text(
            _json.dumps([f.__dict__ for f in findings], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # Stage 5: Trace map (SP8)
    trace_map_path: Path | None = None
    if cfg.require_trace_map:
        trace_map = build_trace_map(drafts, brief, decisions, questions)
        trace_map_path = write_trace_map(trace_map, output_dir)

    return SpecBundle(
        version=2,
        root_dir=output_dir,
        files=files,
        trace_map_path=trace_map_path,
        grounding_violations=remaining_violations,
        self_review_findings=findings,
    )


def _run_generators(
    cfg: SpecPipelineConfig,
    outline_by_section: dict[str, SectionOutline],
    brief: dict,
    approved_answers: dict,
    decisions: list,
    llm_client,
) -> dict[str, SectionDraft]:
    """Run all 5 generators either in parallel or sequentially."""
    if cfg.parallel_sections:
        return _run_parallel(outline_by_section, brief, approved_answers, decisions, llm_client)
    return _run_sequential(outline_by_section, brief, approved_answers, decisions, llm_client)


def _run_parallel(
    outline_by_section: dict[str, SectionOutline],
    brief: dict,
    approved_answers: dict,
    decisions: list,
    llm_client,
) -> dict[str, SectionDraft]:
    results: dict[str, SectionDraft] = {}
    with ThreadPoolExecutor(max_workers=5) as ex:
        future_to_section = {
            ex.submit(
                _GENERATOR_MAP[section],
                outline_by_section.get(section) or _fallback_outline(section),
                brief, approved_answers, decisions, llm_client,
            ): section
            for section in SECTION_FILES
        }
        for future in as_completed(future_to_section):
            section = future_to_section[future]
            try:
                results[section] = future.result()
            except Exception as exc:
                results[section] = SectionDraft(
                    section=section,
                    content=f"## {section}\n\n*(Error: {exc})*",
                    degraded=True, error=str(exc),
                )
    return results


def _run_sequential(
    outline_by_section: dict[str, SectionOutline],
    brief: dict,
    approved_answers: dict,
    decisions: list,
    llm_client,
) -> dict[str, SectionDraft]:
    results: dict[str, SectionDraft] = {}
    for section in SECTION_FILES:
        try:
            results[section] = _GENERATOR_MAP[section](
                outline_by_section.get(section) or _fallback_outline(section),
                brief, approved_answers, decisions, llm_client,
            )
        except Exception as exc:
            results[section] = SectionDraft(
                section=section,
                content=f"## {section}\n\n*(Error: {exc})*",
                degraded=True, error=str(exc),
            )
    return results


def _fallback_outline(section: str) -> SectionOutline:
    """Minimal outline when planner did not produce one for a section."""
    return SectionOutline(
        section=section,
        must_cover=[],
        must_reference=[],
        must_not_mention=[],
        assumptions_for_this_section=[],
    )
