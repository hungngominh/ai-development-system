"""Unit tests for phase-b to-gate2 and phase-b resume-gate2 CLI entry points.

TDD: tests written BEFORE implementation. RED → GREEN.

Strategy:
- Patch run_phase_b_to_gate2 / resume_phase_b_after_gate2 to assert the CLI
  wires arguments correctly (the pipeline functions themselves are tested in
  tests/integration/test_phase_b_gate2_pause_resume.py).
- One end-to-end flow per entry point using StubAgent + stub LLM (AI_DEV_STUB_LLM=1).
- PipelineAborted on reject → CLI must exit 0 (reject is a normal outcome).
"""
from __future__ import annotations

import json
import sys
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers: import the two entry-point main functions
# ---------------------------------------------------------------------------

def _import_gate2_main():
    """Import main() from the gate2 CLI module."""
    from ai_dev_system.cli.run_phase_b_gate2 import main
    return main


# ---------------------------------------------------------------------------
# Test: to-gate2 command wires arguments correctly (patching the pipeline fn)
# ---------------------------------------------------------------------------

class TestToGate2Command:
    """phase-b to-gate2 --run-id R"""

    def test_calls_pipeline_with_run_id(self, monkeypatch, tmp_path):
        """CLI calls run_phase_b_to_gate2 with the supplied run_id."""
        monkeypatch.setenv("AI_DEV_STUB_LLM", "1")
        monkeypatch.setenv("AI_DEV_DATABASE_URL", "sqlite:///:memory:")
        monkeypatch.setenv("AI_DEV_STORAGE_ROOT", str(tmp_path / "storage"))

        captured_kwargs = {}

        def fake_to_gate2(run_id, config, conn_factory, llm_client, *, llm_for=None):
            captured_kwargs["run_id"] = run_id
            captured_kwargs["llm_client"] = llm_client
            captured_kwargs["llm_for"] = llm_for
            return {"run_id": run_id, "task_graph_gen_id": "abc", "envelope": {}}

        main = _import_gate2_main()
        with patch(
            "ai_dev_system.cli.run_phase_b_gate2.run_phase_b_to_gate2",
            side_effect=fake_to_gate2,
        ):
            rc = main(["--run-id", "RUN-001", "--mode", "to-gate2"])

        assert rc == 0
        assert captured_kwargs["run_id"] == "RUN-001"
        # In stub mode, llm_client is a _StubPhaseBLLM, llm_for is None
        assert captured_kwargs["llm_client"] is not None
        assert captured_kwargs["llm_for"] is None  # stub mode skips the per-step resolver

    def test_outputs_json_status(self, monkeypatch, tmp_path, capsys):
        """to-gate2 prints a JSON status to stdout on success."""
        monkeypatch.setenv("AI_DEV_STUB_LLM", "1")
        monkeypatch.setenv("AI_DEV_DATABASE_URL", "sqlite:///:memory:")
        monkeypatch.setenv("AI_DEV_STORAGE_ROOT", str(tmp_path / "storage"))

        def fake_to_gate2(run_id, config, conn_factory, llm_client, *, llm_for=None):
            return {"run_id": run_id, "task_graph_gen_id": "XYZ", "envelope": {}}

        main = _import_gate2_main()
        with patch(
            "ai_dev_system.cli.run_phase_b_gate2.run_phase_b_to_gate2",
            side_effect=fake_to_gate2,
        ):
            rc = main(["--run-id", "RUN-001", "--mode", "to-gate2"])

        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["run_id"] == "RUN-001"
        assert data["status"] == "PAUSED_AT_GATE_2"

    def test_pipeline_error_exits_1(self, monkeypatch, tmp_path, capsys):
        """to-gate2 exits 1 on unexpected pipeline error."""
        monkeypatch.setenv("AI_DEV_STUB_LLM", "1")
        monkeypatch.setenv("AI_DEV_DATABASE_URL", "sqlite:///:memory:")
        monkeypatch.setenv("AI_DEV_STORAGE_ROOT", str(tmp_path / "storage"))

        def fake_to_gate2(*a, **kw):
            raise RuntimeError("something broke")

        main = _import_gate2_main()
        with patch(
            "ai_dev_system.cli.run_phase_b_gate2.run_phase_b_to_gate2",
            side_effect=fake_to_gate2,
        ):
            rc = main(["--run-id", "RUN-001", "--mode", "to-gate2"])

        assert rc == 1
        captured = capsys.readouterr()
        assert "something broke" in captured.err


