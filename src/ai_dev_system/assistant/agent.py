"""Assistant — ties harness + memory + sessions + budget into one turn.

respond(): load memory → build prompt (base + memory) → fetch recent-history window
→ render the user turn with history → run the harness → persist both turns + usage."""
from __future__ import annotations

from ai_dev_system.harness.runtime import TurnResult
from ai_dev_system.assistant.prompt import build_system_prompt, render_user_turn


class Assistant:
    def __init__(self, *, runtime, memory_store, session_store, budget,
                 base_prompt: str, session_id: str, window: int = 10,
                 cap_usd: float | None = None) -> None:
        self._runtime = runtime
        self._memory_store = memory_store
        self._session_store = session_store
        self._budget = budget
        self._base_prompt = base_prompt
        self._session_id = session_id
        self._window = window
        self._cap_usd = cap_usd

    def mark_resume(self) -> None:
        """Mark this session as resumed after a non-clean shutdown."""
        self._session_store.set_status(self._session_id, "resume_pending")

    def respond(self, user_text: str) -> TurnResult:
        if self._budget.over_cap(self._session_id, self._cap_usd):
            total = self._budget.session_total(self._session_id)
            return TurnResult(
                final_text=(
                    f"Budget cap reached (${total.cost_usd:.4f} >= ${self._cap_usd}). "
                    "Raise AI_DEV_ASSISTANT_BUDGET_USD or start a new session."
                ),
                events=[], usage={}, cost_usd=None, session_id=self._session_id,
            )
        mem = self._memory_store.load()
        system_prompt = build_system_prompt(self._base_prompt, mem)
        history = self._session_store.recent(self._session_id, self._window)
        composed = render_user_turn(history, user_text)

        result = self._runtime.run_turn(system_prompt, composed)

        usage = result.usage or {}
        self._session_store.append(self._session_id, "user", user_text)
        self._session_store.append(
            self._session_id, "assistant", result.final_text,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            cost_usd=result.cost_usd,
        )
        return result
