"""Stage 2 — Question Materializer.

Converts each `Decision` into one or more `Question` objects. Operates
in batch mode by default (one LLM call for all decisions); on JSON
parse failure falls back to per-decision calls (spec M4.2).

Classification rule (locked):
- REQUIRED when `blocks_what` is non-empty AND `has_safe_default` is False
- STRATEGIC when `blocks_what` is non-empty AND `has_safe_default` is True
- OPTIONAL when `blocks_what` is empty

Modes:
- "fresh": materialize all decisions (called by `pipeline.run`)
- "retrigger": materialize only the given decisions and append to the
  existing question list (called by Gate 1 G8, per
  `2026-05-25-g8-brief-edit-retrigger.md`). Question IDs gain a
  `-r{N}` suffix in retrigger mode; assignment is the caller's job.
"""

from pathlib import Path
from typing import Literal

from ai_dev_system.debate.questions.models import Decision
from ai_dev_system.debate.report import Question

PROMPT_PATH = Path(__file__).parent / "prompts" / "materializer.txt"

Mode = Literal["fresh", "retrigger"]


class MaterializerError(RuntimeError):
    """Materialization failed in both batch and per-decision fallback."""


def load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def run(
    decisions: list[Decision],
    brief_digest: str,
    llm_client,
    *,
    mode: Mode = "fresh",
) -> list[Question]:
    """Execute Stage 2.

    Args:
        decisions: Output of Stage 1 (or a subset for retrigger).
        brief_digest: 500-token compressed brief (locked decision #2).
        llm_client: object with `.complete(system, user) -> str`.
        mode: "fresh" or "retrigger".

    Returns:
        Validated `list[Question]`. Domains resolved via
        `debate.domains.resolve_domain`; unknown domains emit
        DOMAIN_UNRECOGNIZED via warnings.warn (caller is expected to
        route those warnings into the event stream).

    Raises:
        MaterializerError: both batch and per-decision attempts failed.
    """
    raise NotImplementedError("M4.2 — implement Materializer stage")
