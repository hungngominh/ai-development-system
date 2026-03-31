# Minimal Worker Loop — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a minimal, runnable execution engine that can pick up a task, execute it, promote output to artifact, and resolve downstream dependencies — end-to-end in PostgreSQL + local filesystem.

**Architecture:** 4 focused modules — `db/repos` (DB queries), `storage` (file ops + promotion), `engine` (worker loop + resolver), `agents/stub` (test double). Each layer talks to the next via plain Python objects, no framework magic. TDD throughout: write test, see it fail, implement minimal code to pass.

**Tech Stack:** Python 3.11+, psycopg (v3, sync), pytest, PostgreSQL 15+

**Scope của plan này (Phase 1):**
- Storage layer: path builders, checksum, `promote_output()` (full 7-step protocol)
- DB repo layer: task_runs, artifacts, runs, events
- Engine: Dependency Resolver (Job A), Worker Pickup (Job B), Task Executor (Job C)
- Worker Loop: orchestrate B+C, heartbeat, backoff
- Integration test: 1 task end-to-end (READY → RUNNING → SUCCESS + artifact ACTIVE)

**KHÔNG trong scope Phase 1:** Dead Worker Recovery (Job D), Failure Handler retry logic, Orphan Cleanup Job, real agent execution (dùng stub).

---

## File Structure

```
src/
  ai_dev_system/
    __init__.py
    config.py                    # Env-based config: storage_root, db_url
    db/
      __init__.py
      connection.py              # psycopg connection pool (sync)
      repos/
        __init__.py
        task_runs.py             # TaskRunRepo: pickup, mark_running, mark_success, mark_failed
        artifacts.py             # ArtifactRepo: insert, supersede_active, get_by_ref
        runs.py                  # RunRepo: get_current_artifacts, update_current_artifacts
        events.py                # EventRepo: insert
        version_locks.py         # VersionLockRepo: upsert_sentinel, lock_and_increment
    storage/
      __init__.py
      paths.py                   # build_artifact_path, build_task_output_path, build_temp_path
      checksum.py                # checksum_file, checksum_folder, checksum_artifact
      stability.py               # wait_until_stable()
      promote.py                 # promote_output() — full 7-step protocol
    engine/
      __init__.py
      resolver.py                # Job A: resolve_dependencies()
      worker.py                  # Job B+C: pickup_and_execute()
      loop.py                    # Main worker loop with backoff
    agents/
      __init__.py
      stub.py                    # StubAgent: writes deterministic output files for tests
      base.py                    # AgentResult dataclass
tests/
  conftest.py                    # DB fixtures, tmp storage_root, test run/task setup helpers
  unit/
    test_paths.py
    test_checksum.py
    test_stability.py
  integration/
    test_promote.py              # promote_output() end-to-end
    test_resolver.py             # PENDING → READY transition
    test_worker_loop.py          # Full: READY → RUNNING → SUCCESS + artifact ACTIVE
```

---

## Task 1: Project Setup

**Files:**
- Create: `src/ai_dev_system/__init__.py`
- Create: `src/ai_dev_system/config.py`
- Create: `pyproject.toml`
- Create: `tests/conftest.py`

- [ ] **Step 1: Write failing test — config loads from env**

```python
# tests/unit/test_config.py
import os
import pytest
from ai_dev_system.config import Config

def test_config_reads_from_env(monkeypatch):
    monkeypatch.setenv("STORAGE_ROOT", "/tmp/test-data")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
    cfg = Config.from_env()
    assert cfg.storage_root == "/tmp/test-data"
    assert cfg.database_url == "postgresql://localhost/test"

def test_config_raises_if_missing(monkeypatch):
    monkeypatch.delenv("STORAGE_ROOT", raising=False)
    with pytest.raises(ValueError, match="STORAGE_ROOT"):
        Config.from_env()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd e:/Work/ai-development-system
pytest tests/unit/test_config.py -v
```
Expected: `ModuleNotFoundError: No module named 'ai_dev_system'`

- [ ] **Step 3: Create pyproject.toml**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.backends.legacy:build"

[project]
name = "ai-dev-system"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "psycopg[binary]>=3.1",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-mock>=3.12"]

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 4: Implement config.py**

```python
# src/ai_dev_system/config.py
import os
from dataclasses import dataclass

@dataclass
class Config:
    storage_root: str
    database_url: str

    @classmethod
    def from_env(cls) -> "Config":
        storage_root = os.environ.get("STORAGE_ROOT")
        if not storage_root:
            raise ValueError("STORAGE_ROOT environment variable is required")
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            raise ValueError("DATABASE_URL environment variable is required")
        return cls(storage_root=storage_root, database_url=database_url)
```

- [ ] **Step 5: Install package and run test**

```bash
pip install -e ".[dev]"
pytest tests/unit/test_config.py -v
```
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/ tests/unit/test_config.py
git commit -m "feat: project scaffold, Config from env"
```

---

## Task 2: Storage — Path Builders

**Files:**
- Create: `src/ai_dev_system/storage/paths.py`
- Create: `tests/unit/test_paths.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_paths.py
from ai_dev_system.storage.paths import (
    build_artifact_path,
    build_task_output_path,
    build_temp_path,
)

def test_artifact_path_format():
    path = build_artifact_path("/data", "abc-123", "SPEC_BUNDLE", 2)
    assert path == "/data/runs/abc-123/artifacts/spec_bundle/v2"

def test_artifact_path_type_lowercased():
    path = build_artifact_path("/data", "r1", "TASK_GRAPH_APPROVED", 1)
    assert "task_graph_approved" in path

def test_task_output_path():
    path = build_task_output_path("/data", "r1", "TASK-3", 2)
    assert path == "/data/runs/r1/tasks/TASK-3/attempt-2"

def test_temp_path():
    path = build_temp_path("/data", "r1", "TASK-3", 1)
    assert path == "/data/tmp/runs/r1/tasks/TASK-3/attempt-1"

def test_artifact_type_to_key_maps_correctly():
    from ai_dev_system.storage.paths import ARTIFACT_TYPE_TO_KEY
    assert ARTIFACT_TYPE_TO_KEY["SPEC_BUNDLE"] == "spec_bundle_id"
    assert ARTIFACT_TYPE_TO_KEY["EXECUTION_LOG"] is None
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/unit/test_paths.py -v
```
Expected: FAIL (ImportError)

- [ ] **Step 3: Implement paths.py**

```python
# src/ai_dev_system/storage/paths.py
import os

