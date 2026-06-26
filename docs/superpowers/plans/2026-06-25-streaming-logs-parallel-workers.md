# Streaming Logs + Parallel Workers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stream claude's NDJSON output to the exec log in real-time, and run multiple task workers in parallel.

**Architecture:** `RepoBranchAgent` switches from `subprocess.run(capture_output=True)` (blocking, silent) to `subprocess.Popen` with line-by-line reading. A `live_log_path` on the agent instance lets `single_task_executor.py` wire its exec log directly. In `runner.py`, a `max_parallel_workers` loop spawns N `worker_loop` threads; SQLite WAL mode (already on, busy_timeout=5000) handles the concurrent writes safely.

**Tech Stack:** Python threading, subprocess.Popen, sqlite3 WAL, NDJSON parsing

## Global Constraints

- Python 3.12, no new dependencies
- SQLite WAL mode already enabled — no schema changes needed
- `Agent` Protocol in `base.py` must not change signature (worker.py calls `agent.run(...)`)
- All new behaviour must be covered by new or updated unit tests
- Tests use `StubAgent` pattern (`tests/integration/conftest.py`) for subprocess-free testing

---

### Task 1: Streaming NDJSON logs in RepoBranchAgent

**Files:**
- Modify: `src/ai_dev_system/agents/repo_branch_agent.py`
- Modify: `src/ai_dev_system/task_graph/single_task_executor.py` (line ~267)
- Create: `tests/unit/test_repo_branch_agent_streaming.py`

**Interfaces:**
- Produces: `RepoBranchAgent(live_log_path=Path(...))` — optional kwarg, wire in single_task_executor

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_repo_branch_agent_streaming.py
from __future__ import annotations
import json
import subprocess
import threading
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from ai_dev_system.agents.repo_branch_agent import RepoBranchAgent, _parse_ndjson_event


def test_parse_ndjson_event_tool_use():
    line = json.dumps({"type": "tool_use", "name": "Read", "input": {"file_path": "src/foo.py"}})
    result = _parse_ndjson_event(line)
    assert result is not None
    assert "Read" in result
    assert "src/foo.py" in result


def test_parse_ndjson_event_bash():
    line = json.dumps({"type": "tool_use", "name": "Bash", "input": {"command": "pytest tests/"}})
    result = _parse_ndjson_event(line)
    assert result is not None
    assert "Bash" in result
    assert "pytest" in result


def test_parse_ndjson_event_result():
    line = json.dumps({"type": "result", "subtype": "success", "result": "Done!", "total_cost_usd": 0.05})
    result = _parse_ndjson_event(line)
    assert result is not None
    assert "done" in result.lower() or "success" in result.lower()


def test_parse_ndjson_event_unknown_returns_none():
    line = json.dumps({"type": "system", "subtype": "init"})
    assert _parse_ndjson_event(line) is None


def test_parse_ndjson_event_invalid_json_returns_none():
    assert _parse_ndjson_event("not json {") is None


def _make_fake_popen(stdout_lines: list[str], returncode: int = 0):
    """Build a fake Popen that yields lines from a list."""
    class FakePopen:
        def __init__(self):
            self.stdout = iter(stdout_lines)
            self.stderr = iter([])
            self.returncode = returncode

        def wait(self, timeout=None):
            self.returncode = returncode

    return FakePopen()


def test_streaming_writes_to_live_log(tmp_path):
    """When live_log_path is set, parsed events are written there."""
    log_file = tmp_path / "exec.log"
    agent = RepoBranchAgent(
        repo_path=str(tmp_path),
        branch_name="ai-dev/task-test",
        base_branch="master",
        live_log_path=log_file,
    )

    ndjson_lines = [
        json.dumps({"type": "tool_use", "name": "Read", "input": {"file_path": "src/x.py"}}) + "\n",
        json.dumps({"type": "result", "subtype": "success", "result": "All done"}) + "\n",
    ]
    fake_proc = _make_fake_popen(ndjson_lines, returncode=0)

    # Patch Popen and git diff
    with patch("subprocess.Popen", return_value=fake_proc), \
         patch("ai_dev_system.agents.repo_branch_agent._git") as mock_git:
        mock_git.return_value = MagicMock(stdout="", returncode=0)
        out_dir = tmp_path / "output"
        out_dir.mkdir()
        agent.run("TASK-1", str(out_dir))

    log_content = log_file.read_text(encoding="utf-8")
    assert "Read" in log_content
    assert "src/x.py" in log_content


