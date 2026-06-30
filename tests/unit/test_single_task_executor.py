"""Tests for single_task_executor bridge worker."""
from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _fake_git_ok(stdout: str = "main") -> MagicMock:
    p = MagicMock()
    p.returncode = 0
    p.stdout = stdout
    p.stderr = ""
    return p


def _fake_git_fail() -> MagicMock:
    p = MagicMock()
    p.returncode = 1
    p.stdout = ""
    p.stderr = "fatal: not a git repository"
    return p


# ── Git helpers ────────────────────────────────────────────────────────────────

def test_get_current_branch_returns_stripped_name():
    from ai_dev_system.task_graph.git_ops import current_branch as _git_current_branch
    with patch("ai_dev_system.task_graph.git_ops.subprocess.run",
               return_value=_fake_git_ok("  main\n")):
        branch = _git_current_branch("/some/repo")
    assert branch == "main"


def test_get_current_branch_raises_on_fail():
    from ai_dev_system.task_graph.git_ops import current_branch as _git_current_branch
    with patch("ai_dev_system.task_graph.git_ops.subprocess.run",
               return_value=_fake_git_fail()):
        with pytest.raises(RuntimeError, match="not a git repository"):
            _git_current_branch("/not/a/repo")


def test_checkout_branch_calls_git(tmp_path):
    from ai_dev_system.task_graph.git_ops import checkout_branch as _git_checkout_branch
    calls: list = []

    def _fake_run(cmd, **kw):
        calls.append(cmd)
        return _fake_git_ok("")

    with patch("ai_dev_system.task_graph.git_ops.subprocess.run",
               side_effect=_fake_run):
        _git_checkout_branch(str(tmp_path), "ai-dev/task-abc123")

    assert any("checkout" in cmd for cmd in calls)
    assert any("ai-dev/task-abc123" in cmd for cmd in calls)


def test_checkout_branch_creates_new_when_not_exists(tmp_path):
    from ai_dev_system.task_graph.git_ops import checkout_branch as _git_checkout_branch
    calls: list = []

    def _fake_run(cmd, **kw):
        calls.append(cmd)
        # First checkout fails (branch doesn't exist); second (with -b) succeeds
        if "-b" not in cmd:
            return _fake_git_fail()
        return _fake_git_ok("")

    with patch("ai_dev_system.task_graph.git_ops.subprocess.run",
               side_effect=_fake_run):
        _git_checkout_branch(str(tmp_path), "ai-dev/task-new")

    assert any("-b" in cmd for cmd in calls)


# ── push branch + GitHub compare URL ────────────────────────────────────────────

def test_normalize_github_url_variants():
    from ai_dev_system.task_graph.git_ops import normalize_github_url as _normalize_github_url
    assert _normalize_github_url("https://github.com/o/r.git") == "https://github.com/o/r"
    assert _normalize_github_url("git@github.com:o/r.git") == "https://github.com/o/r"
    assert _normalize_github_url("https://github.com/o/r/") == "https://github.com/o/r"


def test_push_branch_compare_builds_url(tmp_path):
    from ai_dev_system.task_graph.git_ops import push_branch_compare as _push_branch_compare

    def _fake_run(cmd, **kw):
        if cmd[:2] == ["git", "push"]:
            return _fake_git_ok("")
        if cmd[:3] == ["git", "remote", "get-url"]:
            return _fake_git_ok("https://github.com/o/r.git\n")
        return _fake_git_ok("")

    with patch("ai_dev_system.task_graph.git_ops.subprocess.run",
               side_effect=_fake_run):
        info = _push_branch_compare(str(tmp_path), "ai-dev/task-x", "master")

    assert info["pushed"] is True
    assert info["compare_url"] == "https://github.com/o/r/compare/master...ai-dev/task-x"


def test_push_branch_compare_push_failure(tmp_path):
    from ai_dev_system.task_graph.git_ops import push_branch_compare as _push_branch_compare

    with patch("ai_dev_system.task_graph.git_ops.subprocess.run",
               return_value=_fake_git_fail()):
        info = _push_branch_compare(str(tmp_path), "ai-dev/task-x", "master")

    assert info["pushed"] is False
    assert info["compare_url"] is None
    assert "not a git repository" in (info["push_error"] or "")


# ── _create_task_graph_artifact ────────────────────────────────────────────────