ARTIFACT_TYPE_TO_KEY = {
    "INITIAL_BRIEF":        "initial_brief_id",
    "DEBATE_REPORT":        "debate_report_id",
    "DECISION_LOG":         "decision_log_id",
    "APPROVED_ANSWERS":     "approved_answers_id",
    "SPEC_BUNDLE":          "spec_bundle_id",
    "TASK_GRAPH_GENERATED": "task_graph_gen_id",
    "TASK_GRAPH_APPROVED":  "task_graph_approved_id",
    "EXECUTION_LOG":        None,
}

def build_artifact_path(storage_root: str, run_id: str, artifact_type: str, version: int) -> str:
    type_slug = artifact_type.lower()
    return os.path.join(storage_root, "runs", str(run_id), "artifacts", type_slug, f"v{version}")

def build_task_output_path(storage_root: str, run_id: str, task_id: str, attempt_number: int) -> str:
    return os.path.join(storage_root, "runs", str(run_id), "tasks", task_id, f"attempt-{attempt_number}")

def build_temp_path(storage_root: str, run_id: str, task_id: str, attempt_number: int) -> str:
    return os.path.join(storage_root, "tmp", "runs", str(run_id), "tasks", task_id, f"attempt-{attempt_number}")
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_paths.py -v
```
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/storage/ tests/unit/test_paths.py
git commit -m "feat: storage path builders"
```

---

## Task 3: Storage — Checksum

**Files:**
- Create: `src/ai_dev_system/storage/checksum.py`
- Create: `tests/unit/test_checksum.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_checksum.py
import os
import hashlib
import pytest
from pathlib import Path
from ai_dev_system.storage.checksum import checksum_file, checksum_folder, checksum_artifact

def test_checksum_file(tmp_path):
    f = tmp_path / "test.txt"
    f.write_bytes(b"hello world")
    result = checksum_file(str(f))
    expected = hashlib.sha256(b"hello world").hexdigest()
    assert result == expected

def test_checksum_folder_deterministic(tmp_path):
    (tmp_path / "a.txt").write_text("aaa")
    (tmp_path / "b.txt").write_text("bbb")
    c1 = checksum_folder(str(tmp_path))
    c2 = checksum_folder(str(tmp_path))
    assert c1 == c2

def test_checksum_folder_changes_when_file_changes(tmp_path):
    (tmp_path / "a.txt").write_text("aaa")
    c1 = checksum_folder(str(tmp_path))
    (tmp_path / "a.txt").write_text("bbb")
    c2 = checksum_folder(str(tmp_path))
    assert c1 != c2

def test_checksum_folder_order_independent(tmp_path):
    """Same files = same checksum regardless of creation order."""
    d1 = tmp_path / "d1"
    d2 = tmp_path / "d2"
    d1.mkdir(); d2.mkdir()
    (d1 / "x.txt").write_text("x"); (d1 / "y.txt").write_text("y")
    (d2 / "y.txt").write_text("y"); (d2 / "x.txt").write_text("x")
    assert checksum_folder(str(d1)) == checksum_folder(str(d2))

def test_checksum_artifact_file(tmp_path):
    f = tmp_path / "f.txt"
    f.write_bytes(b"data")
    checksum, size = checksum_artifact(str(f))
    assert checksum == hashlib.sha256(b"data").hexdigest()
    assert size == 4

def test_checksum_artifact_folder(tmp_path):
    (tmp_path / "a.txt").write_bytes(b"abc")
    checksum, size = checksum_artifact(str(tmp_path))
    assert isinstance(checksum, str) and len(checksum) == 64
    assert size == 3
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/unit/test_checksum.py -v
```
Expected: FAIL (ImportError)

- [ ] **Step 3: Implement checksum.py**

```python
# src/ai_dev_system/storage/checksum.py
import hashlib
import os

def checksum_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def checksum_folder(folder_path: str) -> str:
    entries = []
    for root, dirs, files in os.walk(folder_path):
        dirs.sort()
        for filename in sorted(files):
            abs_path = os.path.join(root, filename)
            rel_path = os.path.relpath(abs_path, folder_path)
            entries.append(f"{rel_path}:{checksum_file(abs_path)}")
    combined = "\n".join(entries)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()

def checksum_artifact(content_ref: str) -> tuple[str, int]:
    if os.path.isfile(content_ref):
        return checksum_file(content_ref), os.path.getsize(content_ref)
    checksum = checksum_folder(content_ref)
    size = sum(
        os.path.getsize(os.path.join(root, f))
        for root, _, files in os.walk(content_ref)
        for f in files
    )
    return checksum, size
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_checksum.py -v
```
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/storage/checksum.py tests/unit/test_checksum.py
git commit -m "feat: deterministic folder checksum"
```

---

## Task 4: Storage — Stability Check

**Files:**
- Create: `src/ai_dev_system/storage/stability.py`
- Create: `tests/unit/test_stability.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_stability.py
import os
import time
import threading
from pathlib import Path
from ai_dev_system.storage.stability import wait_until_stable

def test_stable_folder_returns_quickly(tmp_path):
    (tmp_path / "done.txt").write_text("data")
    start = time.time()
    wait_until_stable(str(tmp_path), poll_interval_ms=50, stable_duration_ms=150)
    assert time.time() - start < 2.0

def test_unstable_folder_waits(tmp_path):
    (tmp_path / "file.txt").write_text("v1")
    writes_done = threading.Event()

    def writer():
        time.sleep(0.1)
        (tmp_path / "file.txt").write_text("v2")
        time.sleep(0.1)
        (tmp_path / "file.txt").write_text("v3")
        writes_done.set()

    t = threading.Thread(target=writer)
    t.start()
    wait_until_stable(str(tmp_path), poll_interval_ms=50, stable_duration_ms=200)
    assert writes_done.is_set()
    t.join()
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/unit/test_stability.py -v
```
Expected: FAIL (ImportError)

- [ ] **Step 3: Implement stability.py**

```python
# src/ai_dev_system/storage/stability.py
import os
import time

def _snapshot(folder_path: str) -> tuple[int, int, float]:
    """Returns (total_size, file_count, max_mtime)."""
    total_size = file_count = 0
    max_mtime = 0.0
    for root, _, files in os.walk(folder_path):
        for f in files:
            abs_path = os.path.join(root, f)
            stat = os.stat(abs_path)
            total_size += stat.st_size
            file_count += 1
            max_mtime = max(max_mtime, stat.st_mtime)
    return total_size, file_count, max_mtime

