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
from ai_dev_system.spec_bundle import SpecBundle


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

    # Warn on degraded sections
    for draft in drafts.values():
        if draft.degraded:
            warnings.warn(
                f"Spec section '{draft.section}' generation degraded: {draft.error}. "
                "Placeholder written — review and fill manually.",
                UserWarning,
                stacklevel=2,
            )

    # Stage 4: Write files
    files: dict[str, Path] = {}
    for section, filename in SECTION_FILES.items():
        draft = drafts.get(section)
        content = draft.content if draft else f"# {filename}\n\n*(Not generated)*"
        path = output_dir / filename
        path.write_text(content, encoding="utf-8")
        files[filename] = path

    return SpecBundle(version=2, root_dir=output_dir, files=files)


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
