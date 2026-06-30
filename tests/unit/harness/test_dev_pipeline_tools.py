"""Tests for harness/tools/dev_pipeline.py — dev_newproject_start + dev_run_status.

Pattern: asyncio.run(tool.handler({...})) matching test_memory_tool.py style.
All tests are offline/deterministic — no actual subprocess spawn, no LLM calls.
"""
from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ai_dev_system.assistant.run_links import RunLinkStore
from ai_dev_system.db.connection import get_connection
from ai_dev_system.db.migrator import apply_schema
from ai_dev_system.harness.tools.dev_pipeline import make_dev_pipeline_tools


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    """In-memory SQLite with full schema (including v8 run_links)."""
    conn = get_connection("sqlite:///:memory:")
    apply_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def conn_factory(db):
    """conn_factory that always returns the same in-memory conn."""
    return lambda: db


@pytest.fixture
def link_store(conn_factory):
    return RunLinkStore(conn_factory)


@pytest.fixture
def config(tmp_path):
    """Minimal config-like object with storage_root."""
    cfg = MagicMock()
    cfg.storage_root = str(tmp_path / "storage")
    return cfg


def _make_tools(conn_factory, config, link_store, spawn_start=None):
    return make_dev_pipeline_tools(
        surface="telegram",
        chat_id="42",
        conn_factory=conn_factory,
        config=config,
        link_store=link_store,
        spawn_start=spawn_start,
    )


# ---------------------------------------------------------------------------
# dev_newproject_start tests
# ---------------------------------------------------------------------------


class TestDevNewprojectStart:
    def test_spawn_argv_contains_start_and_idea(self, db, conn_factory, link_store, config):
        """spawn_start is called with the correct argv (cli.main start ...)."""
        spawned = {}

        def recording_spawn(argv, **kwargs):
            spawned["argv"] = argv
            spawned["kwargs"] = kwargs

        tools = _make_tools(conn_factory, config, link_store, spawn_start=recording_spawn)
        start_tool = next(t for t in tools if t.name == "dev_newproject_start")

        asyncio.run(start_tool.handler({"project_name": "MyProject", "idea": "Build a thing"}))

        assert spawned, "spawn_start was never called"
        argv = spawned["argv"]
        assert argv[0] == sys.executable
        assert "-m" in argv
        assert "ai_dev_system.cli.main" in argv
        assert "start" in argv
        assert "--project-name" in argv
        assert "MyProject" in argv
        assert "--idea" in argv
        assert "Build a thing" in argv

    def test_links_run_id_when_row_exists(self, db, conn_factory, link_store, config):
        """When a runs row with matching project_id exists, tool links run_id→chat."""
        from ai_dev_system.cli.start_project import make_project_id, name_to_slug

        project_name = "MyProject"
        slug = name_to_slug(project_name)
        project_id = make_project_id(slug)
        run_id = str(uuid.uuid4())

        # Pre-seed a runs row so the tool can resolve it immediately
        db.execute(
            "INSERT INTO runs (run_id, project_id, status, pipeline_version, legacy, "
            "current_artifacts, metadata) VALUES (?,?,?,1,0,'{}','{}')",
            (run_id, project_id, "RUNNING_PHASE_1A"),
        )
        db.commit()

        def recording_spawn(argv, **kwargs):
            pass  # does not spawn

        tools = _make_tools(conn_factory, config, link_store, spawn_start=recording_spawn)
        start_tool = next(t for t in tools if t.name == "dev_newproject_start")

        result = asyncio.run(start_tool.handler({"project_name": project_name, "idea": "Build a thing"}))

        # Verify content has text
        assert "content" in result
        text = result["content"][0]["text"]
        assert len(text) > 0

        # Verify run is linked
        link = link_store.lookup(run_id)
        assert link is not None, "run_id should be linked after tool call"
        assert link.surface == "telegram"
        assert link.chat_id == "42"

    def test_returns_starting_when_no_run_row(self, db, conn_factory, link_store, config):
        """When no runs row exists yet, returns status:starting (no link created)."""
        def recording_spawn(argv, **kwargs):
            pass

        tools = _make_tools(conn_factory, config, link_store, spawn_start=recording_spawn)
        start_tool = next(t for t in tools if t.name == "dev_newproject_start")

        result = asyncio.run(start_tool.handler({"project_name": "GhostProject", "idea": "Nothing yet"}))

        assert "content" in result
        text = result["content"][0]["text"].lower()
        assert "starting" in text


