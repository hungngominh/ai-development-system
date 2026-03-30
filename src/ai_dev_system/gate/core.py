from dataclasses import dataclass
from typing import Literal


@dataclass
class GateResult:
    status: Literal["approved", "rejected"]
    brief: dict


def run_gate_1(brief: dict, io) -> GateResult:
    """Present brief, collect edits, confirm. IO-agnostic."""
    io.present(brief)
    updated = io.collect_edit(brief)
    if io.confirm(updated):
        return GateResult(status="approved", brief=updated)
    return GateResult(status="rejected", brief=brief)
