"""
Conftest for agents unit tests.

crewai does not install on Python 3.14 (the current environment's Python version),
so we inject a lightweight fake crewai package into sys.modules BEFORE the
implementation module is imported.  All tests patch crewai.* via mocker.patch,
so the fake just needs to satisfy the import and provide placeholder classes.
"""
import sys
import types
import enum


def _make_crewai_stub():
    """Build a minimal crewai stub module tree."""
    mod = types.ModuleType("crewai")

    class Process(enum.Enum):
        sequential = "sequential"
        hierarchical = "hierarchical"

    class LLM:
        def __init__(self, model=None, api_key=None, **kwargs):
            self.model = model
            self.api_key = api_key

    class Agent:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    class Task:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    class Crew:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

        def kickoff(self):
            return "stub kickoff"

    mod.Process = Process
    mod.LLM = LLM
    mod.Agent = Agent
    mod.Task = Task
    mod.Crew = Crew
    mod.__version__ = "0.0.0-stub"
    return mod


# Only inject if crewai is not already importable
if "crewai" not in sys.modules:
    try:
        import crewai  # noqa: F401
    except ImportError:
        sys.modules["crewai"] = _make_crewai_stub()
