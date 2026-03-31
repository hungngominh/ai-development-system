# tests/unit/test_debate_pipeline_phase_v.py
"""Unit test: run_phase_b_pipeline() calls run_phase_v_pipeline() after COMPLETED execution."""
import uuid
import os
from unittest.mock import MagicMock, patch
from ai_dev_system.debate_pipeline import run_phase_b_pipeline
from ai_dev_system.engine.runner import ExecutionResult


def _make_phase_b_conn(run_id: str, tmp_path) -> MagicMock:
    """Mock conn that satisfies all queries in run_phase_b_pipeline."""
    import json
    # Set up fake spec bundle dir and approved_answers
    spec_dir = tmp_path / "spec"; spec_dir.mkdir()
    aa_dir = tmp_path / "aa"; aa_dir.mkdir()
    (aa_dir / "approved_answers.json").write_text(json.dumps({"Q1": "yes"}))

    conn = MagicMock()
    def execute(query, params=None):
        cursor = MagicMock()
        q = query.strip().lower()
        if "select status" in q and "current_artifacts" in q:
            cursor.fetchone.return_value = {
                "status": "RUNNING_PHASE_1D",
                "current_artifacts": {"approved_answers_id": "aa-id"},
            }
        elif "from artifacts" in q:
            art_id = (params or [""])[0]
            if art_id == "aa-id":
                cursor.fetchone.return_value = {"content_ref": str(aa_dir)}
            else:
                cursor.fetchone.return_value = {"content_ref": str(spec_dir)}
        else:
            cursor.fetchone.return_value = None
            cursor.fetchall.return_value = []
        return cursor
    conn.execute.side_effect = execute
    return conn


def test_phase_v_pipeline_called_after_completed_execution(tmp_path):
    run_id = str(uuid.uuid4())
    conn = _make_phase_b_conn(run_id, tmp_path)
    stub_agent = MagicMock()
    stub_llm = MagicMock()

    with patch("ai_dev_system.debate_pipeline.finalize_spec") as mock_spec, \
         patch("ai_dev_system.debate_pipeline.generate_task_graph") as mock_tg, \
         patch("ai_dev_system.debate_pipeline.run_gate_2") as mock_g2, \
         patch("ai_dev_system.debate_pipeline.beads_sync"), \
         patch("ai_dev_system.debate_pipeline.promote_output") as mock_promote, \
         patch("ai_dev_system.debate_pipeline.run_execution") as mock_exec, \
         patch("ai_dev_system.verification.pipeline.run_phase_v_pipeline") as mock_phase_v:

        mock_spec.return_value = MagicMock(files=["acceptance-criteria.md"])
        mock_tg.return_value = {"graph_version": 1, "tasks": []}
        g2_result = MagicMock(); g2_result.status = "approved"; g2_result.graph = {}
        mock_g2.return_value = g2_result
        mock_promote.return_value = str(uuid.uuid4())
        mock_exec.return_value = ExecutionResult(run_id=run_id, status="COMPLETED")
        mock_phase_v.return_value = MagicMock()

        config = MagicMock()
        config.storage_root = str(tmp_path / "storage")
        os.makedirs(config.storage_root, exist_ok=True)

        run_phase_b_pipeline(
            run_id=run_id,
            config=config,
            conn_factory=lambda: conn,
            gate2_io=MagicMock(),
            llm_client=stub_llm,
            agent=stub_agent,
        )

    # run_phase_v_pipeline must have been called once
    mock_phase_v.assert_called_once()
    call_args = mock_phase_v.call_args
    assert call_args[0][0] == run_id          # first arg is run_id


def test_phase_v_pipeline_not_called_without_agent(tmp_path):
    """If agent=None, Phase V must not be triggered (backward compat)."""
    run_id = str(uuid.uuid4())
    conn = _make_phase_b_conn(run_id, tmp_path)

    with patch("ai_dev_system.debate_pipeline.finalize_spec") as mock_spec, \
         patch("ai_dev_system.debate_pipeline.generate_task_graph") as mock_tg, \
         patch("ai_dev_system.debate_pipeline.run_gate_2") as mock_g2, \
         patch("ai_dev_system.debate_pipeline.beads_sync"), \
         patch("ai_dev_system.debate_pipeline.promote_output") as mock_promote, \
         patch("ai_dev_system.verification.pipeline.run_phase_v_pipeline") as mock_phase_v:

        mock_spec.return_value = MagicMock(files=["acceptance-criteria.md"])
        mock_tg.return_value = {"graph_version": 1, "tasks": []}
        g2_result = MagicMock(); g2_result.status = "approved"; g2_result.graph = {}
        mock_g2.return_value = g2_result
        mock_promote.return_value = str(uuid.uuid4())

        config = MagicMock()
        config.storage_root = str(tmp_path / "storage")
        os.makedirs(config.storage_root, exist_ok=True)

        run_phase_b_pipeline(
            run_id=run_id,
            config=config,
            conn_factory=lambda: conn,
            gate2_io=MagicMock(),
            llm_client=MagicMock(),
            agent=None,  # No agent
        )

    mock_phase_v.assert_not_called()


def test_phase_v_not_called_when_llm_client_none(tmp_path):
    """If llm_client=None, Phase V must not run and run status must NOT advance to RUNNING_PHASE_V."""
    run_id = str(uuid.uuid4())
    conn = _make_phase_b_conn(run_id, tmp_path)
    stub_agent = MagicMock()

    status_updates = []
    original_side_effect = conn.execute.side_effect

    def tracking_execute(query, params=None):
        if "update runs set status" in query.lower():
            status_updates.append(params[0] if params else None)
        return original_side_effect(query, params)

    conn.execute.side_effect = tracking_execute

    with patch("ai_dev_system.debate_pipeline.finalize_spec") as mock_spec, \
         patch("ai_dev_system.debate_pipeline.generate_task_graph") as mock_tg, \
         patch("ai_dev_system.debate_pipeline.run_gate_2") as mock_g2, \
         patch("ai_dev_system.debate_pipeline.beads_sync"), \
         patch("ai_dev_system.debate_pipeline.promote_output") as mock_promote, \
         patch("ai_dev_system.debate_pipeline.run_execution") as mock_exec, \
         patch("ai_dev_system.verification.pipeline.run_phase_v_pipeline") as mock_phase_v:

        mock_spec.return_value = MagicMock(files=["acceptance-criteria.md"])
        mock_tg.return_value = {"graph_version": 1, "tasks": []}
        g2_result = MagicMock(); g2_result.status = "approved"; g2_result.graph = {}
        mock_g2.return_value = g2_result
        mock_promote.return_value = str(uuid.uuid4())
        mock_exec.return_value = ExecutionResult(run_id=run_id, status="COMPLETED")

        config = MagicMock()
        config.storage_root = str(tmp_path / "storage")
        os.makedirs(config.storage_root, exist_ok=True)

        run_phase_b_pipeline(
            run_id=run_id,
            config=config,
            conn_factory=lambda: conn,
            gate2_io=MagicMock(),
            llm_client=None,   # ← no LLM client
            agent=stub_agent,
        )

    # Phase V must not be called
    mock_phase_v.assert_not_called()
    # Run must NOT have been advanced to RUNNING_PHASE_V
    assert "RUNNING_PHASE_V" not in status_updates