def test_no_live_log_path_does_not_crash(tmp_path):
    """live_log_path=None (default) — agent runs normally without writing a log."""
    agent = RepoBranchAgent(
        repo_path=str(tmp_path),
        branch_name="ai-dev/task-test",
        base_branch="master",
    )
    ndjson_lines = [
        json.dumps({"type": "result", "subtype": "success", "result": "ok"}) + "\n",
    ]
    fake_proc = _make_fake_popen(ndjson_lines, returncode=0)
    with patch("subprocess.Popen", return_value=fake_proc), \
         patch("ai_dev_system.agents.repo_branch_agent._git") as mock_git:
        mock_git.return_value = MagicMock(stdout="", returncode=0)
        out_dir = tmp_path / "output"
        out_dir.mkdir()
        result = agent.run("TASK-1", str(out_dir))
    assert result.success


def test_nonzero_returncode_returns_error(tmp_path):
    agent = RepoBranchAgent(
        repo_path=str(tmp_path),
        branch_name="ai-dev/task-test",
        base_branch="master",
    )
    stderr_lines = ["error: something went wrong\n"]
    class FakePopen:
        stdout = iter([json.dumps({"type": "result", "subtype": "error", "result": "fail"}) + "\n"])
        stderr = iter(stderr_lines)
        returncode = 1
        def wait(self, timeout=None):
            self.returncode = 1

    with patch("subprocess.Popen", return_value=FakePopen()), \
         patch("ai_dev_system.agents.repo_branch_agent._git") as mock_git:
        mock_git.return_value = MagicMock(stdout="", returncode=0)
        out_dir = tmp_path / "output"
        out_dir.mkdir()
        result = agent.run("TASK-1", str(out_dir))
    assert not result.success
    assert "exit" in result.error.lower() or "1" in result.error
