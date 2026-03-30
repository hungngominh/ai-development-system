# src/ai_dev_system/debate/engine.py
from datetime import datetime, timezone
from ai_dev_system.debate.report import Question, QuestionDebateResult, DebateReport, auto_resolve
from ai_dev_system.debate.rounds import run_debate_round

MAX_ROUNDS = 5
CONFIDENCE_THRESHOLD = 0.8


def run_debate(
    questions: list[Question],
    llm_client,
    run_id: str,
    brief: dict,
) -> DebateReport:
    """Run debate for all questions. OPTIONAL questions are auto-resolved."""
    results: list[QuestionDebateResult] = []

    for q in questions:
        if q.classification == "OPTIONAL":
            results.append(auto_resolve(q))
            continue

        prev_summary = None
        rounds = []
        for round_num in range(1, MAX_ROUNDS + 1):
            result = run_debate_round(q, round_num, prev_summary, llm_client)
            rounds.append(result)
            if result.confidence >= CONFIDENCE_THRESHOLD:
                break
            prev_summary = result.moderator_summary

        results.append(QuestionDebateResult(question=q, rounds=rounds, final=rounds[-1]))

    return DebateReport(
        run_id=run_id,
        brief=brief,
        results=results,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
