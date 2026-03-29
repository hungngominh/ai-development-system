import copy


class StubGateIO:
    """Test double: auto-approves with optional field overrides."""
    def __init__(self, edits: dict | None = None, approve: bool = True):
        self.edits = edits or {}
        self.approve = approve
        self.presented = None

    def present(self, brief: dict) -> None:
        self.presented = brief

    def collect_edit(self, brief: dict) -> dict:
        updated = copy.deepcopy(brief)
        for key, value in self.edits.items():
            if isinstance(value, dict) and isinstance(updated.get(key), dict):
                updated[key].update(value)
            else:
                updated[key] = value
        return updated

    def confirm(self, brief: dict) -> bool:
        return self.approve
