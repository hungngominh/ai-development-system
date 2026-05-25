"""Question generation pipeline.

Phase 1 v2 introduces a 4-stage pipeline (Inventory -> Materializer ->
Critic -> Coverage). The legacy single-call generator is retained for
backward compat while `use_question_pipeline_v2` ships.

Public surface:

- `generate_questions(brief, llm_client)` — legacy v1 (re-export from
  `.legacy`). Kept stable for existing callers.
- `SYSTEM_PROMPT`, `SYSTEM_PROMPT_BRIEF_V2` — legacy prompt constants
  re-exported for tests.
- `run_pipeline(...)` — v2 orchestrator (see `.pipeline`).
- `Decision`, `CoverageReport`, `CoverageCheck` — v2 data models.
"""

from ai_dev_system.debate.questions.legacy import (
    SYSTEM_PROMPT,
    SYSTEM_PROMPT_BRIEF_V2,
    generate_questions,
)
from ai_dev_system.debate.questions.models import (
    CoverageCheck,
    CoverageReport,
    Decision,
    PipelineResult,
)
from ai_dev_system.debate.questions.pipeline import run_pipeline

__all__ = [
    "generate_questions",
    "SYSTEM_PROMPT",
    "SYSTEM_PROMPT_BRIEF_V2",
    "run_pipeline",
    "Decision",
    "CoverageCheck",
    "CoverageReport",
    "PipelineResult",
]
