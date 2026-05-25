"""Stage 1 — Decision Inventory.

Reads `brief_v2`, asks the LLM to enumerate the atomic decisions a
human must approve before debate begins. Outputs `list[Decision]`,
each tagged with `brief_field_refs` for G8 re-trigger support
(locked decision #36).

Validation (spec M4.1):
- 8 <= len(decisions) <= 25 (`InventoryCountError` otherwise)
- `id` unique
- `domain_hints` resolved via `debate.domains.resolve_domain`
- `blocks_what` references items in `brief.scope_in`

Failure mode: 1 retry with error feedback, then `InventoryError`.
"""

from pathlib import Path

from ai_dev_system.debate.questions.models import Decision

PROMPT_PATH = Path(__file__).parent / "prompts" / "inventory.txt"


class InventoryError(RuntimeError):
    """Inventory stage failed after retry."""


class InventoryCountError(InventoryError):
    """Decision count outside [8, 25]."""


def load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def run(brief_v2: dict, llm_client) -> list[Decision]:
    """Execute Stage 1.

    Args:
        brief_v2: Promoted intake brief (brief_version == 2).
        llm_client: object with `.complete(system, user) -> str`.

    Returns:
        Validated `list[Decision]`.

    Raises:
        InventoryError: parse/validation failure persists after 1 retry.
        InventoryCountError: count outside [8, 25].
    """
    raise NotImplementedError("M4.1 — implement Decision Inventory stage")
