"""Stage 3 — Critic Loop.

Reviews materialized questions and flags 4 issue types:
- SHALLOW: question is vague or asks the obvious
- OUT_OF_SCOPE: targets a scope_out item or a non-decision
- ALREADY_DECIDED: brief already answers it
- DUPLICATE: another question covers the same decision

Action per flag: rewrite | drop | merge | keep. Loops up to
`MAX_CRITIC_ITER` (= 2). Loop-guard per locked decision #10: track
sha256 of question text; if a rewrite produces a previously-seen
hash → force `action = drop` to prevent A→B→A cycles.

Exit conditions:
- flags list empty
- `MAX_CRITIC_ITER` reached
- surviving question count < 5 → emit alert event, return what's left
"""

from pathlib import Path

from ai_dev_system.debate.report import Question

CRITIC_PROMPT_PATH = Path(__file__).parent / "prompts" / "critic.txt"
REWRITE_PROMPT_PATH = Path(__file__).parent / "prompts" / "critic_rewrite.txt"

MAX_CRITIC_ITER = 2
MIN_SURVIVING_QUESTIONS = 5


class CriticError(RuntimeError):
    """Critic stage failed in a non-recoverable way."""


def load_critic_prompt() -> str:
    return CRITIC_PROMPT_PATH.read_text(encoding="utf-8")


def load_rewrite_prompt() -> str:
    return REWRITE_PROMPT_PATH.read_text(encoding="utf-8")


def run(
    questions: list[Question],
    brief_digest: str,
    llm_client,
    *,
    max_iter: int = MAX_CRITIC_ITER,
) -> tuple[list[Question], int]:
    """Execute Stage 3.

    Args:
        questions: Output of Stage 2.
        brief_digest: 500-token compressed brief.
        llm_client: object with `.complete(system, user) -> str`.
        max_iter: hard cap on critic iterations (default 2).

    Returns:
        Tuple `(refined_questions, iterations_run)`. Caller emits
        `CRITIC_ITERATION_DONE` event per iteration with the count.

    Raises:
        CriticError: catastrophic LLM failure (both critic and rewrite
            calls fail). Stage 4 will block on this.
    """
    raise NotImplementedError("M4.3 — implement Critic stage")
