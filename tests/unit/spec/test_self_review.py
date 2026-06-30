from __future__ import annotations

import json
from unittest.mock import MagicMock

from ai_dev_system.spec.self_review import (
    Finding, self_review, self_review_enabled,
)


def _critic_stub(findings: list[dict]) -> MagicMock:
    c = MagicMock()
    c.complete.return_value = json.dumps({"findings": findings})
    return c


def test_disabled_returns_empty(monkeypatch):
    monkeypatch.setenv("AI_DEV_SPEC_SELF_REVIEW", "0")
    llm = _critic_stub([{"section": "proposal", "dimension": "placeholder",
                         "severity": "error", "message": "TBD left in", "fix": "fill it"}])
    assert self_review({"proposal": "TBD"}, "project", llm) == []
    llm.complete.assert_not_called()


def test_parses_findings(monkeypatch):
    monkeypatch.delenv("AI_DEV_SPEC_SELF_REVIEW", raising=False)
    llm = _critic_stub([
        {"section": "proposal", "dimension": "placeholder", "severity": "error",
         "message": "TBD", "fix": "fill"},
        {"section": "global", "dimension": "scope_decomposition", "severity": "warning",
         "message": "two features", "fix": "split"},
    ])
    out = self_review({"proposal": "..."}, "project", llm)
    assert len(out) == 2
    assert out[0] == Finding(section="proposal", dimension="placeholder",
                             severity="error", message="TBD", fix="fill")
    assert out[1].dimension == "scope_decomposition"


def test_individual_malformed_finding_is_skipped(monkeypatch):
    """A finding missing required keys is skipped without aborting the rest —
    a named contract Tasks 2-3 rely on."""
    monkeypatch.delenv("AI_DEV_SPEC_SELF_REVIEW", raising=False)
    llm = _critic_stub([
        {"dimension": "placeholder"},  # missing section + message → skipped
        {"section": "design", "dimension": "ambiguity", "severity": "warning",
         "message": "readable two ways", "fix": "pick one"},
    ])
    out = self_review({"design": "..."}, "project", llm)
    assert len(out) == 1 and out[0].section == "design"


def test_malformed_json_returns_empty(monkeypatch):
    monkeypatch.delenv("AI_DEV_SPEC_SELF_REVIEW", raising=False)
    c = MagicMock(); c.complete.return_value = "not json at all"
    assert self_review({"proposal": "..."}, "project", c) == []


def test_llm_failure_returns_empty(monkeypatch):
    monkeypatch.delenv("AI_DEV_SPEC_SELF_REVIEW", raising=False)
    c = MagicMock(); c.complete.side_effect = RuntimeError("down")
    assert self_review({"proposal": "..."}, "project", c) == []


def test_single_task_kind_passes_facets(monkeypatch):
    monkeypatch.delenv("AI_DEV_SPEC_SELF_REVIEW", raising=False)
    llm = _critic_stub([{"section": "global", "dimension": "scope_decomposition",
                         "severity": "error", "message": "3 tasks hiding as one", "fix": "split"}])
    out = self_review({"input": {"status": "filled", "content": "..."}}, "single_task", llm)
    assert len(out) == 1 and out[0].dimension == "scope_decomposition"
    # the critic was actually called with the facets payload
    assert llm.complete.called


def test_enabled_default(monkeypatch):
    monkeypatch.delenv("AI_DEV_SPEC_SELF_REVIEW", raising=False)
    assert self_review_enabled() is True
    monkeypatch.setenv("AI_DEV_SPEC_SELF_REVIEW", "off")
    assert self_review_enabled() is False
