# Single-Task Execution via Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sau khi user duyệt một task spec trong webui, tự động tạo git branch, chạy execution engine (RepoBranchAgent → claude với full tools), và hiển thị git diff để review/accept/reject.

**Architecture:** `single_task_executor.py` đọc approved spec → tạo git branch trong repo → tạo synthetic `runs` row + `TASK_GRAPH_APPROVED` artifact → gọi `run_execution()` từ engine với `RepoBranchAgent` → agent chạy `claude -p` với Edit/Write/Bash trên branch → capture `git diff` → lưu vào EXECUTION_LOG artifact. Webui cung cấp route `/task-exec` để theo dõi progress (poll DB + log file) và Accept/Reject branch.

**Tech Stack:** Python stdlib, sqlite3, subprocess (git + claude CLI), existing `engine/runner.py`, `engine/materializer.py`, `engine/worker.py`

## Global Constraints

- Python 3.12+, zero new dependencies (stdlib only + existing project deps)
- SQLite WAL mode — mỗi thread/process lấy connection riêng qua `get_connection(database_url)`
- Tất cả git operations dùng `subprocess.run([...], cwd=repo_path, capture_output=True, text=True, encoding="utf-8")`
- Log file: `task_specs/<spec_id>-exec.log`, exec status JSON: `task_specs/<spec_id>-exec.json`
- Exec branch name: `ai-dev/task-<spec_id[:8]>` (fixed format)
- Claude timeout cho execution: 1800s (30 phút)
- `run_execution()` signature: `run_execution(run_id, graph_artifact_id, config, agent, poll_interval_s=5.0) -> ExecutionResult`
- Agent protocol: `agent.run(task_id, output_path, promoted_outputs=(), context=None, timeout_s=3600.0, file_rules=()) -> AgentResult`
- SPEC_FACET_KEYS = 13 facets; EXEC_FACET_KEYS = 7 facets (set to _EXEC_NA at spec time)

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/ai_dev_system/agents/repo_branch_agent.py` | CREATE | Agent gọi `claude -p` với full tools trên repo branch |
| `src/ai_dev_system/task_graph/single_task_executor.py` | CREATE | Bridge worker: spec → git branch → execution engine |
| `src/ai_dev_system/webui.py` | MODIFY | Routes: `/task-exec` GET + POST accept/reject; spawn executor on approve |
| `tests/unit/agents/test_repo_branch_agent.py` | CREATE | Unit tests cho RepoBranchAgent |
| `tests/unit/test_single_task_executor.py` | CREATE | Unit tests cho bridge worker |

---

## Task 1: RepoBranchAgent

**Files:**
- Create: `src/ai_dev_system/agents/repo_branch_agent.py`
- Test: `tests/unit/agents/test_repo_branch_agent.py`

**Interfaces:**
- Consumes: Agent Protocol từ `agents/base.py`: `run(task_id, output_path, promoted_outputs, context, timeout_s, file_rules) -> AgentResult`
- Consumes: `ClaudeCodeLLMClient._resolve_claude_cmd()` để lấy path của claude CLI
- Produces: `RepoBranchAgent(repo_path: str, branch_name: str, base_branch: str)` — class implement Agent protocol
- Produces: viết `diff.txt` và `summary.txt` vào `output_path`

- [ ] **Step 1: Viết failing test cho `_build_execution_prompt`**

```python
# tests/unit/agents/test_repo_branch_agent.py
import pytest
from ai_dev_system.agents.repo_branch_agent import _build_execution_prompt

def _ctx(objective="Add login", description="Implement JWT login", facets=None):
    return {
        "task_id": "TASK-ADHOC",
        "objective": objective,
        "description": description,
        "done_definition": "Login endpoint returns JWT",
        "facets": facets or {},
    }

def test_prompt_contains_objective():
    prompt = _build_execution_prompt(_ctx())
    assert "Add login" in prompt

def test_prompt_includes_filled_facets():
    ctx = _ctx(facets={
        "input": {"status": "filled", "content": "POST /login {email, password}", "reason": ""},
        "auth_permission": {"status": "needs_human", "content": "", "reason": ""},
        "database": {"status": "na", "content": "", "reason": "no schema change"},
    })
    prompt = _build_execution_prompt(ctx)
    assert "POST /login {email, password}" in prompt
    # needs_human và na không nên inject noise vào prompt
    assert "needs_human" not in prompt

def test_prompt_excludes_na_and_needs_human_facets():
    ctx = _ctx(facets={
        "input": {"status": "na", "content": "", "reason": "irrelevant"},
        "response": {"status": "needs_human", "content": "", "reason": ""},
    })
    prompt = _build_execution_prompt(ctx)
    # chỉ tiêu đề facet section hoặc không có content nào
    assert "irrelevant" not in prompt