def wait_until_stable(
    folder_path: str,
    poll_interval_ms: int = 100,
    stable_duration_ms: int = 200,
    timeout_s: float = 60.0,
) -> None:
    poll_s = poll_interval_ms / 1000
    stable_s = stable_duration_ms / 1000
    deadline = time.time() + timeout_s
    stable_since = None
    last_snap = None

    while time.time() < deadline:
        snap = _snapshot(folder_path)
        if snap == last_snap:
            if stable_since is None:
                stable_since = time.time()
            elif time.time() - stable_since >= stable_s:
                return
        else:
            last_snap = snap
            stable_since = None
        time.sleep(poll_s)

    raise TimeoutError(f"Folder {folder_path!r} did not stabilize within {timeout_s}s")
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_stability.py -v
```
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/storage/stability.py tests/unit/test_stability.py
git commit -m "feat: wait_until_stable for output folders"
```

---

## Task 5: DB Layer — Connection + Repos

**Files:**
- Create: `src/ai_dev_system/db/connection.py`
- Create: `src/ai_dev_system/db/repos/task_runs.py`
- Create: `src/ai_dev_system/db/repos/artifacts.py`
- Create: `src/ai_dev_system/db/repos/runs.py`
- Create: `src/ai_dev_system/db/repos/events.py`
- Create: `src/ai_dev_system/db/repos/version_locks.py`
- Create: `tests/conftest.py`

**Prerequisites:** PostgreSQL phải chạy với schema từ `docs/schema/control-layer-schema.sql` applied. Set `DATABASE_URL` env.

- [ ] **Step 1: Create conftest.py với DB fixtures**

```python
# tests/conftest.py
import os
import uuid
import pytest
import psycopg
from ai_dev_system.config import Config
from ai_dev_system.db.connection import get_connection

@pytest.fixture(scope="session")
def config():
    os.environ.setdefault("STORAGE_ROOT", "/tmp/ai-dev-test")
    os.environ.setdefault("DATABASE_URL", "postgresql://localhost/ai_dev_test")
    return Config.from_env()

@pytest.fixture
def conn(config):
    """One connection per test, autocommit=False. Everything is rolled back after test."""
    c = psycopg.connect(config.database_url, autocommit=False, row_factory=psycopg.rows.dict_row)
    c.execute("BEGIN")
    yield c
    c.execute("ROLLBACK")
    c.close()

@pytest.fixture
def test_run_id():
    return str(uuid.uuid4())

@pytest.fixture
def seed_run(conn, test_run_id):
    """Insert a minimal run row for testing."""
    conn.execute("""
        INSERT INTO runs (run_id, status, title, current_artifacts, metadata)
        VALUES (%s, 'RUNNING_PHASE_3', 'Test Run', '{}', '{}')
    """, (test_run_id,))
    return test_run_id

@pytest.fixture
def seed_task_run(conn, seed_run):
    """Insert a READY task_run row."""
    task_run_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO task_runs (
            task_run_id, run_id, task_id, attempt_number, status,
            agent_type, input_artifact_ids, resolved_dependencies,
            promoted_outputs
        ) VALUES (%s, %s, 'TASK-1', 1, 'READY', 'StubAgent', '{}', '{}', '[]')
    """, (task_run_id, seed_run))
    return task_run_id
```

- [ ] **Step 2: Implement connection.py**

```python
# src/ai_dev_system/db/connection.py
import psycopg
from contextlib import contextmanager

def get_connection(database_url: str) -> psycopg.Connection:
    return psycopg.connect(database_url, row_factory=psycopg.rows.dict_row)

@contextmanager
def transaction(conn: psycopg.Connection):
    with conn.transaction():
        yield conn
```

- [ ] **Step 3: Write failing test for TaskRunRepo.pickup**

```python
# tests/integration/test_task_run_repo.py
import pytest
from ai_dev_system.db.repos.task_runs import TaskRunRepo

def test_pickup_returns_ready_task(conn, seed_run, seed_task_run):
    repo = TaskRunRepo(conn)
    task = repo.pickup(run_id=seed_run, worker_id="worker-1")
    assert task is not None
    assert task["task_id"] == "TASK-1"
    assert task["status"] == "RUNNING"

def test_pickup_returns_none_when_no_ready_tasks(conn, seed_run):
    repo = TaskRunRepo(conn)
    task = repo.pickup(run_id=seed_run, worker_id="worker-1")
    assert task is None

def test_pickup_is_exclusive(conn, seed_run, seed_task_run):
    repo = TaskRunRepo(conn)
    t1 = repo.pickup(run_id=seed_run, worker_id="worker-1")
    t2 = repo.pickup(run_id=seed_run, worker_id="worker-2")
    assert t1 is not None
    assert t2 is None  # already locked

def test_mark_success(conn, seed_run, seed_task_run):
    repo = TaskRunRepo(conn)
    task = repo.pickup(run_id=seed_run, worker_id="w1")
    repo.mark_success(task["task_run_id"], output_ref="/tmp/out", output_artifact_id=None)
    updated = conn.execute(
        "SELECT status FROM task_runs WHERE task_run_id = %s",
        (task["task_run_id"],)
    ).fetchone()
    assert updated["status"] == "SUCCESS"
```

- [ ] **Step 4: Implement task_runs.py**

```python
# src/ai_dev_system/db/repos/task_runs.py
import psycopg
from typing import Optional

class TaskRunRepo:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    def pickup(self, run_id: str, worker_id: str, max_concurrent: int = 4) -> Optional[dict]:
        running = self.conn.execute(
            "SELECT COUNT(*) as n FROM task_runs WHERE run_id = %s AND status = 'RUNNING'",
            (run_id,)
        ).fetchone()
        if running["n"] >= max_concurrent:
            return None

        task = self.conn.execute("""
            SELECT task_run_id, task_id, run_id, attempt_number,
                   input_artifact_ids, promoted_outputs
            FROM task_runs
            WHERE run_id = %s AND status = 'READY' AND worker_id IS NULL
            ORDER BY attempt_number ASC
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        """, (run_id,)).fetchone()
        if not task:
            return None

        self.conn.execute("""
            UPDATE task_runs
            SET status = 'RUNNING', worker_id = %s,
                locked_at = now(), heartbeat_at = now(), started_at = now()
            WHERE task_run_id = %s
        """, (worker_id, task["task_run_id"]))
        return dict(task) | {"status": "RUNNING"}

    def mark_success(self, task_run_id: str, output_ref: str, output_artifact_id: Optional[str]) -> int:
        result = self.conn.execute("""
            UPDATE task_runs
            SET status = 'SUCCESS', output_ref = %s, output_artifact_id = %s, completed_at = now()
            WHERE task_run_id = %s AND status = 'RUNNING' AND output_artifact_id IS NULL AND completed_at IS NULL
        """, (output_ref, output_artifact_id, task_run_id))
        return result.rowcount

    def mark_failed(self, task_run_id: str, error_type: str, error_detail: str) -> int:
        result = self.conn.execute("""
            UPDATE task_runs
            SET status = 'FAILED', error_type = %s, error_detail = %s, completed_at = now()
            WHERE task_run_id = %s AND status = 'RUNNING'
        """, (error_type, error_detail, task_run_id))
        return result.rowcount

    def update_heartbeat(self, task_run_id: str) -> None:
        self.conn.execute(
            "UPDATE task_runs SET heartbeat_at = now() WHERE task_run_id = %s",
            (task_run_id,)
        )

    def get_pending(self, run_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT task_run_id, task_id, resolved_dependencies FROM task_runs WHERE run_id = %s AND status = 'PENDING'",
            (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/integration/test_task_run_repo.py -v
```
Expected: PASS (4 tests)