# ---------------------------------------------------------------------------
# Test: resume-gate2 command — approve path
# ---------------------------------------------------------------------------

class TestResumeGate2ApproveCommand:
    """phase-b resume-gate2 --run-id R --decision approve"""

    def test_calls_pipeline_with_approve(self, monkeypatch, tmp_path):
        """CLI calls resume_phase_b_after_gate2 with decision='approve' and non-None agent."""
        monkeypatch.setenv("AI_DEV_STUB_LLM", "1")
        monkeypatch.setenv("AI_DEV_DATABASE_URL", "sqlite:///:memory:")
        monkeypatch.setenv("AI_DEV_STORAGE_ROOT", str(tmp_path / "storage"))

        captured_kwargs = {}

        def fake_resume(run_id, config, conn_factory, *, decision, edited_graph=None,
                        agent=None, llm_client=None, llm_for=None):
            captured_kwargs["run_id"] = run_id
            captured_kwargs["decision"] = decision
            captured_kwargs["agent"] = agent
            captured_kwargs["llm_client"] = llm_client
            captured_kwargs["llm_for"] = llm_for
            return None  # stub: execution_result

        main = _import_gate2_main()
        with patch(
            "ai_dev_system.cli.run_phase_b_gate2.resume_phase_b_after_gate2",
            side_effect=fake_resume,
        ):
            rc = main(["--run-id", "RUN-002", "--mode", "resume-gate2", "--decision", "approve"])

        assert rc == 0
        assert captured_kwargs["run_id"] == "RUN-002"
        assert captured_kwargs["decision"] == "approve"
        # agent must be provided (not None) for resume — same agent run_phase_b uses
        assert captured_kwargs["agent"] is not None
        assert captured_kwargs["llm_client"] is not None

    def test_outputs_json_on_approve(self, monkeypatch, tmp_path, capsys):
        """resume-gate2 approve prints JSON status."""
        monkeypatch.setenv("AI_DEV_STUB_LLM", "1")
        monkeypatch.setenv("AI_DEV_DATABASE_URL", "sqlite:///:memory:")
        monkeypatch.setenv("AI_DEV_STORAGE_ROOT", str(tmp_path / "storage"))

        def fake_resume(*a, **kw):
            return None

        main = _import_gate2_main()
        with patch(
            "ai_dev_system.cli.run_phase_b_gate2.resume_phase_b_after_gate2",
            side_effect=fake_resume,
        ):
            rc = main(["--run-id", "RUN-002", "--mode", "resume-gate2", "--decision", "approve"])

        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["run_id"] == "RUN-002"
        assert data["decision"] == "approve"


# ---------------------------------------------------------------------------
# Test: resume-gate2 command — reject path (PipelineAborted → exit 0)
# ---------------------------------------------------------------------------

