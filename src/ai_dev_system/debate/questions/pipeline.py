"""v2 Question Generation pipeline orchestrator.

Wires the 4 stages together. Event emission and artifact persistence
are the caller's responsibility (this module returns a
`PipelineResult`; the orchestrator in `debate_pipeline.py` handles DB
side-effects).

Flow:

    inventory.run(brief_v2)
      -> decisions
    materializer.run(decisions, brief_digest, mode="fresh")
      -> questions_draft
    critic.run(questions_draft, brief_digest)
      -> questions_refined, iterations
    coverage.run(questions_refined, decisions, brief_v2)
      -> report
      if C1 misses some decisions:
        materializer.run(missing_decisions, ..., mode="retrigger")
        merge + coverage.run again (one re-check only)
    return PipelineResult(decisions, questions_final, report, iterations)
"""

from ai_dev_system.debate.questions.models import PipelineResult


def run_pipeline(
    brief_v2: dict,
    brief_digest: str,
    llm_client,
) -> PipelineResult:
    """Run all 4 stages and return the consolidated result.

    Args:
        brief_v2: Promoted intake brief.
        brief_digest: 500-token compressed brief (BRIEF_DIGEST artifact
            content).
        llm_client: object with `.complete(system, user) -> str`.
            Critic uses the same client by default (locked decision
            #9); cross-model A/B is deferred.

    Returns:
        `PipelineResult` with decisions, final questions, and coverage
        report. Caller is responsible for persisting artifacts and
        emitting events.
    """
    raise NotImplementedError("M4.5 — wire pipeline orchestrator")