- [ ] **Step 6: Implement remaining repos (artifacts, runs, events, version_locks)**

```python
# src/ai_dev_system/db/repos/artifacts.py
import psycopg
from typing import Optional

class ArtifactRepo:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    def supersede_active(self, run_id: str, artifact_type: str) -> None:
        self.conn.execute("""
            UPDATE artifacts SET status = 'SUPERSEDED'
            WHERE run_id = %s AND artifact_type = %s AND status = 'ACTIVE'
        """, (run_id, artifact_type))

    def insert(
        self, run_id: str, artifact_type: str, version: int,
        created_by: str, input_artifact_ids: list,
        content_ref: str, content_checksum: str, content_size: int,
    ) -> str:
        row = self.conn.execute("""
            INSERT INTO artifacts (
                run_id, artifact_type, version, status, created_by,
                input_artifact_ids, content_ref, content_checksum, content_size
            ) VALUES (%s, %s, %s, 'ACTIVE', %s, %s, %s, %s, %s)
            RETURNING artifact_id
        """, (run_id, artifact_type, version, created_by,
              input_artifact_ids, content_ref, content_checksum, content_size)).fetchone()
        return str(row["artifact_id"])
```

```python
# src/ai_dev_system/db/repos/version_locks.py
import psycopg

class VersionLockRepo:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    def lock_and_increment(self, run_id: str, artifact_type: str) -> int:
        """Upsert sentinel, lock row, increment, return next version."""
        self.conn.execute("""
            INSERT INTO artifact_version_locks (run_id, artifact_type)
            VALUES (%s, %s) ON CONFLICT DO NOTHING
        """, (run_id, artifact_type))
        row = self.conn.execute("""
            SELECT current_version FROM artifact_version_locks
            WHERE run_id = %s AND artifact_type = %s
            FOR UPDATE
        """, (run_id, artifact_type)).fetchone()
        next_version = row["current_version"] + 1
        self.conn.execute("""
            UPDATE artifact_version_locks SET current_version = %s
            WHERE run_id = %s AND artifact_type = %s
        """, (next_version, run_id, artifact_type))
        return next_version
```

```python
# src/ai_dev_system/db/repos/runs.py
import json
import psycopg

class RunRepo:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    def update_current_artifact(self, run_id: str, key: str, artifact_id: str) -> None:
        self.conn.execute("""
            UPDATE runs
            SET current_artifacts = jsonb_set(current_artifacts, %s, to_jsonb(%s::text)),
                last_activity_at = now()
            WHERE run_id = %s
        """, (f"{{{key}}}", artifact_id, run_id))
```

```python
# src/ai_dev_system/db/repos/events.py
import psycopg
from typing import Optional

class EventRepo:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    def insert(
        self, run_id: str, event_type: str, actor: str,
        task_run_id: Optional[str] = None,
        payload: Optional[dict] = None,
    ) -> None:
        self.conn.execute("""
            INSERT INTO events (run_id, task_run_id, event_type, actor, payload)
            VALUES (%s, %s, %s, %s, %s)
        """, (run_id, task_run_id, event_type, actor, psycopg.types.json.Jsonb(payload or {})))
```

- [ ] **Step 7: Commit**

```bash
git add src/ai_dev_system/db/ tests/
git commit -m "feat: DB repo layer — task_runs, artifacts, version_locks, events"
```

---

## Task 6: Storage — Promote Output (7-step protocol)

**Files:**
- Create: `src/ai_dev_system/storage/promote.py`
- Create: `tests/integration/test_promote.py`

This is the most critical piece. Every step maps directly to the 7-step protocol in `artifact-storage-contract.md`.

- [ ] **Step 1: Write failing integration test**

