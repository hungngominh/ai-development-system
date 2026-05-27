"""M5.F.2 spec D10: deprecation shim for legacy 3-line prompts.

The legacy module (`debate.agents.legacy`) is kept alive as a fallback
for callers without an AgentRegistry, but new production code should
use dense .md prompts via the registry. These tests guard the
transition:

1. Engine wired with a registry uses registry.system_prompt for each
   agent, not AGENT_PROMPTS.
2. Engine without a registry still works (v1 fallback).
3. No production module (outside `debate/agents/`) imports legacy
   constants directly — they must go through the package re-export so
   the future removal touches only one seam.
"""

from pathlib import Path

import pytest

from ai_dev_system.debate.agents import (
    AGENT_PROMPTS,
    AgentRegistry,
    AgentSpec,
)
from ai_dev_system.debate.engine import run_debate
from ai_dev_system.debate.llm import StubDebateLLMClient
from ai_dev_system.debate.report import Question


STRATEGIC_Q = Question(
    id="Q1", text="DB?", classification="STRATEGIC",
    domain="database",
    agent_a="DatabaseSpecialist", agent_b="BackendArchitect",
)


def _spec(key: str, domain: str, *, system_prompt: str) -> AgentSpec:
    return AgentSpec(
        key=key, domain=domain, version=1,
        system_prompt=system_prompt,
    )


class _SystemRecorder:
    def __init__(self):
        self.systems: list[str] = []
        self._stub = StubDebateLLMClient()

    def complete(self, system: str, user: str) -> str:
        self.systems.append(system)
        return self._stub.complete(system, user)


def test_engine_with_registry_uses_dense_prompts():
    """When a registry is wired, agent system prompts come from
    AgentSpec.system_prompt, not the legacy 3-line dict."""
    dense_a = "DENSE-PROMPT-AGENT-A (this string is unique enough to identify)"
    dense_b = "DENSE-PROMPT-AGENT-B (this string is unique enough to identify)"
    registry = AgentRegistry.from_specs([
        _spec("DatabaseSpecialist", "data", system_prompt=dense_a),
        _spec("BackendArchitect", "backend", system_prompt=dense_b),
    ])
    client = _SystemRecorder()
    run_debate([STRATEGIC_Q], client, run_id="r", brief={}, registry=registry)

    agent_systems = [
        s for s in client.systems if "moderator" not in s.lower()
    ]
    assert dense_a in agent_systems
    assert dense_b in agent_systems
    # And the legacy 3-line prompt was NOT used.
    legacy_a = AGENT_PROMPTS["DatabaseSpecialist"]
    legacy_b = AGENT_PROMPTS["BackendArchitect"]
    assert legacy_a not in agent_systems
    assert legacy_b not in agent_systems


def test_engine_without_registry_falls_back_to_legacy_prompts():
    """v1 path: no registry → engine still works, uses AGENT_PROMPTS."""
    client = _SystemRecorder()
    run_debate([STRATEGIC_Q], client, run_id="r", brief={})

    agent_systems = [
        s for s in client.systems if "moderator" not in s.lower()
    ]
    assert AGENT_PROMPTS["DatabaseSpecialist"] in agent_systems
    assert AGENT_PROMPTS["BackendArchitect"] in agent_systems


def test_registry_miss_warns_and_falls_back():
    """Registry without the agent → DeprecationWarning + fallback to
    legacy prompt (so the run still completes, just degraded)."""
    registry = AgentRegistry.from_specs([
        _spec("DatabaseSpecialist", "data", system_prompt="dense-a"),
        # BackendArchitect intentionally missing
    ])
    client = _SystemRecorder()
    with pytest.warns(DeprecationWarning, match="BackendArchitect"):
        run_debate([STRATEGIC_Q], client, run_id="r", brief={}, registry=registry)
    agent_systems = [
        s for s in client.systems if "moderator" not in s.lower()
    ]
    # A used dense, B fell back to legacy.
    assert "dense-a" in agent_systems
    assert AGENT_PROMPTS["BackendArchitect"] in agent_systems


def test_no_production_code_imports_from_agents_legacy_directly():
    """Spec D10 contract: outside the `debate/agents/` package itself,
    nothing should import from `debate.agents.legacy`. Tests are
    exempt — only `src/` is scanned. This guard ensures future
    removal of legacy.py touches a single seam."""
    src_root = Path(__file__).resolve().parents[3] / "src"
    assert src_root.exists(), f"source root not found: {src_root}"

    allowed_dir = src_root / "ai_dev_system" / "debate" / "agents"
    offenders: list[str] = []
    needles = (
        "from ai_dev_system.debate.agents.legacy",
        "import ai_dev_system.debate.agents.legacy",
    )
    for py_file in src_root.rglob("*.py"):
        try:
            allowed = py_file.is_relative_to(allowed_dir)
        except AttributeError:
            allowed = str(py_file).startswith(str(allowed_dir))
        if allowed:
            continue
        text = py_file.read_text(encoding="utf-8", errors="ignore")
        if any(n in text for n in needles):
            offenders.append(str(py_file.relative_to(src_root)))
    assert not offenders, (
        "These files import from debate.agents.legacy directly — "
        "they should import from `debate.agents` instead so the legacy "
        f"module can be removed cleanly: {offenders}"
    )


def test_legacy_module_docstring_marks_deprecated():
    """If someone reads legacy.py expecting active code, the docstring
    must surface the deprecation immediately."""
    import ai_dev_system.debate.agents.legacy as legacy_mod

    doc = legacy_mod.__doc__ or ""
    assert "DEPRECATED" in doc
    assert "v6" in doc or "removal" in doc.lower() or "remove" in doc.lower()
