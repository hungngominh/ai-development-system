"""Debate progress reporting — a decoupled UX hook for the debate engine.

The engine runs each question's round loop silently, which on a real run
(every LLM call is a ~tens-of-seconds `claude -p` subprocess) leaves the
operator staring at a frozen terminal for many minutes. To fix that without
coupling the engine to stdout/stderr, the engine emits *structured events*
and the caller decides how to render them.

The base class is a no-op sink, so every existing call site that passes
nothing keeps its exact behaviour and tests stay silent. The CLI subclasses
it to print one line per round to stderr.
"""
from __future__ import annotations

from ai_dev_system.debate.report import Question, RoundResult


class DebateProgress:
    """No-op progress sink. Subclass and override to render events.

    Using a concrete no-op base (rather than a Protocol) means
    `progress or DebateProgress()` yields a safe default and subclasses
    only override the events they care about.
    """

    def on_questions(
        self, total: int, required: int, strategic: int, optional: int
    ) -> None:
        """Emitted once, after questions are known, before any debate."""

    def on_question_start(self, index: int, total: int, question: Question) -> None:
        """Emitted when a non-OPTIONAL question's debate begins.

        `index`/`total` count only debated (non-OPTIONAL) questions.
        """

    def on_round(
        self,
        index: int,
        total: int,
        round_num: int,
        max_rounds: int,
        result: RoundResult,
        *,
        is_final: bool,
    ) -> None:
        """Emitted after each completed round. `is_final` is True for the
        round that resolves the question or hits `max_rounds`."""


# Convenience alias for "no progress output".
NullProgress = DebateProgress
