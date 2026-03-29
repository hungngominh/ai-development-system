from dataclasses import dataclass
from typing import Literal, Protocol


class Gate2IO(Protocol):
    def present_graph(self, graph_envelope: dict) -> None: ...
    def collect_edits(self, graph_envelope: dict) -> tuple[Literal["approve", "reject"], dict]: ...


@dataclass
class Gate2Result:
    status: Literal["approved", "rejected"]
    graph: dict


def run_gate_2(graph_envelope: dict, io: Gate2IO) -> Gate2Result:
    io.present_graph(graph_envelope)
    action, edited = io.collect_edits(graph_envelope)
    if action == "approve":
        return Gate2Result(status="approved", graph=edited)
    return Gate2Result(status="rejected", graph=graph_envelope)