def _minimal_db(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE runs (
            run_id TEXT PRIMARY KEY, project_id TEXT, status TEXT,
            title TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_activity_at TEXT, completed_at TEXT, metadata TEXT,
            current_artifacts TEXT DEFAULT '{}', intake_brief_id TEXT,
            gate1_session_state TEXT, paused_reason TEXT, is_resumable INTEGER DEFAULT 0
        );
        CREATE TABLE artifacts (
            artifact_id TEXT PRIMARY KEY, run_id TEXT, artifact_type TEXT,
            version INTEGER DEFAULT 1, status TEXT DEFAULT 'ACTIVE',
            created_by TEXT DEFAULT 'system',
            input_artifact_ids TEXT DEFAULT '[]',
            content_ref TEXT, content_checksum TEXT DEFAULT 'sha256:0',
            content_size INTEGER DEFAULT 0,
            superseded_by TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            annotations TEXT DEFAULT '{}', change_reason TEXT,
            input_checksum TEXT, checksum_scope TEXT
        );
    """)
    conn.commit()
    return conn


def test_create_task_graph_artifact_inserts_db(tmp_path):
    from ai_dev_system.task_graph.single_task_executor import _create_task_graph_artifact

    conn = _minimal_db(tmp_path)
    run_id = "testrun001"
    conn.execute(
        "INSERT INTO runs (run_id, project_id, status, title) VALUES (?, 'adhoc', 'RUNNING_EXECUTION', 'test')",
        (run_id,),
    )
    conn.commit()

    task_graph = {
        "tasks": [{
            "id": "TASK-ADHOC",
            "execution_type": "atomic",
            "agent_type": "RepoBranchAgent",
            "objective": "test",
            "description": "test",
            "deps": [],
            "required_inputs": [],
            "expected_outputs": ["implementation_diff"],
            "facets": {},
        }]
    }
    artifact_id = _create_task_graph_artifact(conn, run_id, task_graph, str(tmp_path))

    row = conn.execute(
        "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)
    ).fetchone()
    assert row is not None
    assert row["artifact_type"] == "TASK_GRAPH_APPROVED"
    tg_file = Path(row["content_ref"]) / "task_graph.json"
    assert tg_file.exists()
    loaded = json.loads(tg_file.read_text())
    assert loaded["tasks"][0]["id"] == "TASK-ADHOC"
    conn.close()


# ── run_executor end-to-end (mocked) ──────────────────────────────────────────

def _write_spec(spec_dir: Path, spec_id: str, repo: str) -> None:
    spec = {
        "status": "done",
        "idea": "add CSV import",
        "repo": repo,
        "task": {
            "id": "TASK-ADHOC",
            "title": "Add CSV import",
            "objective": "Import CSV files",
            "description": "Parse and import CSV",
            "done_definition": "CSV import works",
        },
        "facets": {},
    }
    (spec_dir / f"{spec_id}.json").write_text(
        json.dumps(spec, ensure_ascii=False), encoding="utf-8"
    )


def test_run_executor_creates_exec_log_and_status(tmp_path):
    from ai_dev_system.task_graph.single_task_executor import run_executor

    spec_id = "test-exec-001"
    spec_dir = tmp_path / "task_specs"
    spec_dir.mkdir()
    _write_spec(spec_dir, spec_id, str(tmp_path))

    db_url = f"sqlite:///{tmp_path}/exec.db"
    # Patch all external calls so the test is isolated
    with patch("ai_dev_system.task_graph.single_task_executor._git_base_branch",
               return_value="main"), \
         patch("ai_dev_system.task_graph.single_task_executor._git_checkout_branch"), \
         patch("ai_dev_system.task_graph.single_task_executor.get_connection") as mock_conn, \
         patch("ai_dev_system.task_graph.single_task_executor.run_execution") as mock_run_exec, \
         patch("ai_dev_system.task_graph.single_task_executor.Config.from_env") as mock_cfg:
        # Set up mock connection
        conn_obj = MagicMock()
        conn_obj.__enter__ = MagicMock(return_value=conn_obj)
        conn_obj.__exit__ = MagicMock(return_value=False)
        conn_obj.execute.return_value = MagicMock()
        mock_conn.return_value = conn_obj

        cfg_obj = MagicMock()
        cfg_obj.database_url = db_url
        mock_cfg.return_value = cfg_obj

        # Mock run_execution to return success
        mock_run_exec.return_value = MagicMock(status="COMPLETED")

        from ai_dev_system.task_graph.single_task_plan import plan_single_task, approve_plan
        plan_single_task(json.loads((spec_dir / f"{spec_id}.json").read_text(encoding="utf-8")),
                         spec_id, storage_root=str(tmp_path))
        approve_plan(str(tmp_path), spec_id)

        run_executor(spec_id, str(tmp_path), db_url)

    log_path = spec_dir / f"{spec_id}-exec.log"
    status_path = spec_dir / f"{spec_id}-exec.json"
    assert log_path.exists(), "exec log should be written"
    assert status_path.exists(), "exec status JSON should be written"

    status = json.loads(status_path.read_text())
    assert status["status"] == "done"
    assert status["branch"] == f"ai-dev/task-{spec_id[:8]}"


def test_run_executor_errors_when_plan_not_approved(tmp_path):
    from ai_dev_system.task_graph.single_task_executor import run_executor
    out = tmp_path / "task_specs"
    out.mkdir(parents=True)
    spec_id = "planless1234"
    (out / f"{spec_id}.json").write_text(json.dumps({
        "status": "done", "idea": "x", "repo": str(tmp_path / "repo"),
        "task": {"id": "TASK-ADHOC", "title": "t"}, "facets": {},
    }), encoding="utf-8")
    # No -plan.json written at all → gate must refuse.
    run_executor(spec_id, str(tmp_path), "sqlite:///:memory:")
    status = json.loads((out / f"{spec_id}-exec.json").read_text(encoding="utf-8"))
    assert status["status"] == "error"
    assert "plan" in status["error"].lower()


def test_run_executor_writes_error_if_no_repo(tmp_path):
    from ai_dev_system.task_graph.single_task_executor import run_executor

    spec_id = "test-exec-002"
    spec_dir = tmp_path / "task_specs"
    spec_dir.mkdir()
    # Spec without repo
    (spec_dir / f"{spec_id}.json").write_text(
        json.dumps({"status": "done", "idea": "test", "repo": "", "task": {}, "facets": {}}),
        encoding="utf-8",
    )

    run_executor(spec_id, str(tmp_path), "sqlite:///:memory:")

    status = json.loads((spec_dir / f"{spec_id}-exec.json").read_text())
    assert status["status"] == "error"
    assert "repo" in status["error"]