```python
# tests/integration/test_promote.py
import os
import uuid
import pytest
from pathlib import Path
from ai_dev_system.storage.promote import promote_output
from ai_dev_system.storage.paths import build_artifact_path

@pytest.fixture
def temp_output(tmp_path):
    """Simulate task output in temp path."""
    out = tmp_path / "agent_output"
    out.mkdir()
    (out / "result.json").write_text('{"status": "done"}')
    return str(out)

def test_promote_creates_artifact_in_db(conn, seed_run, seed_task_run, temp_output, tmp_path, config):
    """promote_output() inserts artifact record and returns artifact_id."""
    from ai_dev_system.agents.base import PromotedOutput
    task_run = {"task_run_id": seed_task_run, "run_id": seed_run, "task_id": "TASK-1",
                "attempt_number": 1, "input_artifact_ids": []}
    promoted = PromotedOutput(name="result.json", artifact_type="EXECUTION_LOG")

    config_with_tmp = config.__class__(storage_root=str(tmp_path), database_url=config.database_url)
    artifact_id = promote_output(conn, config_with_tmp, task_run, promoted, temp_output)

    assert artifact_id is not None
    row = conn.execute(
        "SELECT status, content_ref, version FROM artifacts WHERE artifact_id = %s",
        (artifact_id,)
    ).fetchone()
    assert row["status"] == "ACTIVE"
    assert row["version"] == 1
    assert os.path.exists(row["content_ref"])

def test_promote_writes_complete_marker(conn, seed_run, seed_task_run, temp_output, tmp_path, config):
    from ai_dev_system.agents.base import PromotedOutput
    task_run = {"task_run_id": seed_task_run, "run_id": seed_run, "task_id": "TASK-1",
                "attempt_number": 1, "input_artifact_ids": []}
    promoted = PromotedOutput(name="result.json", artifact_type="EXECUTION_LOG")
    config_with_tmp = config.__class__(storage_root=str(tmp_path), database_url=config.database_url)

    artifact_id = promote_output(conn, config_with_tmp, task_run, promoted, temp_output)
    row = conn.execute("SELECT content_ref FROM artifacts WHERE artifact_id = %s", (artifact_id,)).fetchone()
    assert os.path.exists(os.path.join(row["content_ref"], "_complete.marker"))

def test_promote_increments_version_on_second_call(conn, seed_run, tmp_path, config):
    """Two promotions of same artifact_type on same run → versions 1 and 2."""
    from ai_dev_system.agents.base import PromotedOutput
    import uuid

    def make_task_run(conn, run_id, task_id):
        tid = str(uuid.uuid4())
        conn.execute("""
            INSERT INTO task_runs (task_run_id, run_id, task_id, attempt_number, status,
                agent_type, input_artifact_ids, resolved_dependencies, promoted_outputs)
            VALUES (%s, %s, %s, 1, 'RUNNING', 'StubAgent', '{}', '{}', '[]')
        """, (tid, run_id, task_id))
        return {"task_run_id": tid, "run_id": run_id, "task_id": task_id,
                "attempt_number": 1, "input_artifact_ids": []}

    def make_output(base, name):
        d = base / name
        d.mkdir()
        (d / "f.txt").write_text("data")
        return str(d)

    promoted = PromotedOutput(name="f.txt", artifact_type="EXECUTION_LOG")
    cfg = config.__class__(storage_root=str(tmp_path), database_url=config.database_url)

    task1 = make_task_run(conn, seed_run, "TASK-1")
    id1 = promote_output(conn, cfg, task1, promoted, make_output(tmp_path, "out1"))

    task2 = make_task_run(conn, seed_run, "TASK-2")
    id2 = promote_output(conn, cfg, task2, promoted, make_output(tmp_path, "out2"))

    r1 = conn.execute("SELECT version, status FROM artifacts WHERE artifact_id = %s", (id1,)).fetchone()
    r2 = conn.execute("SELECT version, status FROM artifacts WHERE artifact_id = %s", (id2,)).fetchone()
    assert r1["version"] == 1
    assert r1["status"] == "SUPERSEDED"  # second promotion superseded it
    assert r2["version"] == 2
    assert r2["status"] == "ACTIVE"
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest tests/integration/test_promote.py -v
```
Expected: FAIL (ImportError)

- [ ] **Step 3: Create AgentResult and PromotedOutput dataclasses**

```python
# src/ai_dev_system/agents/base.py
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class PromotedOutput:
    name: str
    artifact_type: str
    description: str = ""

@dataclass
class AgentResult:
    output_path: str
    promoted_outputs: list[PromotedOutput] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.error is None
```

- [ ] **Step 4: Implement promote.py**

```python
# src/ai_dev_system/storage/promote.py
import json
import os
import shutil
from datetime import datetime, timezone

import psycopg

from ai_dev_system.agents.base import PromotedOutput
from ai_dev_system.config import Config
from ai_dev_system.db.repos.artifacts import ArtifactRepo
from ai_dev_system.db.repos.events import EventRepo
from ai_dev_system.db.repos.runs import RunRepo
from ai_dev_system.db.repos.task_runs import TaskRunRepo
from ai_dev_system.db.repos.version_locks import VersionLockRepo
from ai_dev_system.storage.checksum import checksum_artifact
from ai_dev_system.storage.paths import ARTIFACT_TYPE_TO_KEY, build_artifact_path
from ai_dev_system.storage.stability import wait_until_stable


class IntegrityError(Exception):
    pass


class PromotionConflictError(Exception):
    pass


def promote_output(
    conn: psycopg.Connection,
    config: Config,
    task_run: dict,
    promoted_output: PromotedOutput,
    temp_output_path: str,
) -> str:
    """
    Promotion protocol Steps 2–7 (Step 1 = task execution is caller responsibility).

    Step 1 (caller): agent writes output files into temp_output_path.
    Steps 2–7 (this function): validate, move, checksum, DB transaction.

    MUST be called inside an open transaction. Caller (worker.py) manages
    the transaction boundary — pickup is a separate transaction from promotion
    so that the task_run row lock is not held during agent execution.

    Returns artifact_id (str).
    """
    run_id = task_run["run_id"]
    task_run_id = task_run["task_run_id"]
    artifact_type = promoted_output.artifact_type

    # Step 2: Wait for stable
    wait_until_stable(temp_output_path, poll_interval_ms=100, stable_duration_ms=200)

    # Step 3: Validate (v1: existence check only)
    if not os.path.exists(temp_output_path):
        raise FileNotFoundError(f"temp_output_path does not exist: {temp_output_path}")

    # Step 4: Disk space check (v1: skip — add when needed)

    # Step 5: Two-phase atomic move
    # Contract requires staging to be adjacent to final_path (same filesystem as final, not temp).
    # We need the version for the final path → lock version counter first, then move.
    version_lock_repo = VersionLockRepo(conn)
    artifact_repo = ArtifactRepo(conn)
    task_run_repo = TaskRunRepo(conn)
    run_repo = RunRepo(conn)
    event_repo = EventRepo(conn)

    # 7a: Lock and get next version (must happen before building paths)
    next_version = version_lock_repo.lock_and_increment(run_id, artifact_type)

    final_path = build_artifact_path(config.storage_root, run_id, artifact_type, next_version)
    staging_path = final_path + ".staging"  # adjacent to final, same filesystem — atomic rename guaranteed

    os.makedirs(staging_path, exist_ok=False)
    # Move contents from temp into staging
    for item in os.listdir(temp_output_path):
        shutil.move(os.path.join(temp_output_path, item), staging_path)

    staging_checksum, staging_size = checksum_artifact(staging_path)

    os.makedirs(os.path.dirname(final_path), exist_ok=True)
    os.rename(staging_path, final_path)

    # Step 6: Verify after rename
    content_checksum, content_size = checksum_artifact(final_path)
    if content_checksum != staging_checksum or content_size != staging_size:
        raise IntegrityError(
            f"Integrity mismatch after rename: checksum {staging_checksum!r} vs {content_checksum!r}, "
            f"size {staging_size} vs {content_size}"
        )

    # Step 6b: Write _complete.marker
    with open(os.path.join(final_path, "_complete.marker"), "w") as f:
        json.dump({
            "artifact_type": artifact_type,
            "content_checksum": content_checksum,
            "content_size": content_size,
            "promoted_at": datetime.now(timezone.utc).isoformat(),
        }, f)
    content_checksum, content_size = checksum_artifact(final_path)

    # 7b: Promotion guard — FOR UPDATE prevents concurrent workers from passing simultaneously
    guarded = conn.execute("""
        SELECT 1 FROM task_runs
        WHERE task_run_id = %s AND status = 'RUNNING'
          AND output_artifact_id IS NULL AND completed_at IS NULL
        FOR UPDATE
    """, (task_run_id,)).fetchone()
    if not guarded:
        raise PromotionConflictError(f"task_run {task_run_id} not eligible for promotion")

    # 7c: Supersede old active
    artifact_repo.supersede_active(run_id, artifact_type)

    # 7d: Insert new artifact
    artifact_id = artifact_repo.insert(
        run_id=run_id,
        artifact_type=artifact_type,
        version=next_version,
        created_by="system",
        input_artifact_ids=task_run.get("input_artifact_ids", []),
        content_ref=final_path,
        content_checksum=content_checksum,
        content_size=content_size,
    )

    # 7e: Update task_run
    updated = task_run_repo.mark_success(task_run_id, final_path, artifact_id)
    if updated == 0:
        raise PromotionConflictError(f"task_run {task_run_id} updated by another worker")

    # 7f: Update runs.current_artifacts
    artifact_key = ARTIFACT_TYPE_TO_KEY.get(artifact_type)
    if artifact_key is not None:
        run_repo.update_current_artifact(run_id, artifact_key, artifact_id)

    # 7g: Events
    event_repo.insert(run_id, "ARTIFACT_CREATED", "system", task_run_id,
                      {"artifact_id": artifact_id, "version": next_version})
    event_repo.insert(run_id, "TASK_COMPLETED", "system", task_run_id,
                      {"artifact_id": artifact_id})

    return artifact_id
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/integration/test_promote.py -v
```
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add src/ai_dev_system/storage/promote.py src/ai_dev_system/agents/base.py tests/integration/test_promote.py
git commit -m "feat: promote_output 7-step protocol"
```

---

## Task 7: Stub Agent

**Files:**
- Create: `src/ai_dev_system/agents/stub.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_stub_agent.py
import os
from ai_dev_system.agents.stub import StubAgent
from ai_dev_system.agents.base import PromotedOutput

