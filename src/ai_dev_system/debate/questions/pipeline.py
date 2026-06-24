"""v2 Question Generation pipeline orchestrator.

Wires the 4 stages together. Event emission and artifact persistence
are the caller's responsibility (this module returns a
`PipelineResult`; the orchestrator in `debate_pipeline.py` handles
DB side-effects).

Flow:

    decisions = inventory.run(brief_v2)
    draft = materializer.run(decisions, brief_digest, mode="fresh")
    refined, iters = critic.run(draft, brief_digest)
    report = coverage.run(refined, decisions, brief_v2)

    if report.C1 fails:
        missing = decisions whose id is in C1.missing_decision_ids
        extra = materializer.run(missing, brief_digest, mode="retrigger")
        refined = refined + extra (with id suffix on collision)
        report = coverage.run(refined, decisions, brief_v2)    # re-check once

    if report.C4 still fails:
        raise CoverageError      # not enough questions to ship

    return PipelineResult(decisions, refined, report, iters)

Persistent C1 failure (after retrigger) is NOT fatal — the report
ships with C1 status=fail so Gate 1 can surface it to the human, who
may approve anyway or edit the brief to retrigger again via G8.
"""

import dataclasses

from ai_dev_system.debate.questions import (
    coverage,
    critic,
    inventory,
    materializer,
)
from ai_dev_system.debate.questions.coverage import CoverageError
from ai_dev_system.debate.questions.models import (
    CoverageReport,
    Decision,
    PipelineResult,
)
from ai_dev_system.debate.report import Question

RETRIGGER_SUFFIX = "r1"


def _find_check(report: CoverageReport, name: str):
    for c in report.checks:
        if c.name == name:
            return c
    return None


def _merge_retrigger_questions(
    refined: list[Question], extra: list[Question]
) -> list[Question]:
    """Append `extra` to `refined`, suffixing any id that collides.

    The pipeline-internal retrigger uses a fixed `-r1` suffix because
    only one re-check is allowed. The G8 (Gate 1) retrigger flow lives
    outside this module and uses its own per-session counter.
    """
    existing_ids = {q.id for q in refined}
    merged = list(refined)
    for q in extra:
        new_id = q.id if q.id not in existing_ids else f"{q.id}-{RETRIGGER_SUFFIX}"
        merged.append(dataclasses.replace(q, id=new_id))
    return merged


def run_pipeline(
    brief_v2: dict,
    brief_digest: str,
    llm_client,
    profile=None,
) -> PipelineResult:
    """Run all 4 stages and return the consolidated result.

    Args:
        brief_v2: Promoted intake brief.
        brief_digest: 500-token compressed brief (BRIEF_DIGEST artifact
            content per locked decision #39).
        llm_client: object with `.complete(system, user) -> str`.
            Critic uses the same client by default (locked decision #9).
        profile: Optional `ProjectProfile` for vertical lens injection.
            Forwarded to inventory + materializer only (coverage gets it
            in Task 7).

    Returns:
        `PipelineResult` with decisions, final questions, coverage
        report, and critic iteration count. Caller persists artifacts
        and emits events.

    Raises:
        InventoryError / MaterializerError / etc: propagated from the
            failing stage.
        CoverageError: C4_question_count failed after the single
            allowed retrigger — the pipeline cannot deliver enough
            questions to seed debate.
    """
    decisions = inventory.run(brief_v2, llm_client, profile=profile)

    draft = materializer.run(
        decisions, brief_digest, llm_client, mode="fresh", profile=profile
    )
    refined, iterations = critic.run(draft, brief_digest, llm_client)
    report = coverage.run(refined, decisions, brief_v2, profile=profile)

    c1 = _find_check(report, "C1_decision_coverage")
    if c1 is not None and c1.status == "fail":
        missing_ids = list(c1.detail.get("missing_decision_ids", []))
        if missing_ids:
            missing_decisions = [d for d in decisions if d.id in missing_ids]
            extra = materializer.run(
                missing_decisions,
                brief_digest,
                llm_client,
                mode="retrigger",
                profile=profile,
            )
            refined = _merge_retrigger_questions(refined, extra)
            report = coverage.run(refined, decisions, brief_v2, profile=profile)

    c4 = _find_check(report, "C4_question_count")
    if c4 is not None and c4.status == "fail":
        raise CoverageError(
            f"C4_question_count failed after retrigger: {c4.detail}. "
            f"Pipeline cannot produce enough questions to seed debate."
        )

    return PipelineResult(
        decisions=decisions,
        questions_final=refined,
        coverage_report=report,
        critic_iterations=iterations,
    )
