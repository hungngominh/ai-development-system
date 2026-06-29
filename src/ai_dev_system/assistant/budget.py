"""Per-session token/cost rollup, aggregated from assistant_messages."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Budget:
    input_tokens: int
    output_tokens: int
    cost_usd: float


class BudgetTracker:
    def __init__(self, conn_factory) -> None:
        self._conn_factory = conn_factory

    def session_total(self, session_id: str) -> Budget:
        conn = self._conn_factory()
        row = conn.execute(
            "SELECT "
            "COALESCE(SUM(input_tokens),0) AS i, "
            "COALESCE(SUM(output_tokens),0) AS o, "
            "COALESCE(SUM(cost_usd),0.0) AS c "
            "FROM assistant_messages WHERE session_id=?",
            (session_id,),
        ).fetchone()
        return Budget(input_tokens=int(row["i"]), output_tokens=int(row["o"]),
                      cost_usd=float(row["c"]))

    def over_cap(self, session_id: str, cap_usd: float | None) -> bool:
        if cap_usd is None:
            return False
        return self.session_total(session_id).cost_usd >= cap_usd