def test_stub_agent_creates_output_files(tmp_path):
    agent = StubAgent()
    promoted = [PromotedOutput(name="result.json", artifact_type="EXECUTION_LOG")]
    result = agent.run(
        task_id="TASK-1",
        output_path=str(tmp_path),
        promoted_outputs=promoted,
    )
    assert result.success
    assert os.path.exists(os.path.join(str(tmp_path), "result.json"))
```

- [ ] **Step 2: Implement stub.py**

```python
# src/ai_dev_system/agents/stub.py
import json
import os
from ai_dev_system.agents.base import AgentResult, PromotedOutput

class StubAgent:
    """Test double — writes expected output files deterministically."""

    def run(
        self,
        task_id: str,
        output_path: str,
        promoted_outputs: list[PromotedOutput],
    ) -> AgentResult:
        os.makedirs(output_path, exist_ok=True)
        for po in promoted_outputs:
            filepath = os.path.join(output_path, po.name)
            with open(filepath, "w") as f:
                json.dump({"task_id": task_id, "status": "stub_complete"}, f)
        return AgentResult(output_path=output_path, promoted_outputs=promoted_outputs)
```

- [ ] **Step 3: Run test and commit**

```bash
pytest tests/unit/test_stub_agent.py -v
git add src/ai_dev_system/agents/ tests/unit/test_stub_agent.py
git commit -m "feat: StubAgent test double"
```

---

## Task 8: Engine — Dependency Resolver (Job A)

**Files:**
- Create: `src/ai_dev_system/engine/resolver.py`
- Create: `tests/integration/test_resolver.py`

- [ ] **Step 1: Write failing test**

```python
# tests/integration/test_resolver.py
import uuid
import pytest
from ai_dev_system.engine.resolver import resolve_dependencies