class TestResumeGate2RejectCommand:
    """phase-b resume-gate2 --run-id R --decision reject → exit 0"""

    def test_reject_exits_0(self, monkeypatch, tmp_path):
        """A reject (PipelineAborted) is a normal outcome → CLI must exit 0."""
        monkeypatch.setenv("AI_DEV_STUB_LLM", "1")
        monkeypatch.setenv("AI_DEV_DATABASE_URL", "sqlite:///:memory:")
        monkeypatch.setenv("AI_DEV_STORAGE_ROOT", str(tmp_path / "storage"))

        from ai_dev_system.pipeline import PipelineAborted

        def fake_resume(*a, **kw):
            raise PipelineAborted("User rejected task graph at Gate 2")

        main = _import_gate2_main()
        with patch(
            "ai_dev_system.cli.run_phase_b_gate2.resume_phase_b_after_gate2",
            side_effect=fake_resume,
        ):
            rc = main(["--run-id", "RUN-003", "--mode", "resume-gate2", "--decision", "reject"])

        assert rc == 0

    def test_reject_outputs_aborted_status(self, monkeypatch, tmp_path, capsys):
        """Reject → JSON output with status 'aborted'."""
        monkeypatch.setenv("AI_DEV_STUB_LLM", "1")
        monkeypatch.setenv("AI_DEV_DATABASE_URL", "sqlite:///:memory:")
        monkeypatch.setenv("AI_DEV_STORAGE_ROOT", str(tmp_path / "storage"))

        from ai_dev_system.pipeline import PipelineAborted

        def fake_resume(*a, **kw):
            raise PipelineAborted("rejected")

        main = _import_gate2_main()
        with patch(
            "ai_dev_system.cli.run_phase_b_gate2.resume_phase_b_after_gate2",
            side_effect=fake_resume,
        ):
            rc = main(["--run-id", "RUN-003", "--mode", "resume-gate2", "--decision", "reject"])

        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["status"] == "aborted"
        assert data["run_id"] == "RUN-003"

    def test_resume_unexpected_error_exits_1(self, monkeypatch, tmp_path, capsys):
        """Unexpected (non-PipelineAborted) errors still exit 1."""
        monkeypatch.setenv("AI_DEV_STUB_LLM", "1")
        monkeypatch.setenv("AI_DEV_DATABASE_URL", "sqlite:///:memory:")
        monkeypatch.setenv("AI_DEV_STORAGE_ROOT", str(tmp_path / "storage"))

        def fake_resume(*a, **kw):
            raise ValueError("DB is locked")

        main = _import_gate2_main()
        with patch(
            "ai_dev_system.cli.run_phase_b_gate2.resume_phase_b_after_gate2",
            side_effect=fake_resume,
        ):
            rc = main(["--run-id", "RUN-003", "--mode", "resume-gate2", "--decision", "approve"])

        assert rc == 1
        captured = capsys.readouterr()
        assert "DB is locked" in captured.err


# ---------------------------------------------------------------------------
# Test: reuse of _make_llm_client / _make_agent from run_phase_b
# ---------------------------------------------------------------------------

class TestFactoryReuse:
    """The gate2 CLI uses the same factory helpers as run_phase_b."""

    def test_uses_stub_llm_in_stub_mode(self, monkeypatch):
        """In AI_DEV_STUB_LLM=1 mode, _make_llm_client returns _StubPhaseBLLM."""
        monkeypatch.setenv("AI_DEV_STUB_LLM", "1")
        from ai_dev_system.cli.run_phase_b import _make_llm_client
        client = _make_llm_client()
        assert hasattr(client, "complete")

    def test_uses_stub_agent_in_stub_mode(self, monkeypatch):
        """In AI_DEV_STUB_LLM=1 mode, _make_agent returns StubAgent."""
        monkeypatch.setenv("AI_DEV_STUB_LLM", "1")
        from ai_dev_system.cli.run_phase_b import _make_agent
        from ai_dev_system.agents.stub import StubAgent
        agent = _make_agent()
        assert isinstance(agent, StubAgent)

    def test_gate2_module_imports_factories_from_run_phase_b(self):
        """run_phase_b_gate2 imports _make_llm_client and _make_agent from run_phase_b."""
        import ai_dev_system.cli.run_phase_b_gate2 as m
        # Module should import or reference the run_phase_b factories
        # (they should not be duplicated)
        import ai_dev_system.cli.run_phase_b as rb
        assert m._make_llm_client is rb._make_llm_client
        assert m._make_agent is rb._make_agent
