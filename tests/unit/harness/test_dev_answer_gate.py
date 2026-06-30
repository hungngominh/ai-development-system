"""Tests for dev_answer_gate tool (Task 3 — Gate 1 conversational → Phase B).

Pattern: asyncio.run(tool.handler({...})) matching existing test style.
All tests are offline/deterministic — no real spawns, no LLM calls.

Decision assembly: mirrors webui._do_gate1_approve logic (iterates ctx.questions,
looks up result_by_id from debate_report, maps choice → answer + resolution_type).
"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ai_dev_system.db.connection import get_connection
from ai_dev_system.db.migrator import apply_schema
from ai_dev_system.harness.tools.dev_pipeline import make_dev_pipeline_tools


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    """File-backed SQLite with full schema (including v8 run_links)."""
    db_path = tmp_path / "test.db"
    conn = get_connection(f"sqlite:///{db_path}")
    apply_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def conn_factory(db):
    """conn_factory that always returns the same in-memory conn."""
    return lambda: db


@pytest.fixture
def config(tmp_path):
    """Minimal config-like object with storage_root."""
    cfg = MagicMock()
    cfg.storage_root = str(tmp_path / "storage")
    Path(cfg.storage_root).mkdir(parents=True, exist_ok=True)
    return cfg


@pytest.fixture
def link_store(conn_factory):
    from ai_dev_system.assistant.run_links import RunLinkStore
    return RunLinkStore(conn_factory)


def _make_tools(conn_factory, config, link_store, spawn_phase_b=None, monkeypatch=None):
    return make_dev_pipeline_tools(
        surface="telegram",
        chat_id="42",
        conn_factory=conn_factory,
        config=config,
        link_store=link_store,
        spawn_phase_b=spawn_phase_b,
    )


def _gate_tool(tools):
    return next(t for t in tools if t.name == "dev_answer_gate")


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _seed_paused_run(db, run_id: str, project_id: str | None = None) -> str:
    """Seed a run at PAUSED_AT_GATE_1 with a minimal debate_report_id."""
    if project_id is None:
        project_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO runs (run_id, project_id, title, status, pipeline_version, legacy, "
        "current_artifacts, metadata, gate1_session_state) "
        "VALUES (?,?,?,?,1,0,?,?,NULL)",
        (
            run_id,
            project_id,
            "TestProject",
            "PAUSED_AT_GATE_1",
            '{"debate_report_id": "art-dr-001"}',
            "{}",
        ),
    )
    db.commit()
    return run_id


def _make_stub_context(question_ids: list[str]):
    """Build a minimal stub GateReviewContext with questions and debate_report results."""
    from ai_dev_system.debate.report import Question

    questions = [
        Question(
            id=qid,
            text=f"Question text for {qid}",
            classification="REQUIRED",
            domain="scope",
            agent_a="Agent A answer",
            agent_b="Agent B answer",
        )
        for qid in question_ids
    ]
    # Build debate_report with results so decision assembly can look up final
    results = [
        {
            "question": {
                "id": qid,
                "text": f"Question text for {qid}",
                "classification": "REQUIRED",
                "domain": "scope",
                "agent_a": "Agent A answer",
                "agent_b": "Agent B answer",
            },
            "final": {
                "moderator_summary": f"Moderator resolved {qid}",
                "agent_a_position": f"A position for {qid}",
                "agent_b_position": f"B position for {qid}",
                "resolution_status": "RESOLVED",
            },
        }
        for qid in question_ids
    ]
    ctx = MagicMock()
    ctx.questions = questions
    ctx.debate_report = {"results": results}
    ctx.decision_by_id = {}
    return ctx


# ---------------------------------------------------------------------------
# Tests: answer action
# ---------------------------------------------------------------------------


class TestDevAnswerGateAnswer:
    def test_answer_records_choice_in_state(self, db, conn_factory, config, link_store, monkeypatch):
        """answer input → records choice in GateSessionState; spawn_phase_b NOT called."""
        import ai_dev_system.harness.tools.dev_pipeline as dp_module

        run_id = str(uuid.uuid4())
        _seed_paused_run(db, run_id)

        stub_ctx = _make_stub_context(["Q1", "Q2"])
        monkeypatch.setattr(dp_module, "load_gate1_context", lambda rid, conn: stub_ctx)

        spawn_called = []

        def recording_spawn(argv, **kwargs):
            spawn_called.append(argv)

        tools = _make_tools(conn_factory, config, link_store, spawn_phase_b=recording_spawn)
        gate_tool = _gate_tool(tools)

        result = asyncio.run(gate_tool.handler({"run_id": run_id, "text": "Q1 chọn A"}))

        # spawn_phase_b NOT called
        assert spawn_called == [], "spawn_phase_b must NOT be called for an answer action"

        # State shows Q1 resolved
        from ai_dev_system.gate.gate1_review.state import load_state
        state = load_state(run_id, db)
        assert "Q1" in state.resolved, "Q1 should be in state.resolved after answer"
        assert state.resolved["Q1"].choice == "agent_a"

        # Content includes some feedback
        assert "content" in result
        text = result["content"][0]["text"]
        assert len(text) > 0

    def test_answer_reports_remaining_count(self, db, conn_factory, config, link_store, monkeypatch):
        """answer input → response indicates remaining questions count."""
        import ai_dev_system.harness.tools.dev_pipeline as dp_module

        run_id = str(uuid.uuid4())
        _seed_paused_run(db, run_id)

        stub_ctx = _make_stub_context(["Q1", "Q2"])
        monkeypatch.setattr(dp_module, "load_gate1_context", lambda rid, conn: stub_ctx)

        tools = _make_tools(conn_factory, config, link_store)
        gate_tool = _gate_tool(tools)

        result = asyncio.run(gate_tool.handler({"run_id": run_id, "text": "Q1 chọn A"}))

        text = result["content"][0]["text"]
        # After answering Q1, Q2 remains — response should mention 1 remaining
        assert "1" in text


# ---------------------------------------------------------------------------
# Tests: approve / confirm action → finalize + spawn Phase B
# ---------------------------------------------------------------------------


class TestDevAnswerGateApprove:
    def test_confirm_all_resolved_spawns_phase_b_once(
        self, db, conn_factory, config, link_store, monkeypatch
    ):
        """confirm (all questions resolved) → spawn_phase_b called once with phase-b argv."""
        import ai_dev_system.harness.tools.dev_pipeline as dp_module
        from ai_dev_system.gate.gate1_review.state import GateSessionState, save_state

        run_id = str(uuid.uuid4())
        _seed_paused_run(db, run_id)

        stub_ctx = _make_stub_context(["Q1", "Q2"])
        monkeypatch.setattr(dp_module, "load_gate1_context", lambda rid, conn: stub_ctx)

        # Pre-resolve all questions in state so confirm doesn't block
        state = GateSessionState(run_id=run_id)
        state.record_choice("Q1", "agent_a")
        state.record_choice("Q2", "moderator")
        save_state(run_id, state, db)
        db.commit()

        # Stub finalize_gate1 to avoid disk artifact creation
        finalize_calls = []

        def stub_finalize(run_id, decisions, storage_root, conn):
            finalize_calls.append({"run_id": run_id, "decisions": decisions})
            conn.execute("UPDATE runs SET status=? WHERE run_id=?", ("RUNNING_PHASE_1D", run_id))
            conn.commit()
            return ("aa-stub", "dl-stub")

        monkeypatch.setattr(dp_module, "finalize_gate1", stub_finalize)

        spawn_called = []

        def recording_spawn(argv, **kwargs):
            spawn_called.append(argv)

        tools = _make_tools(conn_factory, config, link_store, spawn_phase_b=recording_spawn)
        gate_tool = _gate_tool(tools)

        # Seed a non-empty gate session so we can verify finalize clears it.
        from ai_dev_system.gate.gate1_review.state import (
            load_state as _ls, save_state as _ss,
        )
        _seed = _ls(run_id, db)
        _seed.record_choice("Q1", "agent_a")
        _ss(run_id, _seed, db)
        db.commit()
        assert db.execute(
            "SELECT gate1_session_state FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()[0] is not None

        result = asyncio.run(gate_tool.handler({"run_id": run_id, "text": "confirm"}))

        # spawn_phase_b called exactly once with correct argv
        assert len(spawn_called) == 1, f"Expected 1 spawn_phase_b call, got {len(spawn_called)}"
        argv = spawn_called[0]
        assert "phase-b" in argv, f"argv should contain 'phase-b': {argv}"
        # Gate 1 approve now spawns 'to-gate2' (pauses at Gate 2 for human review)
        assert "to-gate2" in argv, f"argv should contain 'to-gate2': {argv}"
        assert "--run-id" in argv, f"argv should contain '--run-id': {argv}"
        assert run_id in argv, f"argv should contain run_id: {argv}"

        # finalize was called
        assert len(finalize_calls) == 1
        assert finalize_calls[0]["run_id"] == run_id

        # Run status updated
        row = db.execute("SELECT status FROM runs WHERE run_id=?", (run_id,)).fetchone()
        assert row["status"] == "RUNNING_PHASE_1D"

        # Gate session state cleared after finalize (no stale resolved choices).
        assert db.execute(
            "SELECT gate1_session_state FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()[0] is None

        # Content indicates phase-b started
        text = result["content"][0]["text"]
        assert "phase_b" in text.lower() or "phase-b" in text.lower() or "started" in text.lower()

    def test_approve_all_spawns_phase_b(
        self, db, conn_factory, config, link_store, monkeypatch
    ):
        """'approve all' → also spawns phase_b."""
        import ai_dev_system.harness.tools.dev_pipeline as dp_module
        from ai_dev_system.gate.gate1_review.state import GateSessionState, save_state

        run_id = str(uuid.uuid4())
        _seed_paused_run(db, run_id)

        stub_ctx = _make_stub_context(["Q1"])
        monkeypatch.setattr(dp_module, "load_gate1_context", lambda rid, conn: stub_ctx)

        # Pre-resolve Q1
        state = GateSessionState(run_id=run_id)
        state.record_choice("Q1", "moderator")
        save_state(run_id, state, db)
        db.commit()

        def stub_finalize(run_id, decisions, storage_root, conn):
            conn.execute("UPDATE runs SET status=? WHERE run_id=?", ("RUNNING_PHASE_1D", run_id))
            conn.commit()
            return ("aa-stub", "dl-stub")

        monkeypatch.setattr(dp_module, "finalize_gate1", stub_finalize)

        spawn_called = []

        def recording_spawn(argv, **kwargs):
            spawn_called.append(argv)

        tools = _make_tools(conn_factory, config, link_store, spawn_phase_b=recording_spawn)
        gate_tool = _gate_tool(tools)

        asyncio.run(gate_tool.handler({"run_id": run_id, "text": "approve all"}))

        assert len(spawn_called) == 1
        argv = spawn_called[0]
        assert "phase-b" in argv
        # Gate 1 approve_all now spawns 'to-gate2' (pauses at Gate 2 for human review)
        assert "to-gate2" in argv, f"argv should contain 'to-gate2': {argv}"
        assert run_id in argv


# ---------------------------------------------------------------------------
# Tests: unknown action
# ---------------------------------------------------------------------------


class TestDevAnswerGateUnknown:
    def test_unknown_returns_guidance_no_state_change_no_spawn(
        self, db, conn_factory, config, link_store, monkeypatch
    ):
        """Unknown input → returns guidance string; no state change; spawn_phase_b NOT called."""
        import ai_dev_system.harness.tools.dev_pipeline as dp_module

        run_id = str(uuid.uuid4())
        _seed_paused_run(db, run_id)

        stub_ctx = _make_stub_context(["Q1", "Q2"])
        monkeypatch.setattr(dp_module, "load_gate1_context", lambda rid, conn: stub_ctx)

        spawn_called = []

        def recording_spawn(argv, **kwargs):
            spawn_called.append(argv)

        tools = _make_tools(conn_factory, config, link_store, spawn_phase_b=recording_spawn)
        gate_tool = _gate_tool(tools)

        result = asyncio.run(
            gate_tool.handler({"run_id": run_id, "text": "xyzzy this is gibberish nobody knows"})
        )

        # No spawn
        assert spawn_called == [], "spawn_phase_b must NOT be called for unknown action"

        # State unchanged (nothing resolved)
        from ai_dev_system.gate.gate1_review.state import load_state
        state = load_state(run_id, db)
        assert state.resolved == {}, "State should be empty after unknown input"

        # Returns guidance
        assert "content" in result
        text = result["content"][0]["text"]
        assert len(text) > 5  # non-empty guidance

    def test_expand_returns_guidance_no_spawn(
        self, db, conn_factory, config, link_store, monkeypatch
    ):
        """expand / show Q1 → guidance message, no state change, no spawn."""
        import ai_dev_system.harness.tools.dev_pipeline as dp_module

        run_id = str(uuid.uuid4())
        _seed_paused_run(db, run_id)

        stub_ctx = _make_stub_context(["Q1"])
        monkeypatch.setattr(dp_module, "load_gate1_context", lambda rid, conn: stub_ctx)

        spawn_called = []

        def recording_spawn(argv, **kwargs):
            spawn_called.append(argv)

        tools = _make_tools(conn_factory, config, link_store, spawn_phase_b=recording_spawn)
        gate_tool = _gate_tool(tools)

        result = asyncio.run(gate_tool.handler({"run_id": run_id, "text": "show Q1"}))

        assert spawn_called == []
        text = result["content"][0]["text"]
        assert len(text) > 0


# ---------------------------------------------------------------------------
# Tests: optional run_id (Critical #1 — chat-bound resolution)
# ---------------------------------------------------------------------------


class TestDevAnswerGateOptionalRunId:
    def test_no_run_id_resolves_via_latest_for_chat(self, db, conn_factory, config, link_store, monkeypatch):
        """dev_answer_gate with empty run_id resolves via latest_for_chat."""
        import ai_dev_system.harness.tools.dev_pipeline as dp_module

        run_id = str(uuid.uuid4())
        _seed_paused_run(db, run_id)
        link_store.link(run_id, "telegram", "42")

        stub_ctx = _make_stub_context(["Q1"])
        monkeypatch.setattr(dp_module, "load_gate1_context", lambda rid, conn: stub_ctx)

        tools = _make_tools(conn_factory, config, link_store)
        gate_tool = _gate_tool(tools)

        result = asyncio.run(gate_tool.handler({"run_id": "", "text": "Q1 chọn A"}))

        assert "content" in result
        # State should have been updated for the correct run_id
        from ai_dev_system.gate.gate1_review.state import load_state
        state = load_state(run_id, db)
        assert "Q1" in state.resolved, "Q1 should be resolved via chat-bound run lookup"

    def test_no_run_id_no_link_returns_friendly_message(self, db, conn_factory, config, link_store, monkeypatch):
        """dev_answer_gate with empty run_id + no link → friendly 'no run for this chat' message."""
        tools = _make_tools(conn_factory, config, link_store)
        gate_tool = _gate_tool(tools)

        result = asyncio.run(gate_tool.handler({"run_id": "", "text": "Q1 chọn A"}))

        assert "content" in result
        text = result["content"][0]["text"].lower()
        assert "chat" in text or "run" in text or "chưa" in text


# ---------------------------------------------------------------------------
# Tests: unresolved-questions guard (Important #2)
# ---------------------------------------------------------------------------


class TestDevAnswerGateUnresolvedGuard:
    def test_confirm_with_unresolved_questions_returns_guidance_no_spawn(
        self, db, conn_factory, config, link_store, monkeypatch
    ):
        """confirm with unresolved questions → guidance message; spawn_phase_b NOT called."""
        import ai_dev_system.harness.tools.dev_pipeline as dp_module

        run_id = str(uuid.uuid4())
        _seed_paused_run(db, run_id)

        # ctx has Q1, Q2 — neither resolved in state
        stub_ctx = _make_stub_context(["Q1", "Q2"])
        monkeypatch.setattr(dp_module, "load_gate1_context", lambda rid, conn: stub_ctx)

        spawn_called = []

        def recording_spawn(argv, **kwargs):
            spawn_called.append(argv)

        def stub_finalize(run_id, decisions, storage_root, conn):
            conn.execute("UPDATE runs SET status=? WHERE run_id=?", ("RUNNING_PHASE_1D", run_id))
            conn.commit()
            return ("aa-stub", "dl-stub")

        monkeypatch.setattr(dp_module, "finalize_gate1", stub_finalize)

        tools = _make_tools(conn_factory, config, link_store, spawn_phase_b=recording_spawn)
        gate_tool = _gate_tool(tools)

        result = asyncio.run(gate_tool.handler({"run_id": run_id, "text": "confirm"}))

        # spawn_phase_b NOT called
        assert spawn_called == [], "spawn_phase_b must NOT be called when questions are unresolved"

        # Returns guidance with unresolved question ids
        text = result["content"][0]["text"]
        assert "Q1" in text or "Q2" in text or "chưa" in text.lower(), (
            f"Expected guidance about unresolved questions, got: {text!r}"
        )

    def test_approve_all_with_unresolved_proceeds(
        self, db, conn_factory, config, link_store, monkeypatch
    ):
        """'approve all' with unresolved questions → proceeds (explicit accept-defaults intent)."""
        import ai_dev_system.harness.tools.dev_pipeline as dp_module

        run_id = str(uuid.uuid4())
        _seed_paused_run(db, run_id)

        # ctx has Q1, Q2 — neither resolved in state (approve_all should proceed anyway)
        stub_ctx = _make_stub_context(["Q1", "Q2"])
        monkeypatch.setattr(dp_module, "load_gate1_context", lambda rid, conn: stub_ctx)

        spawn_called = []

        def recording_spawn(argv, **kwargs):
            spawn_called.append(argv)

        def stub_finalize(run_id, decisions, storage_root, conn):
            conn.execute("UPDATE runs SET status=? WHERE run_id=?", ("RUNNING_PHASE_1D", run_id))
            conn.commit()
            return ("aa-stub", "dl-stub")

        monkeypatch.setattr(dp_module, "finalize_gate1", stub_finalize)

        tools = _make_tools(conn_factory, config, link_store, spawn_phase_b=recording_spawn)
        gate_tool = _gate_tool(tools)

        result = asyncio.run(gate_tool.handler({"run_id": run_id, "text": "approve all"}))

        # spawn_phase_b called (approve_all bypasses the guard)
        assert len(spawn_called) == 1, "spawn_phase_b should be called on 'approve all'"

    def test_confirm_all_resolved_spawns_phase_b_still_works(
        self, db, conn_factory, config, link_store, monkeypatch
    ):
        """confirm with ALL questions resolved still spawns Phase B (guard doesn't block it)."""
        import ai_dev_system.harness.tools.dev_pipeline as dp_module
        from ai_dev_system.gate.gate1_review.state import GateSessionState, save_state

        run_id = str(uuid.uuid4())
        _seed_paused_run(db, run_id)

        stub_ctx = _make_stub_context(["Q1"])
        monkeypatch.setattr(dp_module, "load_gate1_context", lambda rid, conn: stub_ctx)

        # Pre-resolve Q1
        state = GateSessionState(run_id=run_id)
        state.record_choice("Q1", "moderator")
        save_state(run_id, state, db)
        db.commit()

        spawn_called = []

        def recording_spawn(argv, **kwargs):
            spawn_called.append(argv)

        def stub_finalize(run_id, decisions, storage_root, conn):
            conn.execute("UPDATE runs SET status=? WHERE run_id=?", ("RUNNING_PHASE_1D", run_id))
            conn.commit()
            return ("aa-stub", "dl-stub")

        monkeypatch.setattr(dp_module, "finalize_gate1", stub_finalize)

        tools = _make_tools(conn_factory, config, link_store, spawn_phase_b=recording_spawn)
        gate_tool = _gate_tool(tools)

        result = asyncio.run(gate_tool.handler({"run_id": run_id, "text": "confirm"}))

        # spawn_phase_b called (all resolved) with to-gate2 verb
        assert len(spawn_called) == 1, "spawn_phase_b should be called when all questions resolved"
        assert "to-gate2" in spawn_called[0], (
            f"Gate-1 confirm should spawn 'to-gate2', got: {spawn_called[0]}"
        )


# ---------------------------------------------------------------------------
# Tests: Gate-2 routing (PAUSED_AT_GATE_2)
# ---------------------------------------------------------------------------


def _seed_paused_at_gate2_run(db, run_id: str, task_graph_gen_id: str | None = None) -> str:
    """Seed a run at PAUSED_AT_GATE_2 with optional task_graph_gen_id in current_artifacts."""
    project_id = str(uuid.uuid4())
    current_artifacts = json.dumps(
        {"task_graph_gen_id": task_graph_gen_id} if task_graph_gen_id else {}
    )
    db.execute(
        "INSERT INTO runs (run_id, project_id, title, status, pipeline_version, legacy, "
        "current_artifacts, metadata, gate1_session_state) "
        "VALUES (?,?,?,?,1,0,?,?,NULL)",
        (run_id, project_id, "TestProject", "PAUSED_AT_GATE_2", current_artifacts, "{}"),
    )
    db.commit()
    return run_id


class TestDevAnswerGateGate2:
    """Gate-2 routing: PAUSED_AT_GATE_2 → resume-gate2 spawn."""

    def test_gate2_approve_vietnamese_spawns_resume_with_approve(
        self, db, conn_factory, config, link_store
    ):
        """'duyệt' on PAUSED_AT_GATE_2 run → spawn resume-gate2 --decision approve."""
        run_id = str(uuid.uuid4())
        _seed_paused_at_gate2_run(db, run_id)

        spawn_called = []

        def recording_spawn(argv, **kwargs):
            spawn_called.append(argv)

        tools = _make_tools(conn_factory, config, link_store, spawn_phase_b=recording_spawn)
        gate_tool = _gate_tool(tools)

        result = asyncio.run(gate_tool.handler({"run_id": run_id, "text": "duyệt"}))

        assert len(spawn_called) == 1, f"Expected 1 spawn call, got {len(spawn_called)}"
        argv = spawn_called[0]
        assert "phase-b" in argv, f"argv must contain 'phase-b': {argv}"
        assert "resume-gate2" in argv, f"argv must contain 'resume-gate2': {argv}"
        assert "--run-id" in argv, f"argv must contain '--run-id': {argv}"
        assert run_id in argv, f"argv must contain run_id: {argv}"
        assert "--decision" in argv, f"argv must contain '--decision': {argv}"
        decision_index = argv.index("--decision")
        assert argv[decision_index + 1] == "approve", (
            f"--decision value should be 'approve', got {argv[decision_index + 1]!r}"
        )
        # Response confirms the action
        text = result["content"][0]["text"]
        assert len(text) > 0

    def test_gate2_approve_english_spawns_resume_with_approve(
        self, db, conn_factory, config, link_store
    ):
        """'approve' (English) on PAUSED_AT_GATE_2 → spawn resume-gate2 --decision approve."""
        run_id = str(uuid.uuid4())
        _seed_paused_at_gate2_run(db, run_id)

        spawn_called = []

        def recording_spawn(argv, **kwargs):
            spawn_called.append(argv)

        tools = _make_tools(conn_factory, config, link_store, spawn_phase_b=recording_spawn)
        gate_tool = _gate_tool(tools)

        asyncio.run(gate_tool.handler({"run_id": run_id, "text": "approve"}))

        assert len(spawn_called) == 1
        argv = spawn_called[0]
        assert "resume-gate2" in argv
        decision_index = argv.index("--decision")
        assert argv[decision_index + 1] == "approve"

    def test_gate2_reject_vietnamese_spawns_resume_with_reject(
        self, db, conn_factory, config, link_store
    ):
        """'từ chối' on PAUSED_AT_GATE_2 → spawn resume-gate2 --decision reject."""
        run_id = str(uuid.uuid4())
        _seed_paused_at_gate2_run(db, run_id)

        spawn_called = []

        def recording_spawn(argv, **kwargs):
            spawn_called.append(argv)

        tools = _make_tools(conn_factory, config, link_store, spawn_phase_b=recording_spawn)
        gate_tool = _gate_tool(tools)

        result = asyncio.run(gate_tool.handler({"run_id": run_id, "text": "từ chối"}))

        assert len(spawn_called) == 1, f"Expected 1 spawn call, got {len(spawn_called)}"
        argv = spawn_called[0]
        assert "phase-b" in argv
        assert "resume-gate2" in argv
        assert "--decision" in argv
        decision_index = argv.index("--decision")
        assert argv[decision_index + 1] == "reject", (
            f"--decision value should be 'reject', got {argv[decision_index + 1]!r}"
        )
        text = result["content"][0]["text"]
        assert len(text) > 0

    def test_gate2_both_keywords_is_ambiguous_no_spawn(
        self, db, conn_factory, config, link_store
    ):
        """A message matching BOTH approve and reject keywords must NOT silently
        approve — it returns guidance and spawns nothing (safety on a mixed signal)."""
        run_id = str(uuid.uuid4())
        _seed_paused_at_gate2_run(db, run_id)

        spawn_called = []

        def recording_spawn(argv, **kwargs):
            spawn_called.append(argv)

        tools = _make_tools(conn_factory, config, link_store, spawn_phase_b=recording_spawn)
        gate_tool = _gate_tool(tools)

        result = asyncio.run(
            gate_tool.handler({"run_id": run_id, "text": "approve? no từ chối"})
        )

        assert spawn_called == [], "ambiguous (both keywords) must NOT spawn resume-gate2"
        text = result["content"][0]["text"]
        assert "duyệt" in text.lower() or "từ chối" in text.lower()

    @pytest.mark.parametrize("text", [
        "do not approve",
        "I won't approve this",
        "never approve",
        "not ok",
        "don't approve",
    ])
    def test_gate2_english_negation_does_not_silently_approve(
        self, db, conn_factory, config, link_store, text
    ):
        """English negations of approval must NOT spawn --decision approve (the
        negator makes it ambiguous → guidance). Regression for the final-review
        finding that 'do not approve' silently approved the task graph."""
        run_id = str(uuid.uuid4())
        _seed_paused_at_gate2_run(db, run_id)

        spawn_called = []

        def recording_spawn(argv, **kwargs):
            spawn_called.append(argv)

        tools = _make_tools(conn_factory, config, link_store, spawn_phase_b=recording_spawn)
        gate_tool = _gate_tool(tools)

        asyncio.run(gate_tool.handler({"run_id": run_id, "text": text}))

        approve_spawns = [
            a for a in spawn_called
            if "--decision" in a and a[a.index("--decision") + 1] == "approve"
        ]
        assert approve_spawns == [], (
            f"{text!r} must NOT spawn an approve decision; got {spawn_called}"
        )

    def test_gate2_reject_english_spawns_resume_with_reject(
        self, db, conn_factory, config, link_store
    ):
        """'reject' (English) on PAUSED_AT_GATE_2 → spawn resume-gate2 --decision reject."""
        run_id = str(uuid.uuid4())
        _seed_paused_at_gate2_run(db, run_id)

        spawn_called = []

        def recording_spawn(argv, **kwargs):
            spawn_called.append(argv)

        tools = _make_tools(conn_factory, config, link_store, spawn_phase_b=recording_spawn)
        gate_tool = _gate_tool(tools)

        asyncio.run(gate_tool.handler({"run_id": run_id, "text": "reject"}))

        assert len(spawn_called) == 1
        argv = spawn_called[0]
        assert "resume-gate2" in argv
        decision_index = argv.index("--decision")
        assert argv[decision_index + 1] == "reject"

    def test_gate2_ambiguous_text_returns_guidance_no_spawn(
        self, db, conn_factory, config, link_store
    ):
        """Ambiguous text on PAUSED_AT_GATE_2 → guidance returned, no spawn."""
        run_id = str(uuid.uuid4())
        _seed_paused_at_gate2_run(db, run_id)

        spawn_called = []

        def recording_spawn(argv, **kwargs):
            spawn_called.append(argv)

        tools = _make_tools(conn_factory, config, link_store, spawn_phase_b=recording_spawn)
        gate_tool = _gate_tool(tools)

        result = asyncio.run(gate_tool.handler({"run_id": run_id, "text": "hmm what do I do?"}))

        assert spawn_called == [], f"No spawn expected for ambiguous text, got: {spawn_called}"
        text = result["content"][0]["text"]
        # Should provide guidance
        assert len(text) > 5

    def test_gate2_other_status_returns_guidance(
        self, db, conn_factory, config, link_store
    ):
        """Run at non-gate status → guidance about wrong status, no spawn."""
        run_id = str(uuid.uuid4())
        project_id = str(uuid.uuid4())
        db.execute(
            "INSERT INTO runs (run_id, project_id, title, status, pipeline_version, legacy, "
            "current_artifacts, metadata, gate1_session_state) "
            "VALUES (?,?,?,?,1,0,'{}','{}',NULL)",
            (run_id, project_id, "TestProject", "RUNNING_PHASE_3"),
        )
        db.commit()

        spawn_called = []

        def recording_spawn(argv, **kwargs):
            spawn_called.append(argv)

        tools = _make_tools(conn_factory, config, link_store, spawn_phase_b=recording_spawn)
        gate_tool = _gate_tool(tools)

        result = asyncio.run(gate_tool.handler({"run_id": run_id, "text": "duyệt"}))

        assert spawn_called == [], f"No spawn expected for wrong status, got: {spawn_called}"
        text = result["content"][0]["text"]
        # Should include status info in guidance
        assert "RUNNING_PHASE_3" in text or "status" in text.lower() or "trạng thái" in text