def seed_task(conn, run_id, task_id, status, deps=None):
    tid = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO task_runs (task_run_id, run_id, task_id, attempt_number, status,
            agent_type, input_artifact_ids, resolved_dependencies, promoted_outputs)
        VALUES (%s, %s, %s, 1, %s, 'StubAgent', '{}', %s, '[]')
    """, (tid, run_id, task_id, status, deps or []))
    return tid

def test_task_with_no_deps_moves_to_ready(conn, seed_run):
    tid = seed_task(conn, seed_run, "TASK-1", "PENDING", deps=[])
    resolve_dependencies(conn, seed_run)
    row = conn.execute("SELECT status FROM task_runs WHERE task_run_id = %s", (tid,)).fetchone()
    assert row["status"] == "READY"

def test_task_with_unsatisfied_dep_stays_pending(conn, seed_run):
    seed_task(conn, seed_run, "TASK-1", "PENDING", deps=[])   # dep, still PENDING
    tid2 = seed_task(conn, seed_run, "TASK-2", "PENDING", deps=["TASK-1"])
    resolve_dependencies(conn, seed_run)
    row = conn.execute("SELECT status FROM task_runs WHERE task_run_id = %s", (tid2,)).fetchone()
    assert row["status"] == "PENDING"

def test_task_with_satisfied_dep_moves_to_ready(conn, seed_run):
    seed_task(conn, seed_run, "TASK-1", "SUCCESS", deps=[])
    tid2 = seed_task(conn, seed_run, "TASK-2", "PENDING", deps=["TASK-1"])
    resolve_dependencies(conn, seed_run)
    row = conn.execute("SELECT status FROM task_runs WHERE task_run_id = %s", (tid2,)).fetchone()
    assert row["status"] == "READY"

def test_skipped_dep_counts_as_satisfied(conn, seed_run):
    seed_task(conn, seed_run, "TASK-1", "SKIPPED", deps=[])
    tid2 = seed_task(conn, seed_run, "TASK-2", "PENDING", deps=["TASK-1"])
    resolve_dependencies(conn, seed_run)
    row = conn.execute("SELECT status FROM task_runs WHERE task_run_id = %s", (tid2,)).fetchone()
    assert row["status"] == "READY"
```

- [ ] **Step 2: Implement resolver.py**

```python
# src/ai_dev_system/engine/resolver.py
import psycopg
from ai_dev_system.db.repos.events import EventRepo

def resolve_dependencies(conn: psycopg.Connection, run_id: str) -> int:
    """
    Move PENDING tasks to READY if all dependencies satisfied.
    Returns count of tasks promoted to READY.
    """
    event_repo = EventRepo(conn)
    pending = conn.execute(
        "SELECT task_run_id, task_id, resolved_dependencies FROM task_runs WHERE run_id = %s AND status = 'PENDING'",
        (run_id,)
    ).fetchall()

    promoted = 0
    for task in pending:
        deps = task["resolved_dependencies"] or []
        if not deps:
            all_satisfied = True
        else:
            row = conn.execute("""
                SELECT NOT EXISTS (
                    SELECT 1 FROM unnest(%s::text[]) AS dep(task_id)
                    WHERE NOT EXISTS (
                        SELECT 1 FROM task_runs
                        WHERE run_id = %s AND task_id = dep.task_id
                          AND status IN ('SUCCESS', 'SKIPPED')
                    )
                ) AS satisfied
            """, (deps, run_id)).fetchone()
            all_satisfied = row["satisfied"]

        if all_satisfied:
            result = conn.execute("""
                UPDATE task_runs SET status = 'READY'
                WHERE task_run_id = %s AND status = 'PENDING'
            """, (task["task_run_id"],))
            if result.rowcount == 1:
                event_repo.insert(run_id, "TASK_READY", "system", task["task_run_id"])
                promoted += 1

    return promoted
```

- [ ] **Step 3: Run tests**

```bash
pytest tests/integration/test_resolver.py -v
```
Expected: PASS (4 tests)

- [ ] **Step 4: Commit**

```bash
git add src/ai_dev_system/engine/resolver.py tests/integration/test_resolver.py
git commit -m "feat: dependency resolver PENDING → READY"
```

---

## Task 9: Engine — Worker (Job B+C) + Loop

**Files:**
- Create: `src/ai_dev_system/engine/worker.py`
- Create: `src/ai_dev_system/engine/loop.py`
- Create: `tests/integration/test_worker_loop.py`

- [ ] **Step 1: Write failing integration test (end-to-end)**

Tests use two-transaction model: `pickup_task` (Tx 1) then `execute_and_promote` (Tx 2).
In tests, both run on the same `conn` (single rollback fixture) — correct for unit isolation.

```python
# tests/integration/test_worker_loop.py
import os
import pytest
from ai_dev_system.engine.worker import pickup_task, execute_and_promote
from ai_dev_system.agents.stub import StubAgent

def test_full_pickup_execute_promote(conn, seed_run, seed_task_run, tmp_path, config):
    """READY task → pickup → execute stub → promote → artifact ACTIVE in DB."""
    cfg = config.__class__(storage_root=str(tmp_path), database_url=config.database_url)
    conn.execute("""
        UPDATE task_runs
        SET promoted_outputs = '[{"name": "result.json", "artifact_type": "EXECUTION_LOG", "description": "stub"}]'
        WHERE task_run_id = %s
    """, (seed_task_run,))

    task = pickup_task(conn, cfg, run_id=seed_run, worker_id="w1")
    assert task is not None

    result = StubAgent().run(task["task_id"], task["temp_path"], task["promoted_outputs_parsed"])
    status = execute_and_promote(conn, cfg, task, result, worker_id="w1")

    assert status == "SUCCESS"
    artifact = conn.execute("""
        SELECT status, version FROM artifacts WHERE run_id = %s AND artifact_type = 'EXECUTION_LOG'
    """, (seed_run,)).fetchone()
    assert artifact["status"] == "ACTIVE"
    assert artifact["version"] == 1

def test_no_ready_task_returns_none(conn, seed_run, tmp_path, config):
    cfg = config.__class__(storage_root=str(tmp_path), database_url=config.database_url)
    task = pickup_task(conn, cfg, run_id=seed_run, worker_id="w1")
    assert task is None
```

- [ ] **Step 2: Implement worker.py**

Two functions matching the two-transaction model. `pickup_task` = Tx 1. `execute_and_promote` = Tx 2.

```python
# src/ai_dev_system/engine/worker.py
import json
import os
from typing import Optional

import psycopg

from ai_dev_system.agents.base import AgentResult, PromotedOutput
from ai_dev_system.config import Config
from ai_dev_system.db.repos.events import EventRepo
from ai_dev_system.db.repos.task_runs import TaskRunRepo
from ai_dev_system.engine.resolver import resolve_dependencies
from ai_dev_system.storage.paths import build_temp_path
from ai_dev_system.storage.promote import promote_output


def pickup_task(
    conn: psycopg.Connection,
    config: Config,
    run_id: str,
    worker_id: str,
) -> Optional[dict]:
    """
    Tx 1 (Job B): Lock a READY task, mark RUNNING, emit TASK_STARTED.
    Returns enriched task dict (includes temp_path, promoted_outputs_parsed) or None.
    Short transaction — releases task_run lock before agent execution.
    """
    repo = TaskRunRepo(conn)
    event_repo = EventRepo(conn)

    task = repo.pickup(run_id=run_id, worker_id=worker_id)
    if task is None:
        return None

    event_repo.insert(run_id, "TASK_STARTED", f"worker:{worker_id}", task["task_run_id"])

    promoted_raw = task.get("promoted_outputs") or []
    if isinstance(promoted_raw, str):
        promoted_raw = json.loads(promoted_raw)
    promoted_outputs = [PromotedOutput(**po) for po in promoted_raw]

    temp_path = build_temp_path(config.storage_root, run_id, task["task_id"], task["attempt_number"])
    os.makedirs(temp_path, exist_ok=True)

    return task | {"temp_path": temp_path, "promoted_outputs_parsed": promoted_outputs}


def execute_and_promote(
    conn: psycopg.Connection,
    config: Config,
    task: dict,
    result: AgentResult,
    worker_id: str,
) -> str:
    """
    Tx 2 (Job C): Given agent result, promote outputs and mark task SUCCESS/FAILED.
    Returns final task status string.
    """
    repo = TaskRunRepo(conn)
    event_repo = EventRepo(conn)
    run_id = task["run_id"]

    if not result.success:
        repo.mark_failed(task["task_run_id"], "EXECUTION_ERROR", result.error or "unknown")
        event_repo.insert(run_id, "TASK_FAILED", f"worker:{worker_id}", task["task_run_id"],
                          {"error": result.error})
        return "FAILED"

    for po in task["promoted_outputs_parsed"]:
        promote_output(conn, config, task, po, task["temp_path"])

    if not task["promoted_outputs_parsed"]:
        repo.mark_success(task["task_run_id"], task["temp_path"], None)
        event_repo.insert(run_id, "TASK_COMPLETED", f"worker:{worker_id}", task["task_run_id"], {})

    resolve_dependencies(conn, run_id)
    return "SUCCESS"
```

- [ ] **Step 3: Implement loop.py**

```python
# src/ai_dev_system/engine/loop.py
import logging
import time
from typing import Optional

import psycopg

from ai_dev_system.config import Config
from ai_dev_system.engine.worker import pickup_task, execute_and_promote

logger = logging.getLogger(__name__)

def run_worker_loop(
    config: Config,
    run_id: str,
    worker_id: str,
    agent,
    idle_backoff_s: float = 1.0,
    max_iterations: Optional[int] = None,
) -> None:
    """
    Main worker loop.

    Transaction boundary (per execution-engine spec: "mỗi transition = 1 transaction"):
      Tx 1: pickup — lock task_run row, set RUNNING (short)
      Tx 2: promote — version lock + artifact insert + task_run SUCCESS (after agent finishes)

    This ensures the FOR UPDATE SKIP LOCKED row is released before agent execution,
    so other workers can attempt other tasks while this one runs.
    """
    iterations = 0
    conn_factory = lambda: psycopg.connect(
        config.database_url, autocommit=False, row_factory=psycopg.rows.dict_row
    )

    while True:
        if max_iterations is not None and iterations >= max_iterations:
            break
        iterations += 1

        # Tx 1: pickup
        task = None
        with conn_factory() as conn:
            try:
                conn.execute("BEGIN")
                task = pickup_task(conn, config, run_id, worker_id)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                logger.exception("Pickup error")
                time.sleep(idle_backoff_s * 2)
                continue

        if task is None:
            logger.debug("No tasks available, backing off %ss", idle_backoff_s)
            time.sleep(idle_backoff_s)
            continue

        # Agent execution (outside any transaction)
        result = agent.run(
            task_id=task["task_id"],
            output_path=task["temp_path"],
            promoted_outputs=task["promoted_outputs_parsed"],
        )

        # Tx 2: promote
        with conn_factory() as conn:
            try:
                conn.execute("BEGIN")
                status = execute_and_promote(conn, config, task, result, worker_id)
                conn.execute("COMMIT")
                logger.info("Task %s → %s", task["task_id"], status)
            except Exception:
                conn.execute("ROLLBACK")
                logger.exception("Promotion error for task %s", task["task_id"])
                time.sleep(idle_backoff_s * 2)
```

- [ ] **Step 4: Run end-to-end test**

```bash
pytest tests/integration/test_worker_loop.py -v
```
Expected: PASS (2 tests)

- [ ] **Step 5: Run full test suite**

```bash
pytest tests/ -v
```
Expected: All green

- [ ] **Step 6: Commit**

```bash
git add src/ai_dev_system/engine/ tests/integration/test_worker_loop.py
git commit -m "feat: worker pickup+execute+promote loop"
```

---

## Task 10: Smoke Test End-to-End

**Files:**
- Create: `tests/integration/test_e2e_single_task.py`

Final validation: one task flows through the entire system.

- [ ] **Step 1: Write smoke test**

```python
# tests/integration/test_e2e_single_task.py
"""
End-to-end: insert run + 1 PENDING task → resolve → pickup → execute → promote → verify ACTIVE artifact
"""
import uuid
import pytest
from ai_dev_system.engine.resolver import resolve_dependencies
from ai_dev_system.engine.worker import pickup_task, execute_and_promote
from ai_dev_system.agents.stub import StubAgent

def test_single_task_flow(conn, tmp_path, config):
    cfg = config.__class__(storage_root=str(tmp_path), database_url=config.database_url)
    run_id = str(uuid.uuid4())

    # Seed run
    conn.execute("""
        INSERT INTO runs (run_id, status, title, current_artifacts, metadata)
        VALUES (%s, 'RUNNING_PHASE_3', 'E2E Test', '{}', '{}')
    """, (run_id,))

    # Seed PENDING task with no deps
    task_run_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO task_runs (task_run_id, run_id, task_id, attempt_number, status,
            agent_type, input_artifact_ids, resolved_dependencies, promoted_outputs)
        VALUES (%s, %s, 'TASK-1', 1, 'PENDING', 'StubAgent', '{}', '{}',
                '[{"name": "output.json", "artifact_type": "EXECUTION_LOG", "description": "e2e"}]')
    """, (task_run_id, run_id))

    # Phase 1: Resolve dependencies
    promoted_count = resolve_dependencies(conn, run_id)
    assert promoted_count == 1

    status = conn.execute(
        "SELECT status FROM task_runs WHERE task_run_id = %s", (task_run_id,)
    ).fetchone()["status"]
    assert status == "READY"

    # Phase 2: Worker picks up (Tx 1) then executes + promotes (Tx 2)
    task = pickup_task(conn, cfg, run_id=run_id, worker_id="e2e-worker")
    assert task is not None
    agent_result = StubAgent().run(task["task_id"], task["temp_path"], task["promoted_outputs_parsed"])
    status = execute_and_promote(conn, cfg, task, agent_result, worker_id="e2e-worker")
    assert status == "SUCCESS"

    # Phase 3: Verify artifact
    artifact = conn.execute("""
        SELECT status, version, content_ref FROM artifacts
        WHERE run_id = %s AND artifact_type = 'EXECUTION_LOG'
    """, (run_id,)).fetchone()
    assert artifact["status"] == "ACTIVE"
    assert artifact["version"] == 1

    import os
    assert os.path.exists(os.path.join(artifact["content_ref"], "_complete.marker"))
    assert os.path.exists(os.path.join(artifact["content_ref"], "output.json"))

    # Phase 4: Verify task_run final state
    task_row = conn.execute(
        "SELECT status, output_artifact_id FROM task_runs WHERE task_run_id = %s", (task_run_id,)
    ).fetchone()
    assert task_row["status"] == "SUCCESS"
    assert task_row["output_artifact_id"] is not None
```

- [ ] **Step 2: Run smoke test**

```bash
pytest tests/integration/test_e2e_single_task.py -v
```
Expected: PASS

- [ ] **Step 3: Run full suite one final time**

```bash
pytest tests/ -v --tb=short
```
Expected: All green

- [ ] **Step 4: Final commit**

```bash
git add tests/integration/test_e2e_single_task.py
git commit -m "test: e2e smoke test — single task PENDING → ACTIVE artifact"
```

---

## Phase 1 Done — What This Gives You

Sau khi complete plan này:
- `promote_output()` chạy đúng 7 bước, test đầy đủ
- Worker pickup + execute + promote, idempotent
- Dependency resolver PENDING → READY
- Stub agent để test bất kỳ flow nào
- E2E test chứng minh cả pipeline chạy được

**Phase 2 (không trong scope plan này):**
- Dead Worker Recovery (heartbeat stale → mark FAILED)
- Failure Handler (retry policy per error_type)
- Orphan Cleanup Job
- Real agent integration (replace StubAgent)