```

- [ ] **Step 2: Run tests to confirm they fail**

```
pytest tests/unit/test_repo_branch_agent_streaming.py -v
```
Expected: FAIL (ImportError: `_parse_ndjson_event` not defined, `live_log_path` param missing)

- [ ] **Step 3: Implement streaming in repo_branch_agent.py**

Replace the entire file with this implementation:

```python
"""Agent that runs claude -p with full tools on a git branch of the target repo.

Writes diff.txt, summary.txt, and claude_stderr.txt to output_path after execution.
The agent's output_path is the directory the engine will promote as EXECUTION_LOG.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from ai_dev_system.agents.base import AgentResult
from ai_dev_system.llm_factory import ClaudeCodeLLMClient
from ai_dev_system.task_graph.facets import SPEC_FACET_KEYS

_EXEC_FLAGS = [
    "--output-format", "json",
    "--permission-mode", "bypassPermissions",
    "--max-turns", "30",
]


def _parse_ndjson_event(line: str) -> Optional[str]:
    """Return a human-readable log message for a NDJSON event, or None to skip."""
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None

    event_type = obj.get("type", "")

    if event_type == "tool_use":
        name = obj.get("name", "?")
        inp = obj.get("input") or {}
        detail = _summarize_tool_input(name, inp)
        return f"[tool] {name}{detail}"

    if event_type == "result":
        subtype = obj.get("subtype", "")
        result_text = (obj.get("result") or "")[:100]
        cost = obj.get("total_cost_usd")
        cost_str = f" (${cost:.4f})" if cost else ""
        return f"[done] {subtype}: {result_text}{cost_str}"

    return None


def _summarize_tool_input(name: str, inp: dict) -> str:
    if name in ("Read", "Write", "Edit"):
        fp = inp.get("file_path", "")
        return f": {fp}" if fp else ""
    if name == "Bash":
        cmd = (inp.get("command") or "")[:80]
        return f": {cmd}" if cmd else ""
    if name in ("Glob", "Grep"):
        pat = inp.get("pattern", "")
        return f": {pat}" if pat else ""
    return ""


def _append_log(log_path: Path, msg: str) -> None:
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        f.flush()


def _build_execution_prompt(context: dict) -> str:
    facets = context.get("facets") or {}
    filled_lines: list[str] = []
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


def _extract_summary(stdout: str, returncode: int) -> str:
    """Pull the result text from NDJSON output."""
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict) and obj.get("type") == "result":
                result_text = obj.get("result") or ""
                return result_text[:500] if result_text else f"claude exit={returncode}"
        except json.JSONDecodeError:
            continue
    return f"claude exit={returncode}, stdout={len(stdout)}B"


class RepoBranchAgent:
    """Implements the Agent protocol. Runs claude -p with full tools on a git branch."""

    def __init__(
        self,
        repo_path: str,
        branch_name: str,
        base_branch: str,
        live_log_path: Optional[Path] = None,
    ) -> None:
        self.repo_path = repo_path
        self.branch_name = branch_name
        self.base_branch = base_branch
        self.live_log_path = live_log_path

    def run(
        self,
        task_id: str,
        output_path: str,
        promoted_outputs=(),
        context: Optional[dict] = None,
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

        if self.live_log_path:
            _append_log(self.live_log_path, f"Claude bắt đầu task {task_id}…")

        proc = subprocess.Popen(
            cmd, cwd=self.repo_path,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
        )

        all_stdout: list[str] = []
        stderr_lines: list[str] = []

        def _drain_stderr():
            for line in proc.stderr:
                stderr_lines.append(line)

        stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
        stderr_thread.start()

        try:
            for line in proc.stdout:
                all_stdout.append(line)
                if self.live_log_path:
                    msg = _parse_ndjson_event(line.strip())
                    if msg:
                        _append_log(self.live_log_path, msg)
            proc.wait(timeout=int(timeout_s))
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            return AgentResult(
                output_path=output_path,
                error=f"claude timed out after {timeout_s}s",
            )
        finally:
            stderr_thread.join(timeout=5)

        full_stdout = "".join(all_stdout)
        full_stderr = "".join(stderr_lines)

        diff_proc = _git(["diff", f"{self.base_branch}..HEAD"], self.repo_path)
        diff_text = diff_proc.stdout or "(no diff)"

        summary = _extract_summary(full_stdout, proc.returncode)

        Path(output_path, "diff.txt").write_text(diff_text, encoding="utf-8")
        Path(output_path, "summary.txt").write_text(summary, encoding="utf-8")
        Path(output_path, "claude_stderr.txt").write_text(full_stderr, encoding="utf-8")

        if proc.returncode != 0:
            return AgentResult(
                output_path=output_path,
                error=f"claude CLI exited {proc.returncode}. stderr: {full_stderr[:300]}",
            )

        return AgentResult(output_path=output_path)
```

- [ ] **Step 4: Wire live_log_path in single_task_executor.py**

In [single_task_executor.py](src/ai_dev_system/task_graph/single_task_executor.py), find the `RepoBranchAgent(...)` instantiation (around line 267) and add `live_log_path=log_path`:

```python
    agent = RepoBranchAgent(
        repo_path=repo_path,
        branch_name=branch_name,
        base_branch=base_branch,
        live_log_path=log_path,  # stream claude output to exec log
    )
```

- [ ] **Step 5: Run tests to confirm they pass**

```
pytest tests/unit/test_repo_branch_agent_streaming.py -v
```
Expected: 8 tests PASS

- [ ] **Step 6: Run full suite to check regressions**

```
pytest tests/ -x -q
```
Expected: all pass (same count as before)

- [ ] **Step 7: Commit**

```bash
git add src/ai_dev_system/agents/repo_branch_agent.py \
        src/ai_dev_system/task_graph/single_task_executor.py \
        tests/unit/test_repo_branch_agent_streaming.py
git commit -m "feat: stream claude NDJSON output to exec log in real-time"
```

---

### Task 2: Parallel workers in runner.py

**Files:**
- Modify: `src/ai_dev_system/config.py` (add `max_parallel_workers`)
- Modify: `src/ai_dev_system/engine/runner.py`
- Create: `tests/unit/test_parallel_workers.py`

**Interfaces:**
- Consumes: `Config.max_parallel_workers: int = 4`
- Produces: `run_execution(...)` spawns `config.max_parallel_workers` worker threads

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_parallel_workers.py
"""Verify that runner.py spawns multiple workers and they execute tasks concurrently."""
from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path

import pytest
from ai_dev_system.agents.base import AgentResult
from ai_dev_system.config import Config
from ai_dev_system.db.connection import get_connection
from ai_dev_system.engine.runner import run_execution


def _make_db(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'test.db'}"


def _bootstrap_run(conn, run_id: str) -> None:
    """Insert a minimal run row in RUNNING_EXECUTION state."""
    conn.execute(
        """
        INSERT INTO runs (run_id, project_id, status, title, current_artifacts, metadata)
        VALUES (?, 'test-proj', 'RUNNING_EXECUTION', 'Parallel test', '{}', '{}')
        """,
        (run_id,),
    )
    conn.commit()


def _create_task_graph_artifact(conn, run_id: str, tasks: list[dict], storage_root: str) -> str:
    import hashlib, json
    artifact_id = uuid.uuid4().hex
    from pathlib import Path as P
    artifact_dir = P(storage_root) / "task_execs" / run_id / "task_graph"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    content = json.dumps({"tasks": tasks}, indent=2, ensure_ascii=False)
    (artifact_dir / "task_graph.json").write_text(content, encoding="utf-8")
    checksum = "sha256:" + hashlib.sha256(content.encode()).hexdigest()
    conn.execute(
        """
        INSERT INTO artifacts
            (artifact_id, run_id, artifact_type, version, status, created_by,
             input_artifact_ids, content_ref, content_checksum, content_size, annotations)
        VALUES (?, ?, 'TASK_GRAPH_APPROVED', 1, 'ACTIVE', 'system',
                '[]', ?, ?, ?, '{}')
        """,
        (artifact_id, run_id, str(artifact_dir), checksum, len(content)),
    )
    conn.commit()
    return artifact_id


class SlowStubAgent:
    """Agent that records concurrency: multiple calls overlap if workers are parallel."""

    def __init__(self, sleep_s: float = 0.2) -> None:
        self.sleep_s = sleep_s
        self.active = 0
        self.max_concurrent = 0
        self._lock = threading.Lock()
        self.call_times: list[float] = []

    def run(self, task_id, output_path, promoted_outputs=(), context=None,
            timeout_s=60.0, file_rules=()):
        with self._lock:
            self.active += 1
            self.max_concurrent = max(self.max_concurrent, self.active)
            self.call_times.append(time.monotonic())
        Path(output_path).mkdir(parents=True, exist_ok=True)
        time.sleep(self.sleep_s)
        with self._lock:
            self.active -= 1
        return AgentResult(output_path=output_path)


def _three_independent_tasks() -> list[dict]:
    return [
        {
            "id": f"TASK-{i}",
            "execution_type": "atomic",
            "agent_type": "RepoBranchAgent",
            "phase": "implementation",
            "type": "coding",
            "objective": f"Task {i}",
            "description": "",
            "done_definition": "done",
            "verification_steps": [],
            "required_inputs": [],
            "expected_outputs": [],
            "deps": [],
            "facets": {},
        }
        for i in range(1, 4)
    ]


def test_parallel_workers_run_tasks_concurrently(tmp_path):
    """With max_parallel_workers=3 and 3 independent tasks, all run at the same time."""
    db_url = _make_db(tmp_path)
    storage = str(tmp_path / "storage")

    from ai_dev_system.db.schema import create_tables
    conn = get_connection(db_url)
    create_tables(conn)

    run_id = uuid.uuid4().hex
    _bootstrap_run(conn, run_id)
    artifact_id = _create_task_graph_artifact(conn, run_id, _three_independent_tasks(), storage)
    conn.close()

    agent = SlowStubAgent(sleep_s=0.3)
    cfg = Config(storage_root=storage, database_url=db_url,
                 poll_interval_s=0.1, heartbeat_timeout_s=30.0,
                 max_parallel_workers=3)

    start = time.monotonic()
    result = run_execution(run_id, artifact_id, cfg, agent, poll_interval_s=0.1)
    elapsed = time.monotonic() - start

    assert result.status == "COMPLETED"
    # 3 tasks × 0.3s sequential = 0.9s; parallel should be < 0.7s
    assert elapsed < 0.7, f"Expected parallel execution < 0.7s, got {elapsed:.2f}s"
    # At least 2 tasks ran concurrently
    assert agent.max_concurrent >= 2


def test_config_max_parallel_workers_default():
    """Config.from_env() defaults to max_parallel_workers=4."""
    cfg = Config(storage_root="/tmp", database_url="sqlite:///tmp/x.db")
    assert cfg.max_parallel_workers == 4


def test_single_worker_runs_tasks_sequentially(tmp_path):
    """max_parallel_workers=1 runs tasks one at a time."""
    db_url = _make_db(tmp_path)
    storage = str(tmp_path / "storage")

    from ai_dev_system.db.schema import create_tables
    conn = get_connection(db_url)
    create_tables(conn)

    run_id = uuid.uuid4().hex
    _bootstrap_run(conn, run_id)
    artifact_id = _create_task_graph_artifact(conn, run_id, _three_independent_tasks(), storage)
    conn.close()

    agent = SlowStubAgent(sleep_s=0.1)
    cfg = Config(storage_root=storage, database_url=db_url,
                 poll_interval_s=0.05, heartbeat_timeout_s=30.0,
                 max_parallel_workers=1)

    result = run_execution(run_id, artifact_id, cfg, agent, poll_interval_s=0.05)
    assert result.status == "COMPLETED"
    # With 1 worker, max_concurrent must be 1
    assert agent.max_concurrent == 1
```

- [ ] **Step 2: Run tests to confirm they fail**

```
pytest tests/unit/test_parallel_workers.py -v
```
Expected: FAIL (`Config` has no `max_parallel_workers`, runner spawns only 1 worker)

- [ ] **Step 3: Add max_parallel_workers to Config**

In [config.py](src/ai_dev_system/config.py), add the field to the dataclass:

```python
@dataclass
class Config:
    storage_root: str
    database_url: str
    poll_interval_s: float = 5.0
    heartbeat_interval_s: float = 30.0
    heartbeat_timeout_s: float = 120.0
    task_timeout_s: float = 3600.0
    max_parallel_workers: int = 4
    retry_policy: dict = field(default_factory=_default_retry_policy)
```

- [ ] **Step 4: Spawn N workers in runner.py**

In [runner.py](src/ai_dev_system/engine/runner.py), replace the single `worker_thread` block (lines ~60-80) with:

```python
    stop_event = threading.Event()

    n_workers = getattr(effective_config, "max_parallel_workers", 1)
    worker_threads = []
    for i in range(n_workers):
        t = threading.Thread(
            target=worker_loop,
            args=(run_id, effective_config, agent, stop_event, conn_factory),
            name=f"worker-{run_id[:8]}-{i}",
            daemon=True,
        )
        worker_threads.append(t)

    background_thread = threading.Thread(
        target=background_loop,
        args=(run_id, effective_config, stop_event, conn_factory),
        name=f"bg-{run_id[:8]}",
        daemon=True,
    )

    for t in worker_threads:
        t.start()
    background_thread.start()
    logger.info("Execution runner started for run %s (%d workers)", run_id, n_workers)

    final_status = _wait_for_terminal_state(run_id, effective_config, conn_factory)

    stop_event.set()
    for t in worker_threads:
        t.join(timeout=30)
    background_thread.join(timeout=10)

    stale = [t for t in worker_threads if t.is_alive()]
    if stale:
        logger.warning("%d worker thread(s) did not stop cleanly for run %s", len(stale), run_id)

    logger.info("Run %s finished with status %s", run_id, final_status)
    return ExecutionResult(run_id=run_id, status=final_status)
```

- [ ] **Step 5: Run tests to confirm they pass**

```
pytest tests/unit/test_parallel_workers.py -v
```
Expected: 3 tests PASS

- [ ] **Step 6: Run full suite to check regressions**

```
pytest tests/ -x -q
```
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add src/ai_dev_system/config.py \
        src/ai_dev_system/engine/runner.py \
        tests/unit/test_parallel_workers.py
git commit -m "feat: parallel worker threads in execution runner (max_parallel_workers=4)"
```
