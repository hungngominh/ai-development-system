class StubGate2IO:
    def __init__(self, action: str = "approve", edits: dict | None = None):
        self.action = action
        self.edits = edits
        self.presented = None

    def present_graph(self, graph_envelope: dict) -> None:
        self.presented = graph_envelope

    def collect_edits(self, graph_envelope: dict) -> tuple[str, dict]:
        if self.edits:
            graph_envelope.update(self.edits)
        return self.action, graph_envelope