# ---------------------------------------------------------------------------
# dev_run_status tests
# ---------------------------------------------------------------------------


class TestDevRunStatus:
    def test_running_phase_1b_returns_status_no_questions(self, db, conn_factory, link_store, config):
        """RUNNING_PHASE_1B status → returns status, no questions field."""
        run_id = str(uuid.uuid4())
        project_id = str(uuid.uuid4())
        db.execute(
            "INSERT INTO runs (run_id, project_id, status, pipeline_version, legacy, "
            "current_artifacts, metadata) VALUES (?,?,?,1,0,'{}','{}')",
            (run_id, project_id, "RUNNING_PHASE_1B"),
        )
        db.commit()

        tools = _make_tools(conn_factory, config, link_store)
        status_tool = next(t for t in tools if t.name == "dev_run_status")

        result = asyncio.run(status_tool.handler({"run_id": run_id}))

        assert "content" in result
        text = result["content"][0]["text"]
        assert "RUNNING_PHASE_1B" in text
        # Non-gate status must not carry a questions payload (unambiguous JSON check).
        assert "questions" not in json.loads(text)

    def test_paused_at_gate1_returns_status_and_questions(self, db, conn_factory, link_store, config, tmp_path, monkeypatch):
        """PAUSED_AT_GATE_1 → returns status + non-empty questions list.

        Gate1 loading requires DEBATE_REPORT artifact on disk. Because seeding
        that artifact is heavy (requires real debate_report.json structure),
        we monkeypatch load_gate1_context to return a stub with 2 questions.
        This tests the tool's gate-branch logic without the full artifact machinery.
        """
        from ai_dev_system.debate.report import Question

        run_id = str(uuid.uuid4())
        project_id = str(uuid.uuid4())
        db.execute(
            "INSERT INTO runs (run_id, project_id, status, pipeline_version, legacy, "
            "current_artifacts, metadata) VALUES (?,?,?,1,0,'{}','{}')",
            (run_id, project_id, "PAUSED_AT_GATE_1"),
        )
        db.commit()

        stub_questions = [
            Question(id="Q1", text="What is the primary user?", classification="REQUIRED",
                     domain="user", agent_a="AgentA", agent_b="AgentB"),
            Question(id="Q2", text="What is the scale target?", classification="STRATEGIC",
                     domain="scale", agent_a="AgentA", agent_b="AgentB"),
        ]

        # Stub GateReviewContext
        stub_context = MagicMock()
        stub_context.questions = stub_questions

        import ai_dev_system.harness.tools.dev_pipeline as dp_module
        monkeypatch.setattr(dp_module, "load_gate1_context", lambda run_id, conn: stub_context)

        tools = _make_tools(conn_factory, config, link_store)
        status_tool = next(t for t in tools if t.name == "dev_run_status")

        result = asyncio.run(status_tool.handler({"run_id": run_id}))

        assert "content" in result
        text = result["content"][0]["text"]
        assert "PAUSED_AT_GATE_1" in text
        # Questions should be included
        assert "Q1" in text or "What is the primary user" in text
        assert "Q2" in text or "What is the scale target" in text

    def test_unknown_run_id_returns_error(self, db, conn_factory, link_store, config):
        """Missing run_id → error message in content."""
        tools = _make_tools(conn_factory, config, link_store)
        status_tool = next(t for t in tools if t.name == "dev_run_status")

        result = asyncio.run(status_tool.handler({"run_id": "nonexistent-run-id"}))

        assert "content" in result
        text = result["content"][0]["text"].lower()
        assert "not found" in text or "error" in text