```

- [ ] **Step 2: Chạy test để confirm fail**

```
cd e:\Work\ai-development-system
python -m pytest tests/unit/agents/test_repo_branch_agent.py -x -q
```
Expected: `ModuleNotFoundError` hoặc `ImportError` vì file chưa tồn tại.

- [ ] **Step 3: Implement `_build_execution_prompt` và `RepoBranchAgent`**

```python
# src/ai_dev_system/agents/repo_branch_agent.py
"""Agent that runs claude -p with full tools on a git branch of the target repo.

Writes diff.txt and summary.txt to output_path after execution.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from ai_dev_system.agents.base import AgentResult, PromotedOutput
from ai_dev_system.llm_factory import ClaudeCodeLLMClient
from ai_dev_system.task_graph.facets import SPEC_FACET_KEYS

if TYPE_CHECKING:
    pass

# claude -p flags for execution (full tool access — NOT readonly)
_EXEC_FLAGS = [
    "--output-format", "json",
    "--permission-mode", "bypassPermissions",
    "--max-turns", "30",
]


def _build_execution_prompt(context: dict) -> str:
    facets = context.get("facets") or {}
    filled_lines = []
    for key in SPEC_FACET_KEYS:
        f = facets.get(key) or {}
        if f.get("status") == "filled" and f.get("content", "").strip():
            filled_lines.append(f"### {key}\n{f['content']}")

    spec_section = "\n\n".join(filled_lines) if filled_lines else "(no spec facets filled)"

    return (
        "You are implementing a coding task in THIS repository. "
        "Read existing code to understand patterns and conventions before writing anything. "
        "Implement the task completely, write tests, and commit your changes with a "
        "meaningful commit message.\n\n"
        f"## Task\n"
        f"**Objective:** {context.get('objective', '')}\n"
        f"**Description:** {context.get('description', '')}\n"
        f"**Done when:** {context.get('done_definition', '')}\n\n"
        f"## Technical Specification\n{spec_section}\n\n"
        "## Rules\n"
        "- Follow existing code style and patterns in this repo\n"
        "- Write or update tests for every behaviour you add or change\n"
        "- Run existing tests before committing — fix failures if they relate to your change\n"
        "- Commit with: `git add -A && git commit -m '<type>: <summary>'`\n"
        "- Do NOT push to remote\n"
    )


def _git(args: list[str], cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


class RepoBranchAgent:
    """Implements Agent protocol. Runs claude -p with full tools on a git branch."""

    def __init__(self, repo_path: str, branch_name: str, base_branch: str) -> None:
        self.repo_path = repo_path
        self.branch_name = branch_name
        self.base_branch = base_branch

    def run(
        self,
        task_id: str,
        output_path: str,
        promoted_outputs=(),
        context: dict | None = None,
        timeout_s: float = 1800.0,
        file_rules: list = (),
    ) -> AgentResult:
        Path(output_path).mkdir(parents=True, exist_ok=True)
        context = context or {}

        try:
            claude = ClaudeCodeLLMClient._resolve_claude_cmd()
        except Exception as exc:
            return AgentResult(output_path=output_path, error=f"claude CLI not found: {exc}")

        prompt = _build_execution_prompt(context)
        cmd = [claude, "-p", prompt, *_EXEC_FLAGS]

        proc = subprocess.run(
            cmd, cwd=self.repo_path,
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=int(timeout_s),
        )

        # Capture git diff regardless of claude exit code
        diff_proc = _git(["diff", self.base_branch + "..HEAD"], self.repo_path)
        diff_text = diff_proc.stdout or "(no diff)"

        # Extract summary from claude output
        summary = f"claude exit={proc.returncode}"
        stdout = proc.stdout or ""
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict) and obj.get("type") == "result":
                    result_text = obj.get("result") or ""
                    summary = result_text[:500] if result_text else summary
                    break
            except json.JSONDecodeError:
                continue

        Path(output_path, "diff.txt").write_text(diff_text, encoding="utf-8")
        Path(output_path, "summary.txt").write_text(summary, encoding="utf-8")
        Path(output_path, "claude_stderr.txt").write_text(proc.stderr or "", encoding="utf-8")

        if proc.returncode != 0:
            return AgentResult(
                output_path=output_path,
                error=f"claude CLI exited {proc.returncode}. stderr: {(proc.stderr or '')[:300]}",
            )

        return AgentResult(
            output_path=output_path,
            promoted_outputs=[PromotedOutput("implementation_diff", "EXECUTION_LOG")],
        )
```

- [ ] **Step 4: Chạy tests**

```
python -m pytest tests/unit/agents/test_repo_branch_agent.py -x -q
```
Expected: 4 passed.

- [ ] **Step 5: Viết test cho git fallback (claude fail → vẫn write diff)**

```python
# Thêm vào tests/unit/agents/test_repo_branch_agent.py

import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock
from ai_dev_system.agents.repo_branch_agent import RepoBranchAgent


def _fake_run_ok(cmd, **kw):
    return subprocess.CompletedProcess(cmd, 0, stdout='{"type":"result","result":"done"}', stderr="")

def _fake_run_fail(cmd, **kw):
    return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="auth error")

def _fake_git(args, **kw):
    # subprocess.run called with ["git", ...] — return a simple diff
    return subprocess.CompletedProcess(["git"] + args, 0, stdout="diff --git a/x.py b/x.py\n+new line", stderr="")


def test_agent_writes_diff_on_success(tmp_path):
    repo = str(tmp_path)
    agent = RepoBranchAgent(repo, "ai-dev/task-abc", "main")
    ctx = _ctx()
    with patch("ai_dev_system.agents.repo_branch_agent.ClaudeCodeLLMClient._resolve_claude_cmd", return_value="claude"), \
         patch("ai_dev_system.agents.repo_branch_agent.subprocess.run", side_effect=[_fake_run_ok(None), _fake_git(None)]):
        result = agent.run("TASK-ADHOC", str(tmp_path / "out"), context=ctx)
    assert result.success
    assert (tmp_path / "out" / "diff.txt").exists()


def test_agent_still_writes_diff_on_claude_failure(tmp_path):
    repo = str(tmp_path)
    agent = RepoBranchAgent(repo, "ai-dev/task-abc", "main")
    ctx = _ctx()
    with patch("ai_dev_system.agents.repo_branch_agent.ClaudeCodeLLMClient._resolve_claude_cmd", return_value="claude"), \
         patch("ai_dev_system.agents.repo_branch_agent.subprocess.run", side_effect=[_fake_run_fail(None), _fake_git(None)]):
        result = agent.run("TASK-ADHOC", str(tmp_path / "out"), context=ctx)
    assert not result.success
    assert (tmp_path / "out" / "diff.txt").exists()
```

- [ ] **Step 6: Chạy toàn bộ tests**

```
python -m pytest tests/unit/agents/test_repo_branch_agent.py -x -q
```
Expected: 6 passed.

- [ ] **Step 7: Commit**

```bash
git add src/ai_dev_system/agents/repo_branch_agent.py tests/unit/agents/test_repo_branch_agent.py
git commit -m "feat: add RepoBranchAgent — runs claude with full tools on git branch"
```

---

## Task 2: single_task_executor.py (Bridge Worker)

**Files:**
- Create: `src/ai_dev_system/task_graph/single_task_executor.py`
- Test: `tests/unit/test_single_task_executor.py`

**Interfaces:**
- Consumes: `task_specs/<spec_id>.json` (must have `status=="done"` và `approved==True`, fields: `task`, `facets`, `repo`)
- Consumes: `run_execution(run_id, graph_artifact_id, config, agent, poll_interval_s) -> ExecutionResult` từ `engine/runner.py`
- Consumes: `get_connection(database_url) -> sqlite3.Connection` từ `db/connection.py`
- Consumes: `Config.from_env()` từ `config.py`
- Consumes: `RepoBranchAgent(repo_path, branch_name, base_branch)` từ task 1
- Produces: `run_executor(spec_id, storage_root, database_url) -> None` — blocking, chạy đến khi xong
- Produces: viết `task_specs/<spec_id>-exec.log` (progress)
- Produces: viết `task_specs/<spec_id>-exec.json` với schema:
  ```json
  {"status": "running|done|error", "run_id": "...", "branch": "ai-dev/task-<8>", "base_branch": "...", "error": "..."}
  ```
- Produces: `main(argv=None) -> int` — CLI entry point (`python -m ai_dev_system.task_graph.single_task_executor --id ... --storage-root ... --database-url ...`)

- [ ] **Step 1: Viết failing tests cho git helpers**

```python
# tests/unit/test_single_task_executor.py
import json
import subprocess
import uuid
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


def _fake_git_ok(stdout="main"):
    p = MagicMock()
    p.returncode = 0
    p.stdout = stdout
    p.stderr = ""
    return p


def _fake_git_fail():
    p = MagicMock()
    p.returncode = 1
    p.stdout = ""
    p.stderr = "fatal: not a git repository"
    return p


def test_get_current_branch_returns_stripped_name():
    from ai_dev_system.task_graph.single_task_executor import _git_current_branch
    with patch("ai_dev_system.task_graph.single_task_executor.subprocess.run",
               return_value=_fake_git_ok("  main\n")):
        branch = _git_current_branch("/some/repo")
    assert branch == "main"


def test_get_current_branch_raises_on_fail():
    from ai_dev_system.task_graph.single_task_executor import _git_current_branch
    with patch("ai_dev_system.task_graph.single_task_executor.subprocess.run",
               return_value=_fake_git_fail()):
        with pytest.raises(RuntimeError, match="not a git repository"):
            _git_current_branch("/not/a/repo")


def test_checkout_new_branch_calls_git(tmp_path):
    from ai_dev_system.task_graph.single_task_executor import _git_checkout_branch
    with patch("ai_dev_system.task_graph.single_task_executor.subprocess.run",
               return_value=_fake_git_ok("")) as mock_run:
        _git_checkout_branch(str(tmp_path), "ai-dev/task-abc123")
    args = mock_run.call_args[0][0]
    assert "checkout" in args
    assert "ai-dev/task-abc123" in args


def test_create_task_graph_artifact_inserts_db(tmp_path):
    import sqlite3
    from ai_dev_system.task_graph.single_task_executor import _create_task_graph_artifact

    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    # Minimal schema
    conn.executescript("""
        CREATE TABLE runs (run_id TEXT PRIMARY KEY, project_id TEXT, status TEXT,
            title TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_activity_at TEXT, completed_at TEXT, metadata TEXT,
            current_artifacts TEXT DEFAULT '{}', intake_brief_id TEXT,
            gate1_session_state TEXT, paused_reason TEXT, is_resumable INTEGER DEFAULT 0);
        CREATE TABLE artifacts (
            artifact_id TEXT PRIMARY KEY, run_id TEXT, artifact_type TEXT,
            version INTEGER, status TEXT DEFAULT 'ACTIVE', created_by TEXT DEFAULT 'system',
            input_artifact_ids TEXT DEFAULT '[]',
            content_ref TEXT, content_checksum TEXT DEFAULT 'sha256:0',
            content_size INTEGER DEFAULT 0,
            superseded_by TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            annotations TEXT DEFAULT '{}', change_reason TEXT,
            input_checksum TEXT, checksum_scope TEXT);
    """)
    conn.commit()
    run_id = "testrun001"
    conn.execute("INSERT INTO runs (run_id, project_id, status, title) VALUES (?, 'adhoc', 'RUNNING_EXECUTION', 'test')", (run_id,))
    conn.commit()

    task_graph = {"tasks": [{"id": "TASK-ADHOC", "execution_type": "atomic", "agent_type": "RepoBranchAgent",
                              "objective": "test", "description": "test", "deps": [], "required_inputs": [],
                              "expected_outputs": ["implementation_diff"], "facets": {}}]}
    artifact_id = _create_task_graph_artifact(conn, run_id, task_graph, str(tmp_path))

    row = conn.execute("SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)).fetchone()
    assert row is not None
    assert row["artifact_type"] == "TASK_GRAPH_APPROVED"
    assert (Path(row["content_ref"]) / "task_graph.json").exists()
    conn.close()
```

- [ ] **Step 2: Chạy để confirm fail**

```
python -m pytest tests/unit/test_single_task_executor.py -x -q
```
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `single_task_executor.py`**

```python
# src/ai_dev_system/task_graph/single_task_executor.py
"""Bridge worker: approved task spec → git branch → execution engine.

