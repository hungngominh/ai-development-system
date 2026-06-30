"""TDD tests for Task 3: spec_single_task returns self-review critic findings.

Tests cover:
- text path (llm provided): findings from self_review included in result
- env disabled (AI_DEV_SPEC_SELF_REVIEW=0): findings == [], self_review not called
- worker: written JSON includes 'findings' key
- agentic path (llm=None, repo_path given): critic built lazily via make_llm_client("critic")
- critic-build failure is non-blocking: findings stays []
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from ai_dev_system.spec.self_review import Finding
from ai_dev_system.task_graph.single_task import spec_single_task


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_finding(**kw):
    defaults = dict(section="global", dimension="scope_decomposition",
                    severity="error", message="3 tasks hiding as one", fix="split")
    defaults.update(kw)
    return Finding(**defaults)


class _FakeLLM:
    """Minimal LLM stub: returns a scripted JSON for generate_task_facets."""
    def __init__(self, facet_response: str):
        self._resp = facet_response
    def complete(self, system, user):
        return self._resp


def _all_filled_json():
    from ai_dev_system.task_graph.facets import SPEC_FACET_KEYS
    return json.dumps({k: {"status": "filled", "content": f"{k} val", "reason": ""} for k in SPEC_FACET_KEYS})


# ---------------------------------------------------------------------------
# Test 1: findings present when self_review enabled (text/llm path)
# ---------------------------------------------------------------------------

def test_spec_single_task_returns_findings_when_enabled(monkeypatch):
    """With self_review enabled, returned dict must include findings from the critic."""
    scripted_finding = _make_finding()

    llm = _FakeLLM(_all_filled_json())

    with patch("ai_dev_system.task_graph.single_task.self_review_enabled", return_value=True), \
         patch("ai_dev_system.task_graph.single_task.self_review", return_value=[scripted_finding]) as mock_sr:

        result = spec_single_task("build a huge monolith", llm)

    assert "findings" in result, "result must have 'findings' key"
    findings = result["findings"]
    assert len(findings) == 1
    f = findings[0]
    assert f["dimension"] == "scope_decomposition"
    assert f["severity"] == "error"
    assert f["message"] == "3 tasks hiding as one"
    assert f["fix"] == "split"
    # self_review was called with ("single_task", llm) as kind and critic_llm
    mock_sr.assert_called_once()
    call_args = mock_sr.call_args
    assert call_args.args[1] == "single_task"


# ---------------------------------------------------------------------------
# Test 2: findings empty when env disabled
# ---------------------------------------------------------------------------

def test_spec_single_task_findings_empty_when_disabled(monkeypatch):
    """When AI_DEV_SPEC_SELF_REVIEW=0, findings == [] and self_review is not called."""
    monkeypatch.setenv("AI_DEV_SPEC_SELF_REVIEW", "0")
    llm = _FakeLLM(_all_filled_json())

    with patch("ai_dev_system.task_graph.single_task.self_review") as mock_sr:
        result = spec_single_task("build a CSV importer", llm)

    # Either 'findings' key is absent or it is an empty list
    findings = result.get("findings", [])
    assert findings == [], f"Expected empty findings, got: {findings}"
    mock_sr.assert_not_called()


# ---------------------------------------------------------------------------
# Test 3: agentic path (llm=None) — critic built lazily via make_llm_client
# ---------------------------------------------------------------------------

def test_spec_single_task_agentic_path_builds_critic_lazily(monkeypatch):
    """When llm=None and repo_path given, a critic client is built lazily."""
    import ai_dev_system.task_graph.single_task as st
    from ai_dev_system.task_graph.facets import FACET_KEYS

    scripted_finding = _make_finding()
    fake_critic = MagicMock()

    # Patch agentic facets to avoid real claude CLI
    def _fake_agentic(task, repo_path, **kw):
        return {k: {"status": "filled", "content": "x", "reason": ""} for k in FACET_KEYS}
    monkeypatch.setattr(st, "generate_task_facets_agentic", _fake_agentic)

    with patch("ai_dev_system.task_graph.single_task.self_review_enabled", return_value=True), \
         patch("ai_dev_system.task_graph.single_task.self_review", return_value=[scripted_finding]) as mock_sr:
        # Patch make_llm_client inside single_task module (imported lazily inside the func)
        with patch("ai_dev_system.llm_factory.make_llm_client", return_value=fake_critic):
            result = spec_single_task("add feature", None, repo_path="/some/repo")

    assert "findings" in result
    assert len(result["findings"]) == 1
    # critic llm used in self_review call should be fake_critic (built lazily)
    mock_sr.assert_called_once()
    # third arg (critic_llm) is fake_critic
    assert mock_sr.call_args.args[2] is fake_critic


# ---------------------------------------------------------------------------
# Test 4: critic-build failure is non-blocking (findings stays [])
# ---------------------------------------------------------------------------

def test_spec_single_task_critic_build_failure_is_nonblocking(monkeypatch):
    """If make_llm_client('critic') raises, findings should be [] not an exception."""
    import ai_dev_system.task_graph.single_task as st
    from ai_dev_system.task_graph.facets import FACET_KEYS

    def _fake_agentic(task, repo_path, **kw):
        return {k: {"status": "filled", "content": "x", "reason": ""} for k in FACET_KEYS}
    monkeypatch.setattr(st, "generate_task_facets_agentic", _fake_agentic)

    def _boom(step="default"):
        raise RuntimeError("no LLM config")

    with patch("ai_dev_system.task_graph.single_task.self_review_enabled", return_value=True), \
         patch("ai_dev_system.llm_factory.make_llm_client", side_effect=_boom):
        # llm=None so it tries to build critic lazily; building fails
        result = spec_single_task("add feature", None, repo_path="/some/repo")

    # Must not raise; findings stays []
    assert result.get("findings", []) == []


# ---------------------------------------------------------------------------
# Test 5: worker — written JSON includes 'findings' key
# ---------------------------------------------------------------------------

def test_run_worker_includes_findings_in_json(tmp_path, monkeypatch, file_db_url):
    """The JSON file written by run_worker must include a 'findings' key."""
    from ai_dev_system.task_graph import single_task_worker as w
    from ai_dev_system.debate.llm import StubDebateLLMClient

    scripted_finding = _make_finding()

    monkeypatch.setattr(w, "make_llm_client", lambda step="default": StubDebateLLMClient())
    # Patch spec_single_task in the worker module to return findings
    real_spec = w.spec_single_task
    def _spec_with_findings(idea, llm, **kw):
        base = real_spec(idea, llm, **kw)
        base["findings"] = [f.__dict__ for f in [scripted_finding]]
        return base
    monkeypatch.setattr(w, "spec_single_task", _spec_with_findings)

    path = w.run_worker("wcfind01", "build a monolith", None,
                        storage_root=str(tmp_path), database_url=file_db_url)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["status"] == "done"
    assert "findings" in data, "worker JSON must include 'findings' key"
    assert isinstance(data["findings"], list)
    assert len(data["findings"]) == 1
    assert data["findings"][0]["dimension"] == "scope_decomposition"


# ---------------------------------------------------------------------------
# Test 6: worker with no findings from spec (empty list propagated)
# ---------------------------------------------------------------------------

def test_run_worker_findings_empty_list_when_none_returned(tmp_path, monkeypatch, file_db_url):
    """When spec_single_task returns no findings, worker JSON has findings: []."""
    from ai_dev_system.task_graph import single_task_worker as w
    from ai_dev_system.debate.llm import StubDebateLLMClient

    monkeypatch.setattr(w, "make_llm_client", lambda step="default": StubDebateLLMClient())
    # spec_single_task returns no 'findings' key (old behavior) — worker should still include []
    real_spec = w.spec_single_task
    def _spec_no_findings(idea, llm, **kw):
        base = real_spec(idea, llm, **kw)
        base.pop("findings", None)  # simulate missing key
        return base
    monkeypatch.setattr(w, "spec_single_task", _spec_no_findings)

    path = w.run_worker("wcfind02", "add CSV import", None,
                        storage_root=str(tmp_path), database_url=file_db_url)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["status"] == "done"
    assert "findings" in data
    assert data["findings"] == []
