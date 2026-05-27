# src/ai_dev_system/finalize_spec.py
import json
from pathlib import Path
from typing import Optional

from ai_dev_system.spec_bundle import SpecBundle
from ai_dev_system.spec.pipeline import run_spec_pipeline, SpecPipelineConfig

SYSTEM_PROMPT = (
    "You are a technical writer generating a structured spec from approved design decisions. "
    "Given approved_answers (question_id → answer), write a complete spec in 5 sections. "
    "Return ONLY a JSON object with these exact keys: "
    '"proposal", "design", "functional", "non_functional", "acceptance_criteria". '
    "Each value is a Markdown string. Write coherent prose — not template substitution."
)

SYSTEM_PROMPT_BRIEF_V2 = (
    "You are a technical writer generating a structured spec from BOTH an intake brief "
    "and AI-debated, human-approved decisions.\n\n"
    "You will receive:\n"
    "  1. brief: structured project context (problem_statement, primary_user, scope_in, "
    "scope_out, success_metric, nfr_priority, constraints, known_unknowns, etc.). "
    "Fields carry a `source` marker: 'user' (typed), 'ai_suggested_confirmed' (AI proposed, "
    "human confirmed) — treat both as ground truth. 'skipped' = unanswered.\n"
    "  2. approved_answers: question_id → answer, decisions chốt from debate.\n"
    "  3. assumptions: list of brief field_ids the user skipped — surface them.\n\n"
    "Write a 5-section spec where:\n"
    "  - 'proposal' references brief.problem_statement and brief.success_metric verbatim.\n"
    "  - 'functional' covers scope_in, explicitly excludes scope_out.\n"
    "  - 'non_functional' is anchored on brief.nfr_priority order.\n"
    "  - 'design' uses approved_answers + brief constraints as hard requirements.\n"
    "  - 'acceptance_criteria' must be measurable against brief.success_metric.\n\n"
    "If brief.assumptions is non-empty, append a '## Open Questions' subsection in each "
    "relevant file listing the skipped fields that affect that section.\n\n"
    "Return ONLY a JSON object with keys: "
    '"proposal", "design", "functional", "non_functional", "acceptance_criteria". '
    "Each value is a Markdown string."
)

_FILE_MAP = {
    "proposal": "proposal.md",
    "design": "design.md",
    "functional": "functional.md",
    "non_functional": "non-functional.md",
    "acceptance_criteria": "acceptance-criteria.md",
}


def finalize_spec(
    approved_answers: dict,
    run_id: str,
    llm_client,
    output_dir: Path,
    brief_v2: Optional[dict] = None,
    *,
    decisions: Optional[list] = None,
    questions: Optional[list] = None,
    use_v2_pipeline: bool = False,
    pipeline_config: Optional[SpecPipelineConfig] = None,
) -> SpecBundle:
    """Spec generation: approved_answers (+ optional brief v2) → 5-file SpecBundle.

    Routing:
    - use_v2_pipeline=True + brief_v2 (flat format): calls run_spec_pipeline()
      (SP1-SP4 parallel generators). Returns SpecBundle v2.
    - brief_v2 with brief_version==2 (nested format): uses SYSTEM_PROMPT_BRIEF_V2
      (legacy single-call path). Returns SpecBundle v1.
    - No brief_v2: legacy SYSTEM_PROMPT single-call path. Returns SpecBundle v1.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    is_flat_v2 = bool(brief_v2) and brief_v2.get("brief_version") == 2 and "problem_statement" in brief_v2
    if use_v2_pipeline and is_flat_v2:
        return run_spec_pipeline(
            brief_v2, approved_answers, output_dir, llm_client,
            decisions=decisions or [],
            questions=questions or [],
            config=pipeline_config,
        )

    use_brief_v2 = bool(brief_v2) and brief_v2.get("brief_version") == 2
    if use_brief_v2:
        system = SYSTEM_PROMPT_BRIEF_V2
        payload = {
            "run_id": run_id,
            "brief": brief_v2,
            "approved_answers": approved_answers,
            "assumptions": brief_v2.get("assumptions") or [],
        }
    else:
        system = SYSTEM_PROMPT
        payload = {"run_id": run_id, "approved_answers": approved_answers}

    response = llm_client.complete(
        system=system,
        user=json.dumps(payload, ensure_ascii=False),
    )

    try:
        sections = json.loads(response)
    except json.JSONDecodeError:
        sections = {k: f"# {k}\n\n{response}" for k in _FILE_MAP}

    files: dict[str, Path] = {}
    for key, filename in _FILE_MAP.items():
        content = sections.get(key, f"# {filename}\n\n(Not generated)")
        path = output_dir / filename
        path.write_text(content, encoding="utf-8")
        files[filename] = path

    return SpecBundle(version=1, root_dir=output_dir, files=files)