Spawned detached by webui after spec approval. Writes:
  task_specs/<spec_id>-exec.log  — progress lines
  task_specs/<spec_id>-exec.json — status/result summary
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import subprocess
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _git(args: list[str], cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def _git_current_branch(repo_path: str) -> str:
    proc = _git(["rev-parse", "--abbrev-ref", "HEAD"], repo_path)
    if proc.returncode != 0:
        raise RuntimeError(f"git rev-parse failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def _git_checkout_branch(repo_path: str, branch_name: str) -> None:
    # Try checkout existing branch first, then create new
    proc = _git(["checkout", branch_name], repo_path)
    if proc.returncode != 0:
        proc2 = _git(["checkout", "-b", branch_name], repo_path)
        if proc2.returncode != 0:
            raise RuntimeError(f"git checkout -b {branch_name!r} failed: {proc2.stderr.strip()}")


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _create_run_row(conn, run_id: str, title: str, spec_id: str, branch: str) -> None:
    import json as _json
    metadata = _json.dumps({"kind": "task_exec", "spec_id": spec_id, "branch": branch})
    conn.execute(
        """
        INSERT INTO runs (run_id, project_id, status, title, metadata, current_artifacts)
        VALUES (?, 'adhoc-task-exec', 'RUNNING_EXECUTION', ?, ?, '{}')
        """,
        (run_id, title[:60], metadata),
    )
    conn.commit()


def _create_task_graph_artifact(conn, run_id: str, task_graph: dict, storage_root: str) -> str:
    artifact_id = uuid.uuid4().hex
    artifact_dir = Path(storage_root) / "task_execs" / run_id / "task_graph"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    content = json.dumps(task_graph, indent=2, ensure_ascii=False)
    (artifact_dir / "task_graph.json").write_text(content, encoding="utf-8")
    checksum = "sha256:" + hashlib.sha256(content.encode()).hexdigest()
    conn.execute(
        """
        INSERT INTO artifacts
            (artifact_id, run_id, artifact_type, version, status, created_by,
             input_artifact_ids, content_ref, content_checksum, content_size, annotations)
        VALUES (?, ?, 'TASK_GRAPH_APPROVED', 1, 'ACTIVE', 'system', '[]', ?, ?, ?, '{}')
        """,
        (artifact_id, run_id, str(artifact_dir), checksum, len(content)),
    )
    conn.commit()
    return artifact_id


# ---------------------------------------------------------------------------
# Exec log helpers
# ---------------------------------------------------------------------------

def _exec_log(log_path: Path, msg: str) -> None:
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        f.flush()


def _write_exec_status(status_path: Path, data: dict) -> None:
    status_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main worker
# ---------------------------------------------------------------------------

def run_executor(spec_id: str, storage_root: str, database_url: str | None = None) -> None:
    out_dir = Path(storage_root) / "task_specs"
    out_dir.mkdir(parents=True, exist_ok=True)
    spec_path = out_dir / f"{spec_id}.json"
    log_path = out_dir / f"{spec_id}-exec.log"
    status_path = out_dir / f"{spec_id}-exec.json"

    _exec_log(log_path, "Executor khởi động")

    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except Exception as exc:
        _exec_log(log_path, f"LỖI đọc spec: {exc}")
        _write_exec_status(status_path, {"status": "error", "error": str(exc)})
        return

    repo_path = spec.get("repo") or ""
    task = spec.get("task") or {}
    facets = spec.get("facets") or {}

    if not repo_path:
        _exec_log(log_path, "LỖI: spec không có repo path — không thể execute")
        _write_exec_status(status_path, {"status": "error", "error": "no repo path in spec"})
        return

    branch_name = f"ai-dev/task-{spec_id[:8]}"
    _exec_log(log_path, f"Repo: {repo_path}")

    # 1. Get current branch and checkout new branch
    try:
        base_branch = _git_current_branch(repo_path)
        _exec_log(log_path, f"Base branch: {base_branch}")
        _git_checkout_branch(repo_path, branch_name)
        _exec_log(log_path, f"Branch created/checked out: {branch_name}")
    except Exception as exc:
        _exec_log(log_path, f"LỖI git branch: {exc}")
        _write_exec_status(status_path, {"status": "error", "error": str(exc)})
        return

    _write_exec_status(status_path, {
        "status": "running", "branch": branch_name, "base_branch": base_branch,
    })

    # 2. Setup DB
    if database_url is None:
        from ai_dev_system.config import Config
        cfg = Config.from_env()
        database_url = cfg.database_url
    else:
        from ai_dev_system.config import Config
        cfg = Config.from_env()

    from ai_dev_system.db.connection import get_connection
    conn = get_connection(database_url)

    run_id = uuid.uuid4().hex
    title = str(task.get("title") or spec.get("idea") or "Task exec")
    try:
        _create_run_row(conn, run_id, title, spec_id, branch_name)
        _exec_log(log_path, f"Run row created: {run_id[:8]}")
    except Exception as exc:
        _exec_log(log_path, f"LỖI tạo run row: {exc}")
        _write_exec_status(status_path, {"status": "error", "error": str(exc), "branch": branch_name, "base_branch": base_branch})
        conn.close()
        return

    # 3. Build task_graph.json and create TASK_GRAPH_APPROVED artifact
    task_graph = {
        "tasks": [{
            "id": task.get("id") or "TASK-ADHOC",
            "execution_type": "atomic",
            "agent_type": "RepoBranchAgent",
            "phase": "implementation",
            "type": task.get("type") or "coding",
            "objective": task.get("objective") or "",
            "description": task.get("description") or "",
            "done_definition": f"Code committed to branch {branch_name}",
            "verification_steps": [],
            "required_inputs": [],
            "expected_outputs": ["implementation_diff"],
            "deps": [],
            "facets": facets,
        }]
    }
    try:
        graph_artifact_id = _create_task_graph_artifact(conn, run_id, task_graph, storage_root)
        _exec_log(log_path, f"TASK_GRAPH_APPROVED artifact: {graph_artifact_id[:8]}")
    except Exception as exc:
        _exec_log(log_path, f"LỖI tạo task graph artifact: {exc}")
        _write_exec_status(status_path, {"status": "error", "error": str(exc), "run_id": run_id, "branch": branch_name, "base_branch": base_branch})
        conn.close()
        return

    conn.close()

    # 4. Run execution engine
    _exec_log(log_path, "Đang chạy execution engine (claude -p với full tools, tối đa 30 phút)…")
    _write_exec_status(status_path, {
        "status": "running", "run_id": run_id, "branch": branch_name, "base_branch": base_branch,
    })

    from ai_dev_system.agents.repo_branch_agent import RepoBranchAgent
    from ai_dev_system.engine.runner import run_execution

    agent = RepoBranchAgent(
        repo_path=repo_path,
        branch_name=branch_name,
        base_branch=base_branch,
    )

    try:
        result = run_execution(run_id, graph_artifact_id, cfg, agent, poll_interval_s=5.0)
        _exec_log(log_path, f"Execution xong: {result.status}")
        _write_exec_status(status_path, {
            "status": "done", "exec_status": result.status,
            "run_id": run_id, "branch": branch_name, "base_branch": base_branch,
        })
    except Exception as exc:
        _exec_log(log_path, f"LỖI execution: {type(exc).__name__}: {exc}")
        _write_exec_status(status_path, {
            "status": "error", "error": str(exc),
            "run_id": run_id, "branch": branch_name, "base_branch": base_branch,
        })


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--id", required=True)
    p.add_argument("--storage-root", required=True)
    p.add_argument("--database-url", default=None)
    args = p.parse_args(argv)
    run_executor(args.id, args.storage_root, args.database_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Chạy tests**

```
python -m pytest tests/unit/test_single_task_executor.py -x -q
```
Expected: 4 passed.

- [ ] **Step 5: Chạy full test suite để check không có regression**

```
python -m pytest tests/ -x -q 2>&1 | tail -5
```
Expected: tất cả pass.

- [ ] **Step 6: Commit**

```bash
git add src/ai_dev_system/task_graph/single_task_executor.py tests/unit/test_single_task_executor.py
git commit -m "feat: add single_task_executor — bridges approved spec into execution engine"
```

---

## Task 3: Webui Integration

**Files:**
- Modify: `src/ai_dev_system/webui.py`
- Test: `tests/unit/test_task_spec_webui.py` (thêm test cases)

**Interfaces:**
- Consumes: `task_specs/<spec_id>-exec.json` để hiện trạng thái
- Consumes: `task_specs/<spec_id>-exec.log` để hiện progress
- Consumes: `runs` table + `task_runs` table để hiện task status từ engine
- Consumes: `_spawn_task_spec_worker` pattern để spawn executor process
- Produces: `GET /task-exec?id=<spec_id>` — trang progress + diff + accept/reject
- Produces: `POST /task-exec` với field `action=accept|reject` và `id=<spec_id>`
- Produces: Sửa POST `/task-spec` (approve) để spawn executor sau khi lưu

Thay đổi trong `do_POST` path `/task-spec`:
```python
# Sau khi _save_task_spec_edits() — thêm:
if spec_id:
    data = json.loads((Path(_config().storage_root) / "task_specs" / f"{spec_id}.json")
                      .read_text(encoding="utf-8"))
    if data.get("repo"):  # chỉ spawn executor nếu có repo
        _spawn_task_executor(spec_id)
```

- [ ] **Step 1: Viết failing tests cho webui exec routes**

```python
# Thêm vào tests/unit/test_task_spec_webui.py (hoặc test_webui_task_spec.py)

def test_task_exec_page_running_shows_log(tmp_path, monkeypatch):
    """GET /task-exec?id=X khi đang chạy shows log và auto-refresh."""
    import json, io
    from unittest.mock import patch
    from ai_dev_system import webui

    spec_id = "exec001"
    exec_status = {"status": "running", "run_id": "r1", "branch": "ai-dev/task-exec001", "base_branch": "main"}
    exec_log = "[10:00:00] Executor khởi động\n[10:00:01] Branch created: ai-dev/task-exec001\n"

    spec_dir = tmp_path / "task_specs"
    spec_dir.mkdir()
    (spec_dir / f"{spec_id}-exec.json").write_text(json.dumps(exec_status))
    (spec_dir / f"{spec_id}-exec.log").write_text(exec_log)

    monkeypatch.setattr(webui, "_config", lambda: type("C", (), {"storage_root": str(tmp_path), "database_url": "sqlite:///:memory:"})())

    body = webui._task_exec_page(spec_id).decode("utf-8")
    assert "Đang chạy" in body or "running" in body.lower()
    assert "Executor khởi động" in body


def test_task_exec_page_done_shows_diff(tmp_path, monkeypatch):
    """GET /task-exec?id=X khi done shows git diff."""
    import json
    from ai_dev_system import webui

    spec_id = "exec002"
    exec_status = {
        "status": "done", "exec_status": "COMPLETED",
        "run_id": "r2", "branch": "ai-dev/task-exec002", "base_branch": "main",
    }
    exec_log = "[10:05:00] Execution xong: COMPLETED\n"
    diff_text = "diff --git a/src/foo.py b/src/foo.py\n+def new_func(): pass\n"

    spec_dir = tmp_path / "task_specs"
    spec_dir.mkdir()
    (spec_dir / f"{spec_id}-exec.json").write_text(json.dumps(exec_status))
    (spec_dir / f"{spec_id}-exec.log").write_text(exec_log)

    # Simulate artifact diff file — webui reads from task_runs in real case,
    # for test we mock _task_exec_diff
    monkeypatch.setattr(webui, "_config", lambda: type("C", (), {"storage_root": str(tmp_path), "database_url": "sqlite:///:memory:"})())
    monkeypatch.setattr(webui, "_task_exec_diff", lambda spec_id, run_id, cfg: diff_text)

    body = webui._task_exec_page(spec_id).decode("utf-8")
    assert "def new_func" in body
    assert "Accept" in body or "accept" in body.lower()
    assert "Reject" in body or "reject" in body.lower()
```

- [ ] **Step 2: Chạy để confirm fail**

```
python -m pytest tests/unit/test_task_spec_webui.py -x -q -k "exec"
```
Expected: ImportError hoặc AttributeError (functions chưa tồn tại).

- [ ] **Step 3: Implement webui changes**

Thêm vào `webui.py` — helper functions (sau `_task_spec_log_card`):

```python
def _spawn_task_executor(spec_id: str) -> None:
    """Spawn single_task_executor as detached background process."""
    cfg = _config()
    popen_kwargs: dict = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True
    subprocess.Popen(
        [sys.executable, "-m", "ai_dev_system.task_graph.single_task_executor",
         "--id", spec_id,
         "--storage-root", str(cfg.storage_root),
         "--database-url", str(cfg.database_url)],
        cwd=str(Path(__file__).resolve().parents[2]),
        **popen_kwargs,
    )


def _task_exec_status(spec_id: str) -> dict:
    path = Path(_config().storage_root) / "task_specs" / f"{spec_id}-exec.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}


def _task_exec_log_lines(spec_id: str) -> list[str]:
    log_path = Path(_config().storage_root) / "task_specs" / f"{spec_id}-exec.log"
    if not log_path.exists():
        return []
    try:
        return log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-50:]
    except OSError:
        return []


def _task_exec_diff(spec_id: str, run_id: str, cfg) -> str:
    """Read diff.txt from the EXECUTION_LOG artifact for this run."""
    conn = get_connection(cfg.database_url)
    try:
        row = conn.execute(
            """SELECT a.content_ref FROM artifacts a
               JOIN task_runs tr ON tr.output_artifact_id = a.artifact_id
               WHERE tr.run_id = ? AND a.artifact_type = 'EXECUTION_LOG'
               ORDER BY a.created_at DESC LIMIT 1""",
            (run_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return "(diff chưa có — execution đang chạy hoặc không có output)"
    diff_file = Path(row["content_ref"]) / "diff.txt"
    if not diff_file.exists():
        return "(diff.txt không tìm thấy trong artifact)"
    try:
        return diff_file.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"(lỗi đọc diff: {exc})"


def _task_exec_page(spec_id: str) -> bytes:
    exec_st = _task_exec_status(spec_id)
    log_lines = _task_exec_log_lines(spec_id)
    log_pre = html.escape("\n".join(log_lines)) if log_lines else "(chưa có log)"
    head = f"<p><a href='/'>← trang chủ</a></p>"
    branch = html.escape(exec_st.get("branch") or "")
    base = html.escape(exec_st.get("base_branch") or "")
    run_id = exec_st.get("run_id") or ""
    status = exec_st.get("status") or "unknown"

    if not exec_st:
        return _page("task exec", head + "<div class='card muted'>Chưa có thông tin execution. "
                     "Thử refresh sau vài giây.</div>",
                     head_extra="<meta http-equiv='refresh' content='3'>")

    branch_card = (
        f"<div class='card'><h2>Branch</h2>"
        f"<p class='muted'>Branch: <b>{branch}</b> (từ {base})</p>"
        f"<p class='muted'>Run: {html.escape(run_id[:8]) if run_id else '—'}</p></div>"
    )
    log_card = f"<div class='card'><h2>Log tiến trình</h2><pre>{log_pre}</pre></div>"

    if status == "running":
        return _page("Task execution",
                     head + branch_card + log_card,
                     head_extra="<meta http-equiv='refresh' content='5'>")

    if status == "error":
        err = html.escape(exec_st.get("error") or "")
        return _page("Task execution",
                     head + branch_card
                     + f"<div class='card'><h2>⚠ Lỗi execution</h2><p class='caveat'>{err}</p></div>"
                     + log_card)

    # status == "done"
    cfg = _config()
    diff_text = _task_exec_diff(spec_id, run_id, cfg)
    diff_html = html.escape(diff_text)
    exec_status_badge = _badge(exec_st.get("exec_status") or "COMPLETED")
    diff_card = (
        f"<div class='card'><h2>Git diff — {exec_status_badge}</h2>"
        f"<pre style='max-height:600px;overflow-y:auto'>{diff_html}</pre></div>"
    )
    action_card = (
        "<div class='card'><h2>Hành động</h2>"
        "<form method='post' action='/task-exec' style='display:inline;margin-right:12px'>"
        f"<input type='hidden' name='id' value='{html.escape(spec_id)}'>"
        "<input type='hidden' name='action' value='accept'>"
        "<button type='submit' style='background:#1a6b2a'>✓ Accept branch</button>"
        "</form>"
        "<form method='post' action='/task-exec' style='display:inline'>"
        f"<input type='hidden' name='id' value='{html.escape(spec_id)}'>"
        "<input type='hidden' name='action' value='reject'>"
        "<button type='submit' style='background:#6b1a1a'>✗ Reject &amp; xóa branch</button>"
        "</form>"
        f"<p class='muted' style='margin-top:10px'>Accept: branch <b>{branch}</b> giữ nguyên, bạn merge thủ công. "
        f"Reject: xóa branch, quay lại <b>{base}</b>.</p></div>"
    )
    return _page("Task execution", head + branch_card + diff_card + action_card + log_card)
```

Thêm handler trong `do_POST` (trong class `Handler`):

```python
elif path == "/task-exec":
    length = int(self.headers.get("Content-Length", "0"))
    form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
    spec_id = (form.get("id") or [""])[0].strip()
    action = (form.get("action") or [""])[0].strip()
    if not spec_id:
        self._send(_page("error", "<div class='card muted'>Thiếu spec id.</div>"), 400)
        return
    exec_st = _task_exec_status(spec_id)
    branch = exec_st.get("branch") or ""
    base = exec_st.get("base_branch") or ""
    repo = ""
    try:
        spec_data = json.loads((Path(_config().storage_root) / "task_specs" / f"{spec_id}.json")
                               .read_text(encoding="utf-8"))
        repo = spec_data.get("repo") or ""
    except Exception:
        pass

    if action == "accept":
        body = (
            "<div class='card'><h2>Branch accepted ✓</h2>"
            f"<p>Branch <b>{html.escape(branch)}</b> đã được giữ lại.</p>"
            f"<p class='muted'>Để merge: <code>git checkout {html.escape(base)} && git merge --no-ff {html.escape(branch)}</code></p>"
            "<p><a href='/'>← trang chủ</a></p></div>"
        )
        self._send(_page("accepted", body))
    elif action == "reject":
        msg = "Branch đã bị xóa."
        if branch and repo:
            try:
                result = subprocess.run(
                    ["git", "checkout", base],
                    cwd=repo, capture_output=True, text=True, encoding="utf-8",
                )
                subprocess.run(
                    ["git", "branch", "-D", branch],
                    cwd=repo, capture_output=True, text=True, encoding="utf-8",
                )
            except Exception as exc:
                msg = f"Lỗi xóa branch: {html.escape(str(exc))}"
        body = (
            f"<div class='card'><h2>Branch rejected ✗</h2><p>{msg}</p>"
            "<p><a href='/'>← trang chủ</a></p></div>"
        )
        self._send(_page("rejected", body))
    else:
        self._send(_page("error", "<div class='card muted'>Action không hợp lệ.</div>"), 400)
```

Thêm GET route `/task-exec` trong `do_GET`:

```python
elif parsed.path == "/task-exec":
    qs = urllib.parse.parse_qs(parsed.query)
    self._send(_task_exec_page((qs.get("id") or [""])[0]))
```

Sửa POST `/task-spec` — sau `_save_task_spec_edits()`:

```python
# Sau dòng: _save_task_spec_edits(spec_id, edits, storage_root=str(_config().storage_root))
# Thêm:
try:
    _spec_data = json.loads(
        (Path(_config().storage_root) / "task_specs" / f"{spec_id}.json")
        .read_text(encoding="utf-8")
    )
    if _spec_data.get("repo"):
        _spawn_task_executor(spec_id)
except Exception:
    pass  # best-effort; redirect still happens
```

Và sửa redirect sau approve để đưa về `/task-exec`:

```python
redirect = f"/task-exec?id={urllib.parse.quote(spec_id)}" if spec_id else "/"
```

- [ ] **Step 4: Chạy webui tests**

```
python -m pytest tests/unit/test_task_spec_webui.py tests/unit/test_webui_task_spec.py -x -q
```
Expected: tất cả pass bao gồm 2 test mới.

- [ ] **Step 5: Chạy full suite**

```
python -m pytest tests/ -x -q 2>&1 | tail -5
```
Expected: tất cả pass.

- [ ] **Step 6: Commit**

```bash
git add src/ai_dev_system/webui.py tests/unit/test_task_spec_webui.py
git commit -m "feat: wire task-exec flow in webui — spawn executor on approve, /task-exec progress page"
```

---

## Self-Review

**Spec coverage:**
- ✅ Reuse execution engine: `run_execution()` được gọi trực tiếp
- ✅ Tạo git branch trước khi execute: `_git_checkout_branch()` trong executor
- ✅ RepoBranchAgent chạy claude với full tools (không có `--disallowedTools`)
- ✅ Progress log realtime trong webui
- ✅ Git diff hiển thị khi done (từ EXECUTION_LOG artifact)
- ✅ Accept (giữ branch) / Reject (xóa branch) buttons

**Placeholder scan:** Không có TBD hay TODO.

**Type consistency:** `AgentResult`, `PromotedOutput` từ `agents/base.py` dùng nhất quán. `run_execution()` signature khớp với runner.py.

**Potential issues:**
- `run_execution()` đã đủ cần `current_artifacts` update — executor update qua `_create_run_row` với `current_artifacts='{}'`; materializer sẽ update nó
- Cần kiểm tra `runs` table có cột `current_artifacts` với default `'{}'` không — có, theo schema
- `task_runs` cần `task_graph_artifact_id` FK → artifact_id vừa tạo — materializer set nó
