"""TDD-first 2-task run end-to-end with claude mocked.

We stub `_invoke_claude` so both agents 'commit' on a real temp git branch and
the reviewers return clean verdicts, then assert ordering + terminal state.

DB/Config pattern adapted from tests/integration/test_executor_e2e.py:
  - direct Config(...) constructor (not from_env) with tmp_path SQLite DB
  - apply_schema(conn) to create tables
  - _create_run_row / _create_task_graph_artifact from single_task_executor
    (sets status = RUNNING_EXECUTION so the worker picks up tasks)
"""
import json
import subprocess
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from ai_dev_system.agents.repo_branch_agent import _ClaudeRun
from ai_dev_system.db.migrator import apply_schema


def _git(args, cwd):
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8"
    )


@pytest.fixture
def temp_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init"], repo)
    _git(["config", "user.email", "t@t"], repo)
    _git(["config", "user.name", "t"], repo)
    (repo / "README.md").write_text("x", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "init"], repo)
    _git(["checkout", "-b", "ai-dev/task-it"], repo)
    return repo


def _claude_commit(repo, fname, msg):
    """Return a fake _ClaudeRun that actually creates+commits a file on the branch."""
    fpath = Path(repo) / fname
    fpath.parent.mkdir(parents=True, exist_ok=True)
    fpath.write_text("content", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", msg], repo)
    return _ClaudeRun(
        returncode=0,
        stdout='{"type":"result","result":"done"}',
        stderr="",
        result_event={"type": "result", "result": "done"},
        subtype="success",
    )


def test_two_task_run_orders_test_before_impl(temp_repo, tmp_path):
    """TASK-1-TEST runs and SUCCEEDs before TASK-1-IMPL; run reaches COMPLETED.

    Both agents commit on the real temp git branch. The stub routes by prompt
    substring (lowercased) to either the test-commit path, the test-review
    verdict, the impl-commit path, or the impl-review verdict.
    """
    from ai_dev_system.config import Config
    from ai_dev_system.db.connection import get_connection
    from ai_dev_system.engine.runner import run_execution
    from ai_dev_system.agents.phase_routing_agent import PhaseRoutingAgent
    from ai_dev_system.task_graph import single_task_executor as ste

    # ── DB + storage setup (mirrors test_executor_e2e.py pattern) ────────────
    db_path = tmp_path / "test.db"
    db_url = f"sqlite:///{db_path}"
    storage_root = tmp_path / "storage"
    storage_root.mkdir()

    cfg = Config(
        storage_root=str(storage_root),
        database_url=db_url,
        poll_interval_s=0.2,
        heartbeat_interval_s=1.0,
        heartbeat_timeout_s=5.0,
        task_timeout_s=30.0,
    )

    conn = get_connection(db_url)
    apply_schema(conn)

    run_id = uuid.uuid4().hex
    # _create_run_row inserts with status = RUNNING_EXECUTION (worker picks it up)
    ste._create_run_row(conn, run_id, "it", "spec-it", "ai-dev/task-it")

    # TDD gate on: emits TASK-1-TEST → TASK-1-IMPL
    graph = ste._build_task_graph(
        {
            "id": "TASK-1",
            "type": "coding",
            "objective": "o",
            "description": "d",
            "done_definition": "",
        },
        {"test_cases": {"status": "filled", "content": "401", "reason": ""}},
        "ai-dev/task-it",
        "master",
    )
    gid = ste._create_task_graph_artifact(conn, run_id, graph, str(storage_root))
    conn.close()

    # ── Prompt-routing fake (substrings match the real prompts, lowercased) ──
    # TestAuthorAgent._build_test_prompt   → "you are writing tests …"
    # TestReviewAgent._build_test_review_prompt → "you are an independent reviewer of tests …"
    # ReviewAgent._build_review_prompt     → "you are an independent reviewer of a code change …"
    # RepoBranchAgent._build_execution_prompt → "you are implementing a coding task …"
    calls = []

    def fake_invoke(claude, cwd, prompt, max_turns, timeout_s, live_log_path=None):
        low = prompt.lower()
        if "reviewer of tests" in low:
            # TestReviewAgent: tests are RED, verdict pass
            return _ClaudeRun(
                0,
                "done",
                "",
                {
                    "type": "result",
                    "result": '{"verdict":"pass","tests_red":true,"findings":[]}',
                },
                "success",
            )
        if "independent reviewer of a code change" in low:
            # ReviewAgent: tests ran + passed, verdict pass
            return _ClaudeRun(
                0,
                "done",
                "",
                {
                    "type": "result",
                    "result": '{"verdict":"pass","tests_ran":true,"tests_passed":true,"findings":[]}',
                },
                "success",
            )
        if "writing tests" in low:
            # TestAuthorAgent: write failing tests, commit
            calls.append("test")
            return _claude_commit(cwd, "tests/test_new.py", "test: add failing tests")
        # RepoBranchAgent / fix pass: implement feature, commit
        calls.append("impl")
        return _claude_commit(cwd, "src/feature.py", "feat: implement")

    repo_str = str(temp_repo)
    with (
        patch("ai_dev_system.agents.repo_branch_agent._invoke_claude", side_effect=fake_invoke),
        patch("ai_dev_system.agents.test_author_agent._invoke_claude", side_effect=fake_invoke),
        patch("ai_dev_system.agents.test_review_agent._invoke_claude", side_effect=fake_invoke),
        patch("ai_dev_system.agents.review_agent._invoke_claude", side_effect=fake_invoke),
        patch(
            "ai_dev_system.llm_factory.ClaudeCodeLLMClient._resolve_claude_cmd",
            return_value="claude",
        ),
    ):
        agent = PhaseRoutingAgent(repo_str, "ai-dev/task-it", "master")
        result = run_execution(run_id, gid, cfg, agent, poll_interval_s=0.2)

    # ── Assertions ────────────────────────────────────────────────────────────
    assert result.status == "COMPLETED", (
        f"Expected COMPLETED, got {result.status!r}. "
        "Worker may not have picked up tasks or agents returned errors."
    )
    assert "test" in calls, "test phase never ran"
    assert "impl" in calls, "impl phase never ran"
    assert calls.index("test") < calls.index("impl"), (
        f"Expected test before impl, got order: {calls}"
    )
    assert calls == ["test", "impl"], "clean run: each phase ran exactly once, test before impl"

    log = _git(["log", "--oneline"], temp_repo).stdout
    assert "test: add failing tests" in log, f"Test commit missing from log:\n{log}"
    assert "feat: implement" in log, f"Impl commit missing from log:\n{log}"
