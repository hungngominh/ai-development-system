# Execution Runner v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Phase 3 execution to the AI Dev System — materializes an approved task graph into DB task_runs, runs them with heartbeat/retry/failure propagation, and surfaces escalations for human-in-the-loop decisions.

**Architecture:** Three-layer design: (1) Materializer turns the approved task graph into PENDING task_run rows; (2) two threads (Worker + Background) run concurrently — worker picks up and executes tasks, background handles health recovery, PENDING→READY transitions, and completion detection; (3) escalation module handles human decisions (retry/skip/abort) when tasks fail permanently.

**Tech Stack:** Python 3.12, psycopg v3, PostgreSQL 15+, threading (stdlib). All new code is additive — existing `run_worker_loop`, `pickup_task`, `execute_and_promote`, and `resolve_dependencies` remain unchanged.

**Spec:** `docs/superpowers/specs/2026-03-29-execution-runner-design.md`
**Worktree:** `.worktrees/minimal-worker-loop/` (all file paths below are relative to this directory)

---

## File Map

### New Files
| File | Responsibility |
|------|---------------|
| `src/ai_dev_system/engine/materializer.py` | Graph → task_run rows (idempotent); artifact path resolution |
| `src/ai_dev_system/engine/heartbeat.py` | Per-task heartbeat thread (conn_factory pattern) |
| `src/ai_dev_system/engine/background.py` | Background loop: recover_dead + mark_ready + check_completion |
| `src/ai_dev_system/engine/failure.py` | `_handle_failure()` + `propagate_failure()` BFS |
| `src/ai_dev_system/engine/escalation.py` | `resolve_escalation()` + `_unblock_downstream_bfs()` |
| `src/ai_dev_system/engine/runner.py` | `run_execution()` entry point — thread orchestration |
| `src/ai_dev_system/db/repos/escalations.py` | `EscalationRepo`: upsert_open, get_open, mark_resolved |
| `docs/schema/migrations/v2-execution-runner.sql` | New enum values, new columns on task_runs, escalations table |
| `tests/unit/test_materializer.py` | Materializer idempotency, context snapshot |
| `tests/unit/test_background_jobs.py` | mark_ready (retry_at), recover_dead, check_completion |
| `tests/unit/test_failure.py` | propagate_failure BFS, escalation dedup |
| `tests/unit/test_heartbeat.py` | conn_factory pattern, stop |
| `tests/unit/test_escalation.py` | retry/skip/abort resolution, BFS unblock |
| `tests/integration/test_runner_golden.py` | Scenario A: full happy path |
| `tests/integration/test_runner_escalation.py` | Scenario C: failure → escalation → skip |

### Modified Files
| File | Change |
|------|--------|
| `src/ai_dev_system/config.py` | Add heartbeat/poll/timeout/retry_policy fields with defaults |
| `src/ai_dev_system/db/repos/task_runs.py` | Add `mark_failed_final`, `mark_failed_retryable`, `create_retry`, `get_by_id` |
| `src/ai_dev_system/agents/base.py` | Extend `Agent` protocol with `context` + `timeout_s` params |
| `src/ai_dev_system/agents/stub.py` | Update StubAgent to accept `context` + `timeout_s` kwargs |
| `tests/conftest.py` | Add `seed_pending_task_run` fixture, `seed_graph_artifact` fixture |

---

## Task 1: DB Migration

**Files:**
- Create: `docs/schema/migrations/v2-execution-runner.sql`
- Modify: `tests/conftest.py`

The migration adds new status enum values, extends `task_runs` with new columns, and creates the `escalations` table. Must be applied to the test DB before any other task's tests can run.

- [ ] **Step 1.1: Write migration SQL**

Create `docs/schema/migrations/v2-execution-runner.sql`:

```sql
-- v2-execution-runner.sql
-- Requires PostgreSQL 15+ (NULLS NOT DISTINCT)

-- 1. New run statuses
ALTER TYPE run_status ADD VALUE IF NOT EXISTS 'RUNNING_EXECUTION';
ALTER TYPE run_status ADD VALUE IF NOT EXISTS 'PAUSED_FOR_DECISION';

-- 2. New task_run statuses
ALTER TYPE task_run_status ADD VALUE IF NOT EXISTS 'FAILED_RETRYABLE';
ALTER TYPE task_run_status ADD VALUE IF NOT EXISTS 'FAILED_FINAL';
ALTER TYPE task_run_status ADD VALUE IF NOT EXISTS 'BLOCKED_BY_FAILURE';

-- 3. New columns on task_runs
ALTER TABLE task_runs
    ADD COLUMN IF NOT EXISTS retry_count        INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS retry_at           TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS agent_routing_key  TEXT,
    ADD COLUMN IF NOT EXISTS context_snapshot   JSONB,
    ADD COLUMN IF NOT EXISTS materialized_at    TIMESTAMPTZ;

-- 4. Idempotency constraint for materializer
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'uq_task_runs_attempt'
    ) THEN
        ALTER TABLE task_runs
            ADD CONSTRAINT uq_task_runs_attempt
            UNIQUE (run_id, task_id, attempt_number);
    END IF;
END $$;

-- 5. Escalations table
CREATE TABLE IF NOT EXISTS escalations (
    escalation_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          UUID NOT NULL REFERENCES runs(run_id),
    task_run_id     UUID NOT NULL REFERENCES task_runs(task_run_id),
    status          TEXT NOT NULL DEFAULT 'OPEN'
                        CHECK (status IN ('OPEN', 'RESOLVED')),
    reason          TEXT NOT NULL,
    options         JSONB NOT NULL,
    resolution      TEXT,
    resolved_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- One OPEN escalation per (run, task_run, reason). Named so ON CONFLICT can reference it.
    -- NULLS NOT DISTINCT is irrelevant here (status is NOT NULL) but keeps the constraint
    -- from blocking re-escalation after a RESOLVED record exists for the same combination
    -- (RESOLVED ≠ OPEN, so a new OPEN escalation can always be inserted).
    CONSTRAINT uq_escalation_open_dedup
        UNIQUE (run_id, task_run_id, reason, status)
);

CREATE INDEX IF NOT EXISTS idx_escalations_run_open
    ON escalations (run_id) WHERE status = 'OPEN';
```

- [ ] **Step 1.2: Apply migration to test DB**

```bash
psql $DATABASE_URL -f docs/schema/migrations/v2-execution-runner.sql
```

Expected: all `ALTER TABLE`, `CREATE TABLE` statements execute without error.

- [ ] **Step 1.3: Verify columns and table exist**

```bash
psql $DATABASE_URL -c "\d task_runs" | grep -E "retry_count|retry_at|context_snapshot|materialized_at"
psql $DATABASE_URL -c "\d escalations"
```

Expected: columns listed, escalations table shown.

- [ ] **Step 1.4: Add new fixtures to `tests/conftest.py`**

**Note:** The existing `conftest.py` already defines `config`, `project_id`, `conn`, and `seed_run` fixtures. The `seed_task_run` fixture (used in heartbeat and escalation unit tests) also already exists — it inserts a READY task_run row. Only add `seed_graph_artifact` and `seed_pending_task_run` below if they are missing.

```python
import json

@pytest.fixture
def seed_graph_artifact(conn, seed_run, tmp_path):
    """TASK_GRAPH_APPROVED artifact backed by a real file for materializer tests."""
    artifact_id = str(uuid.uuid4())
    # Write a minimal valid graph JSON
    graph = {
        "graph_version": 1,
        "tasks": [
            {
                "id": "TASK-PARSE", "execution_type": "atomic",
                "phase": "parse_spec", "type": "design",
                "agent_type": "SpecAnalyst",
                "objective": "Parse spec", "description": "", "done_definition": "done",
                "verification_steps": [], "deps": [],
                "required_inputs": [], "expected_outputs": [],
            },
            {
                "id": "TASK-DESIGN", "execution_type": "atomic",
                "phase": "design_solution", "type": "design",
                "agent_type": "Architect",
                "objective": "Design", "description": "", "done_definition": "done",
                "verification_steps": [], "deps": ["TASK-PARSE"],
                "required_inputs": [], "expected_outputs": [],
            },
        ],
    }
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    (graph_dir / "task_graph.json").write_text(json.dumps(graph))
    conn.execute("""
        INSERT INTO artifacts (
            artifact_id, run_id, artifact_type, version, status, created_by,
            input_artifact_ids, content_ref, content_checksum, content_size
        ) VALUES (%s, %s, 'TASK_GRAPH_APPROVED', 1, 'ACTIVE', 'system',
                  '{}', %s, 'abc123', 0)
    """, (artifact_id, seed_run, str(graph_dir)))
    return artifact_id


@pytest.fixture
def seed_pending_task_run(conn, seed_run):
    """Insert a PENDING task_run with no dependencies (ready to be resolved)."""
    task_run_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO task_runs (
            task_run_id, run_id, task_id, attempt_number, status,
            agent_type, input_artifact_ids, resolved_dependencies, promoted_outputs,
            retry_count
        ) VALUES (%s, %s, 'TASK-PARSE', 1, 'PENDING',
                  'SpecAnalyst', '{}', '{}', '[]', 0)
    """, (task_run_id, seed_run))
    return task_run_id
```

- [ ] **Step 1.5: Run existing tests to verify no regressions**

```bash
cd .worktrees/minimal-worker-loop
pytest tests/ -x -q
```

Expected: all existing tests pass. The new columns have defaults, so no INSERT failures.

- [ ] **Step 1.6: Commit**

```bash
git add docs/schema/migrations/v2-execution-runner.sql tests/conftest.py
git commit -m "feat: db migration v2 - new statuses, retry columns, escalations table"
```

---

## Task 2: Extend Config

**Files:**
- Modify: `src/ai_dev_system/config.py`

Add execution runner settings with production-safe defaults. Backward compatible — existing code using `Config(storage_root=..., database_url=...)` still works.

- [ ] **Step 2.1: Write failing test**

Create `tests/unit/test_config.py`:

```python
from ai_dev_system.config import Config, RetryPolicy


def test_config_defaults():
    cfg = Config(storage_root="/tmp", database_url="postgresql://x")
    assert cfg.poll_interval_s == 5.0
    assert cfg.heartbeat_interval_s == 30.0
    assert cfg.heartbeat_timeout_s == 120.0
    assert cfg.task_timeout_s == 3600.0
    assert isinstance(cfg.retry_policy, dict)
    assert cfg.retry_policy["EXECUTION_ERROR"]["max_retries"] == 2
    assert cfg.retry_policy["ENVIRONMENT_ERROR"]["retry_delay_s"] == 5.0


def test_config_retry_policy_keys():
    cfg = Config(storage_root="/tmp", database_url="postgresql://x")
    for key in ("EXECUTION_ERROR", "ENVIRONMENT_ERROR", "SPEC_AMBIGUITY",
                "SPEC_CONTRADICTION", "UNKNOWN"):
        assert key in cfg.retry_policy, f"Missing key: {key}"
```

- [ ] **Step 2.2: Run test to verify it fails**

```bash
pytest tests/unit/test_config.py -v
```

Expected: `AttributeError: 'Config' object has no attribute 'poll_interval_s'`

- [ ] **Step 2.3: Implement**

Replace `src/ai_dev_system/config.py`:

```python
import os
from dataclasses import dataclass, field
from typing import Any


def _default_retry_policy() -> dict[str, dict[str, Any]]:
    return {
        "EXECUTION_ERROR":     {"max_retries": 2, "retry_delay_s": 0},
        "ENVIRONMENT_ERROR":   {"max_retries": 3, "retry_delay_s": 5.0},
        "SPEC_AMBIGUITY":      {"max_retries": 0, "retry_delay_s": 0},
        "SPEC_CONTRADICTION":  {"max_retries": 0, "retry_delay_s": 0},
        "UNKNOWN":             {"max_retries": 1, "retry_delay_s": 0},
    }


@dataclass
class Config:
    storage_root: str
    database_url: str
    poll_interval_s: float = 5.0
    heartbeat_interval_s: float = 30.0
    heartbeat_timeout_s: float = 120.0
    task_timeout_s: float = 3600.0
    retry_policy: dict = field(default_factory=_default_retry_policy)

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

- [ ] **Step 2.4: Run test to verify it passes**

```bash
pytest tests/unit/test_config.py tests/ -x -q
```

Expected: new tests PASS, all existing tests still PASS.

- [ ] **Step 2.5: Commit**

```bash
git add src/ai_dev_system/config.py tests/unit/test_config.py
git commit -m "feat: extend Config with execution runner settings"
```

---

## Task 3: Extend TaskRunRepo + EscalationRepo

**Files:**
- Modify: `src/ai_dev_system/db/repos/task_runs.py`
- Create: `src/ai_dev_system/db/repos/escalations.py`
- Test: `tests/unit/test_repos.py`

- [ ] **Step 3.1: Write failing tests**

Create `tests/unit/test_repos.py`:

```python
import uuid
import pytest
from ai_dev_system.db.repos.task_runs import TaskRunRepo
from ai_dev_system.db.repos.escalations import EscalationRepo


def _insert_running_task(conn, run_id, task_id="TASK-1"):
    task_run_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO task_runs (
            task_run_id, run_id, task_id, attempt_number, status,
            agent_type, input_artifact_ids, resolved_dependencies,
            promoted_outputs, retry_count, worker_id,
            locked_at, heartbeat_at, started_at
        ) VALUES (%s, %s, %s, 1, 'RUNNING',
                  'agent', '{}', '{}', '[]', 0, 'worker-1',
                  now(), now(), now())
    """, (task_run_id, run_id, task_id))
    return task_run_id


def test_mark_failed_final_changes_status(conn, seed_run):
    task_run_id = _insert_running_task(conn, seed_run)
    repo = TaskRunRepo(conn)
    rows = repo.mark_failed_final(task_run_id, "EXECUTION_ERROR", "exploded")
    assert rows == 1
    row = conn.execute(
        "SELECT status FROM task_runs WHERE task_run_id = %s", (task_run_id,)
    ).fetchone()
    assert row["status"] == "FAILED_FINAL"


def test_mark_failed_retryable_changes_status(conn, seed_run):
    task_run_id = _insert_running_task(conn, seed_run)
    repo = TaskRunRepo(conn)
    rows = repo.mark_failed_retryable(task_run_id, "EXECUTION_ERROR", "transient")
    assert rows == 1
    row = conn.execute(
        "SELECT status FROM task_runs WHERE task_run_id = %s", (task_run_id,)
    ).fetchone()
    assert row["status"] == "FAILED_RETRYABLE"


def test_create_retry_increments_attempt_number(conn, seed_run):
    task_run_id = _insert_running_task(conn, seed_run)
    conn.execute(
        "UPDATE task_runs SET status = 'FAILED_RETRYABLE', retry_count = 0 WHERE task_run_id = %s",
        (task_run_id,)
    )
    repo = TaskRunRepo(conn)
    source = conn.execute(
        "SELECT * FROM task_runs WHERE task_run_id = %s", (task_run_id,)
    ).fetchone()
    new_id = repo.create_retry(seed_run, dict(source), retry_delay_s=0, reset_retry_count=False)
    new_row = conn.execute(
        "SELECT attempt_number, retry_count, previous_attempt_id FROM task_runs WHERE task_run_id = %s",
        (new_id,)
    ).fetchone()
    assert new_row["attempt_number"] == 2
    assert new_row["retry_count"] == 1
    assert new_row["previous_attempt_id"] == task_run_id


def test_create_retry_resets_count_for_human_override(conn, seed_run):
    task_run_id = _insert_running_task(conn, seed_run)
    conn.execute(
        "UPDATE task_runs SET status = 'FAILED_FINAL', retry_count = 3 WHERE task_run_id = %s",
        (task_run_id,)
    )
    repo = TaskRunRepo(conn)
    source = conn.execute(
        "SELECT * FROM task_runs WHERE task_run_id = %s", (task_run_id,)
    ).fetchone()
    new_id = repo.create_retry(seed_run, dict(source), retry_delay_s=0, reset_retry_count=True)
    new_row = conn.execute(
        "SELECT retry_count FROM task_runs WHERE task_run_id = %s", (new_id,)
    ).fetchone()
    assert new_row["retry_count"] == 0   # reset for human override


def test_escalation_upsert_open_creates_record(conn, seed_run, seed_task_run):
    repo = EscalationRepo(conn)
    esc_id = repo.upsert_open(
        run_id=seed_run,
        task_run_id=seed_task_run,
        reason="TASK_FAILURE",
        options=["retry", "skip", "abort"],
    )
    assert esc_id is not None
    row = conn.execute(
        "SELECT * FROM escalations WHERE escalation_id = %s", (esc_id,)
    ).fetchone()
    assert row["status"] == "OPEN"
    assert row["reason"] == "TASK_FAILURE"


def test_escalation_upsert_open_idempotent(conn, seed_run, seed_task_run):
    repo = EscalationRepo(conn)
    id1 = repo.upsert_open(seed_run, seed_task_run, "TASK_FAILURE", ["retry", "skip", "abort"])
    id2 = repo.upsert_open(seed_run, seed_task_run, "TASK_FAILURE", ["retry", "skip", "abort"])
    # Second call returns the existing open escalation
    assert id1 == id2


def test_escalation_get_open(conn, seed_run, seed_task_run):
    repo = EscalationRepo(conn)
    repo.upsert_open(seed_run, seed_task_run, "TASK_FAILURE", ["retry"])
    open_escs = repo.get_open(seed_run)
    assert len(open_escs) == 1
    assert open_escs[0]["reason"] == "TASK_FAILURE"
```

- [ ] **Step 3.2: Run tests to verify they fail**

```bash
pytest tests/unit/test_repos.py -v
```

Expected: `ImportError: cannot import name 'EscalationRepo'` and `AttributeError` for new TaskRunRepo methods.

- [ ] **Step 3.3: Add new methods to TaskRunRepo**

Add to end of `src/ai_dev_system/db/repos/task_runs.py`:

```python
    def mark_failed_final(self, task_run_id: str, error_type: str, error_detail: str) -> int:
        result = self.conn.execute("""
            UPDATE task_runs
            SET status = 'FAILED_FINAL',
                error_type = %s::error_type,
                error_detail = %s,
                completed_at = now()
            WHERE task_run_id = %s AND status = 'RUNNING'
        """, (error_type, error_detail, task_run_id))
        return result.rowcount

    def mark_failed_retryable(self, task_run_id: str, error_type: str, error_detail: str) -> int:
        result = self.conn.execute("""
            UPDATE task_runs
            SET status = 'FAILED_RETRYABLE',
                error_type = %s::error_type,
                error_detail = %s,
                completed_at = now()
            WHERE task_run_id = %s AND status = 'RUNNING'
        """, (error_type, error_detail, task_run_id))
        return result.rowcount

    def create_retry(
        self,
        run_id: str,
        source_task: dict,
        retry_delay_s: float = 0,
        reset_retry_count: bool = False,
    ) -> str:
        """Create a new attempt row linked to source_task. Returns new task_run_id.
        Uses self.conn — caller must call this inside an open transaction.
        reset_retry_count=True for human-override retries (counter resets to 0).
        reset_retry_count=False for automatic retries (counter increments).
        """
        from datetime import datetime, timezone, timedelta
        new_id = str(uuid.uuid4())
        new_retry_count = 0 if reset_retry_count else (source_task.get("retry_count", 0) + 1)
        retry_at = None
        if retry_delay_s and retry_delay_s > 0:
            retry_at = datetime.now(timezone.utc) + timedelta(seconds=retry_delay_s)

        self.conn.execute("""
            INSERT INTO task_runs (
                task_run_id, run_id, task_id,
                task_graph_artifact_id,
                attempt_number, status,
                agent_type,
                resolved_dependencies,
                input_artifact_ids,
                promoted_outputs,
                retry_count,
                retry_at,
                agent_routing_key,
                context_snapshot,
                previous_attempt_id
            ) VALUES (%s, %s, %s, %s, %s, 'PENDING', %s, %s, '{}', '[]',
                      %s, %s, %s, %s, %s)
        """, (
            new_id, run_id, source_task["task_id"],
            source_task.get("task_graph_artifact_id"),
            (source_task.get("attempt_number", 1) + 1),
            source_task.get("agent_type"),
            source_task.get("resolved_dependencies") or [],
            new_retry_count,
            retry_at,
            source_task.get("agent_routing_key"),
            source_task.get("context_snapshot"),
            source_task.get("task_run_id"),
        ))
        return new_id

    def get_by_id(self, task_run_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM task_runs WHERE task_run_id = %s", (task_run_id,)
        ).fetchone()
        return dict(row) if row else None
```

- [ ] **Step 3.4: Create `src/ai_dev_system/db/repos/escalations.py`**

```python
# src/ai_dev_system/db/repos/escalations.py
import uuid
import psycopg
import psycopg.types.json
from typing import Optional


class EscalationRepo:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    def upsert_open(
        self,
        run_id: str,
        task_run_id: str,
        reason: str,
        options: list[str],
    ) -> str:
        """Insert an OPEN escalation or return existing one's ID (idempotent).
        The named constraint uq_escalation_open_dedup prevents duplicates.
        """
        # Try insert; on conflict return existing
        self.conn.execute("""
            INSERT INTO escalations (run_id, task_run_id, reason, options)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT ON CONSTRAINT uq_escalation_open_dedup
            DO NOTHING
        """, (run_id, task_run_id, reason, psycopg.types.json.Jsonb(options)))

        row = self.conn.execute("""
            SELECT escalation_id FROM escalations
            WHERE run_id = %s AND task_run_id = %s AND reason = %s AND status = 'OPEN'
        """, (run_id, task_run_id, reason)).fetchone()
        return row["escalation_id"] if row else None

    def get_open(self, run_id: str) -> list[dict]:
        rows = self.conn.execute("""
            SELECT * FROM escalations
            WHERE run_id = %s AND status = 'OPEN'
            ORDER BY created_at ASC
        """, (run_id,)).fetchall()
        return [dict(r) for r in rows]

    def get_and_lock(self, escalation_id: str) -> Optional[dict]:
        """SELECT FOR UPDATE — must be inside a transaction."""
        row = self.conn.execute("""
            SELECT * FROM escalations
            WHERE escalation_id = %s
            FOR UPDATE
        """, (escalation_id,)).fetchone()
        return dict(row) if row else None

    def mark_resolved(self, escalation_id: str, resolution: str) -> int:
        result = self.conn.execute("""
            UPDATE escalations
            SET status = 'RESOLVED', resolution = %s, resolved_at = now()
            WHERE escalation_id = %s AND status = 'OPEN'
        """, (resolution, escalation_id))
        return result.rowcount
```

- [ ] **Step 3.5: Run tests to verify they pass**

```bash
pytest tests/unit/test_repos.py -v
```

Expected: all 7 tests PASS.

- [ ] **Step 3.6: Run full test suite**

```bash
pytest tests/ -x -q
```

Expected: all tests PASS.

- [ ] **Step 3.7: Commit**

```bash
git add src/ai_dev_system/db/repos/task_runs.py \
        src/ai_dev_system/db/repos/escalations.py \
        tests/unit/test_repos.py
git commit -m "feat: add mark_failed_final, create_retry to TaskRunRepo; add EscalationRepo"
```

---

## Task 4: Materializer

**Files:**
- Create: `src/ai_dev_system/engine/materializer.py`
- Create: `tests/unit/test_materializer.py`

- [ ] **Step 4.1: Write failing tests**

Create `tests/unit/test_materializer.py`:

```python
import json
import uuid
import pytest
from ai_dev_system.engine.materializer import materialize_task_runs, _build_context, ArtifactResolutionError, _resolve_artifact_paths


def test_materializer_creates_pending_task_runs(conn, seed_run, seed_graph_artifact, config):
    materialize_task_runs(conn, seed_run, seed_graph_artifact, config)
    rows = conn.execute(
        "SELECT task_id, status, retry_count FROM task_runs WHERE run_id = %s ORDER BY task_id",
        (seed_run,)
    ).fetchall()
    assert len(rows) == 2
    assert {r["task_id"] for r in rows} == {"TASK-PARSE", "TASK-DESIGN"}
    assert all(r["status"] == "PENDING" for r in rows)
    assert all(r["retry_count"] == 0 for r in rows)


def test_materializer_sets_run_status_running_execution(conn, seed_run, seed_graph_artifact, config):
    conn.execute("UPDATE runs SET status = 'RUNNING_PHASE_3' WHERE run_id = %s", (seed_run,))
    materialize_task_runs(conn, seed_run, seed_graph_artifact, config)
    status = conn.execute(
        "SELECT status FROM runs WHERE run_id = %s", (seed_run,)
    ).scalar()
    assert status == "RUNNING_EXECUTION"


def test_materializer_is_idempotent(conn, seed_run, seed_graph_artifact, config):
    materialize_task_runs(conn, seed_run, seed_graph_artifact, config)
    materialize_task_runs(conn, seed_run, seed_graph_artifact, config)  # second call
    count = conn.execute(
        "SELECT COUNT(*) FROM task_runs WHERE run_id = %s", (seed_run,)
    ).scalar()
    assert count == 2   # no duplicates


def test_materializer_resolves_dependencies(conn, seed_run, seed_graph_artifact, config):
    materialize_task_runs(conn, seed_run, seed_graph_artifact, config)
    design = conn.execute(
        "SELECT resolved_dependencies FROM task_runs WHERE run_id = %s AND task_id = 'TASK-DESIGN'",
        (seed_run,)
    ).fetchone()
    assert "TASK-PARSE" in (design["resolved_dependencies"] or [])


def test_build_context_returns_snapshot():
    task = {
        "id": "TASK-PARSE", "phase": "parse_spec", "type": "design",
        "agent_type": "SpecAnalyst",
        "objective": "Parse all specs", "description": "Detailed desc",
        "done_definition": "All parsed", "verification_steps": ["step1"],
        "required_inputs": ["spec.md"], "expected_outputs": ["summary.json"],
    }
    ctx = _build_context(task)
    assert ctx["task_id"] == "TASK-PARSE"
    assert ctx["required_inputs"] == ["spec.md"]
    assert ctx["verification_steps"] == ["step1"]


def test_resolve_artifact_paths_raises_on_missing(conn, seed_run):
    context = {
        "task_id": "TASK-IMPL",
        "required_inputs": ["some_nonexistent_artifact.md"],
    }
    with pytest.raises(ArtifactResolutionError):
        _resolve_artifact_paths(conn, seed_run, context)


def test_resolve_artifact_paths_returns_empty_for_no_inputs(conn, seed_run):
    context = {"task_id": "TASK-PARSE", "required_inputs": []}
    result = _resolve_artifact_paths(conn, seed_run, context)
    assert result["required_inputs"] == []
```

- [ ] **Step 4.2: Run tests to verify they fail**

```bash
pytest tests/unit/test_materializer.py -v
```

Expected: `ModuleNotFoundError: No module named 'ai_dev_system.engine.materializer'`

- [ ] **Step 4.2a: Add `get()` to `ArtifactRepo`**

`materializer.py` needs `ArtifactRepo.get(artifact_id)`. Check whether it already exists:

```bash
grep -n "def get" src/ai_dev_system/db/repos/artifacts.py
```

If not found, add to `src/ai_dev_system/db/repos/artifacts.py`:

```python
    def get(self, artifact_id: str) -> Optional[dict]:
        """Fetch a single artifact by ID. Returns None if not found."""
        row = self.conn.execute(
            "SELECT * FROM artifacts WHERE artifact_id = %s", (artifact_id,)
        ).fetchone()
        return dict(row) if row else None
```

Verify `ArtifactRepo.__init__` accepts a `conn` parameter — check the class definition:

```bash
grep -n "class ArtifactRepo\|def __init__" src/ai_dev_system/db/repos/artifacts.py | head -5
```

If `ArtifactRepo` uses a different constructor pattern (e.g., class methods), adapt the materializer instantiation (`artifact_repo = ArtifactRepo(conn)`) to match.

- [ ] **Step 4.3: Implement `src/ai_dev_system/engine/materializer.py`**

```python
# src/ai_dev_system/engine/materializer.py
import copy
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import psycopg
import psycopg.types.json

from ai_dev_system.config import Config
from ai_dev_system.db.repos.artifacts import ArtifactRepo
from ai_dev_system.db.repos.events import EventRepo

logger = logging.getLogger(__name__)


class ArtifactResolutionError(Exception):
    """Required input artifact could not be resolved to a filesystem path."""


def materialize_task_runs(
    conn: psycopg.Connection,
    run_id: str,
    graph_artifact_id: str,
    config: Config,
) -> None:
    """Load approved task graph → create PENDING task_runs. Safe to call multiple times.

    Idempotency: SELECT FOR UPDATE inside transaction, INSERT ON CONFLICT DO NOTHING.
    Both guards together prevent duplicates even under concurrent callers.
    """
    artifact_repo = ArtifactRepo(conn)
    event_repo = EventRepo(conn)
    # Note: both repos take a single `conn` parameter in their __init__.
    # Verify with: grep -n "def __init__" src/ai_dev_system/db/repos/events.py
    # If EventRepo uses a different signature, adapt accordingly.

    # Read graph from promoted artifact path (before transaction — I/O outside TX is fine)
    artifact = artifact_repo.get(graph_artifact_id)
    if artifact is None:
        raise ValueError(f"Artifact {graph_artifact_id} not found")
    graph_path = os.path.join(artifact["content_ref"], "task_graph.json")
    with open(graph_path) as f:
        graph = json.load(f)

    atomic_tasks = [t for t in graph["tasks"] if t.get("execution_type") == "atomic"]

    # All inserts + status update in one transaction.
    # Lock the run row first to serialize concurrent materializer calls.
    # The SAVEPOINT allows the caller (runner.py) to wrap this in its own TX.
    conn.execute("SAVEPOINT materializer_start")

    # Idempotency guard: lock run row FOR UPDATE, then check count.
    # FOR UPDATE ensures two concurrent callers cannot both read count=0.
    conn.execute("SELECT run_id FROM runs WHERE run_id = %s FOR UPDATE", (run_id,))
    existing = conn.execute("""
        SELECT COUNT(*) FROM task_runs
        WHERE run_id = %s AND task_graph_artifact_id = %s
    """, (run_id, graph_artifact_id)).scalar()

    if existing and existing > 0:
        logger.info("materialize_task_runs: already materialized for run %s, skipping", run_id)
        conn.execute("RELEASE SAVEPOINT materializer_start")
        return

    for task in atomic_tasks:
        conn.execute("""
            INSERT INTO task_runs (
                task_run_id, run_id, task_id,
                task_graph_artifact_id,
                attempt_number, status,
                resolved_dependencies,
                retry_count,
                agent_routing_key,
                context_snapshot,
                materialized_at,
                input_artifact_ids,
                promoted_outputs
            ) VALUES (
                gen_random_uuid(), %s, %s,
                %s,
                1, 'PENDING',
                %s,
                0,
                %s,
                %s,
                now(),
                '{}',
                '[]'
            )
            ON CONFLICT (run_id, task_id, attempt_number) DO NOTHING
        """, (
            run_id,
            task["id"],
            graph_artifact_id,
            task.get("deps", []),
            task.get("agent_type"),
            psycopg.types.json.Jsonb(_build_context(task)),
        ))

    conn.execute("""
        UPDATE runs
        SET status = 'RUNNING_EXECUTION', last_activity_at = now()
        WHERE run_id = %s AND status IN ('CREATED', 'RUNNING_PHASE_3', 'RUNNING_PHASE_2A')
    """, (run_id,))

    event_repo.insert(run_id, "PHASE_STARTED", "system",
                      payload={"phase": "execution", "task_count": len(atomic_tasks)})

    conn.execute("RELEASE SAVEPOINT materializer_start")
    logger.info("Materialized %d tasks for run %s", len(atomic_tasks), run_id)


def _build_context(task: dict) -> dict:
    """Immutable snapshot stored at materialization time.
    required_inputs stores logical names; paths resolved at agent dispatch.
    """
    return {
        "task_id": task["id"],
        "phase": task.get("phase", ""),
        "type": task.get("type", ""),
        "agent_type": task.get("agent_type", ""),
        "objective": task.get("objective", ""),
        "description": task.get("description", ""),
        "done_definition": task.get("done_definition", ""),
        "verification_steps": list(task.get("verification_steps", [])),
        "required_inputs": list(task.get("required_inputs", [])),
        "expected_outputs": list(task.get("expected_outputs", [])),
    }


def _resolve_artifact_paths(
    conn: psycopg.Connection,
    run_id: str,
    context_snapshot: dict,
) -> dict:
    """Enrich context_snapshot.required_inputs with real artifact paths.

    For each logical input name, tries to match against current_artifacts keys.
    Returns a copy of context_snapshot with required_inputs as list of dicts:
      [{"name": "...", "artifact_id": "...", "path": "..."}]

    Raises ArtifactResolutionError if a required input cannot be resolved.
    Retryable (EXECUTION_ERROR) — upstream artifact may not yet be promoted.
    """
    required = context_snapshot.get("required_inputs", [])
    if not required:
        ctx = copy.deepcopy(context_snapshot)
        ctx["required_inputs"] = []
        return ctx

    current = conn.execute(
        "SELECT current_artifacts FROM runs WHERE run_id = %s", (run_id,)
    ).scalar() or {}

    resolved = []
    for logical_name in required:
        artifact_id = _match_artifact(logical_name, current)
        if artifact_id is None:
            raise ArtifactResolutionError(
                f"Required input '{logical_name}' not in current_artifacts for run {run_id}. "
                f"Upstream task may not have completed yet."
            )
        artifact = conn.execute(
            "SELECT artifact_id, content_ref FROM artifacts WHERE artifact_id = %s",
            (artifact_id,)
        ).fetchone()
        if artifact is None:
            raise ArtifactResolutionError(
                f"Artifact {artifact_id} referenced by '{logical_name}' not found."
            )
        resolved.append({
            "name": logical_name,
            "artifact_id": str(artifact["artifact_id"]),
            "path": artifact["content_ref"],
        })

    ctx = copy.deepcopy(context_snapshot)
    ctx["required_inputs"] = resolved
    return ctx


def _match_artifact(logical_name: str, current_artifacts: dict) -> Optional[str]:
    """Map a logical input name to an artifact_id from current_artifacts.
    v1: keyword matching. v2: explicit mapping table in task graph.
    """
    name_lower = logical_name.lower().replace("_", "").replace(".", "").replace("-", "")
    for key, artifact_id in current_artifacts.items():
        if artifact_id:
            key_clean = key.replace("_id", "").replace("_", "")
            if key_clean in name_lower or name_lower in key_clean:
                return str(artifact_id)
    return None
```

**Note:** The `ArtifactRepo.get()` method may not exist yet. Add a `get` method to `ArtifactRepo` if needed:

```python
# In src/ai_dev_system/db/repos/artifacts.py — add:
    def get(self, artifact_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM artifacts WHERE artifact_id = %s", (artifact_id,)
        ).fetchone()
        return dict(row) if row else None
```

Note: `ArtifactRepo.__init__` takes `conn`. Check if it's `ArtifactRepo(conn)` or a standalone function. Update materializer to instantiate correctly.

- [ ] **Step 4.4: Run tests to verify they pass**

```bash
pytest tests/unit/test_materializer.py -v
```

Expected: all 7 tests PASS.

- [ ] **Step 4.5: Run full suite**

```bash
pytest tests/ -x -q
```

Expected: all PASS.

- [ ] **Step 4.6: Commit**

```bash
git add src/ai_dev_system/engine/materializer.py \
        src/ai_dev_system/db/repos/artifacts.py \
        tests/unit/test_materializer.py
git commit -m "feat: add materializer - graph to task_runs with idempotency"
```

---

## Task 5: HeartbeatThread

**Files:**
- Create: `src/ai_dev_system/engine/heartbeat.py`
- Create: `tests/unit/test_heartbeat.py`

- [ ] **Step 5.1: Write failing tests**

Create `tests/unit/test_heartbeat.py`:

```python
import time
import threading
import psycopg
import pytest
from ai_dev_system.engine.heartbeat import HeartbeatThread


def test_heartbeat_updates_heartbeat_at(conn, seed_run, seed_task_run):
    """HeartbeatThread updates heartbeat_at on the task_run."""
    conn.execute(
        "UPDATE task_runs SET status = 'RUNNING', worker_id = 'w1' WHERE task_run_id = %s",
        (seed_task_run,)
    )
    conn.commit()  # commit so heartbeat thread can see it

    calls = []

    def factory():
        c = psycopg.connect(conn.info.dsn, autocommit=True,
                            row_factory=psycopg.rows.dict_row)
        calls.append(c)
        return c

    hb = HeartbeatThread(conn_factory=factory, task_run_id=seed_task_run, interval_s=0.05)
    hb.start()
    time.sleep(0.2)
    hb.stop()

    assert len(calls) >= 1  # at least one heartbeat fired


def test_heartbeat_stops_cleanly(conn, seed_run, seed_task_run):
    """stop() terminates the thread within timeout."""
    def factory():
        return psycopg.connect(conn.info.dsn, autocommit=True,
                               row_factory=psycopg.rows.dict_row)

    hb = HeartbeatThread(conn_factory=factory, task_run_id=seed_task_run, interval_s=60)
    hb.start()
    assert hb.is_alive()
    hb.stop()
    assert not hb.is_alive()


def test_heartbeat_does_not_crash_on_db_error():
    """HeartbeatThread is non-fatal — bad DB connection does not kill the thread."""
    error_count = []

    def bad_factory():
        raise psycopg.OperationalError("connection refused")

    hb = HeartbeatThread(conn_factory=bad_factory, task_run_id="fake-id", interval_s=0.05)
    hb.start()
    time.sleep(0.15)
    hb.stop()
    assert not hb.is_alive()  # stopped cleanly despite errors
```

- [ ] **Step 5.2: Run tests to verify they fail**

```bash
pytest tests/unit/test_heartbeat.py -v
```

Expected: `ModuleNotFoundError: No module named 'ai_dev_system.engine.heartbeat'`

- [ ] **Step 5.3: Implement `src/ai_dev_system/engine/heartbeat.py`**

```python
# src/ai_dev_system/engine/heartbeat.py
import logging
import threading
from typing import Callable

import psycopg

logger = logging.getLogger(__name__)


class HeartbeatThread(threading.Thread):
    """Per-task heartbeat. Lives only while agent is executing.
    Receives conn_factory (not conn) — creates and closes a short-lived
    connection each tick, so the worker thread's connection is not shared.
    Non-fatal: any DB error is logged and swallowed.
    """

    def __init__(
        self,
        conn_factory: Callable[[], psycopg.Connection],
        task_run_id: str,
        interval_s: float = 30.0,
    ):
        super().__init__(daemon=True, name=f"hb-{task_run_id[:8]}")
        self._stop_event = threading.Event()
        self.conn_factory = conn_factory
        self.task_run_id = task_run_id
        self.interval_s = interval_s

    def run(self) -> None:
        while not self._stop_event.wait(self.interval_s):
            conn = None
            try:
                conn = self.conn_factory()
                conn.execute("""
                    UPDATE task_runs SET heartbeat_at = now()
                    WHERE task_run_id = %s AND status = 'RUNNING'
                """, (self.task_run_id,))
                if hasattr(conn, "commit"):
                    conn.commit()
            except Exception:
                logger.warning(
                    "HeartbeatThread: failed to update heartbeat for %s",
                    self.task_run_id, exc_info=True
                )
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass

    def stop(self) -> None:
        """Signal thread to stop and wait up to 5 seconds."""
        self._stop_event.set()
        self.join(timeout=5)
        if self.is_alive():
            logger.warning("HeartbeatThread did not stop cleanly for %s", self.task_run_id)
```

- [ ] **Step 5.4: Run tests to verify they pass**

```bash
pytest tests/unit/test_heartbeat.py -v
```

Expected: all 3 PASS (note: test_heartbeat_updates_heartbeat_at requires a commit-capable connection; adjust fixture if needed).

- [ ] **Step 5.5: Run full suite**

```bash
pytest tests/ -x -q
```

- [ ] **Step 5.6: Commit**

```bash
git add src/ai_dev_system/engine/heartbeat.py tests/unit/test_heartbeat.py
git commit -m "feat: add HeartbeatThread with conn_factory pattern"
```

---

## Task 6: Background Jobs

**Files:**
- Create: `src/ai_dev_system/engine/background.py`
- Create: `tests/unit/test_background_jobs.py`

- [ ] **Step 6.1: Write failing tests**

Create `tests/unit/test_background_jobs.py`:

```python
import uuid
import time
import pytest
from datetime import datetime, timezone, timedelta
from ai_dev_system.engine.background import mark_ready_tasks, recover_dead_tasks, check_completion
from ai_dev_system.config import Config


def _insert_task(conn, run_id, task_id, status, deps=None, retry_at=None, retry_count=0):
    tid = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO task_runs (
            task_run_id, run_id, task_id, attempt_number, status,
            agent_type, input_artifact_ids, resolved_dependencies, promoted_outputs,
            retry_count, retry_at
        ) VALUES (%s, %s, %s, 1, %s, 'agent', '{}', %s, '[]', %s, %s)
    """, (tid, run_id, task_id, status, deps or [], retry_count, retry_at))
    return tid


def test_mark_ready_tasks_no_deps(conn, seed_run):
    _insert_task(conn, seed_run, "TASK-A", "PENDING", deps=[])
    count = mark_ready_tasks(conn, seed_run)
    assert count >= 1
    row = conn.execute(
        "SELECT status FROM task_runs WHERE run_id = %s AND task_id = 'TASK-A'", (seed_run,)
    ).fetchone()
    assert row["status"] == "READY"


def test_mark_ready_tasks_respects_retry_at(conn, seed_run):
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    _insert_task(conn, seed_run, "TASK-A", "PENDING", deps=[], retry_at=future)
    count = mark_ready_tasks(conn, seed_run)
    assert count == 0
    row = conn.execute(
        "SELECT status FROM task_runs WHERE run_id = %s AND task_id = 'TASK-A'", (seed_run,)
    ).fetchone()
    assert row["status"] == "PENDING"  # not promoted yet


def test_mark_ready_tasks_waits_for_dep(conn, seed_run):
    _insert_task(conn, seed_run, "TASK-A", "PENDING", deps=["TASK-PARSE"])
    _insert_task(conn, seed_run, "TASK-PARSE", "PENDING", deps=[])
    # TASK-A cannot be ready until TASK-PARSE is SUCCESS/SKIPPED
    count = mark_ready_tasks(conn, seed_run)
    # Only TASK-PARSE becomes READY (no deps)
    statuses = {
        r["task_id"]: r["status"]
        for r in conn.execute(
            "SELECT task_id, status FROM task_runs WHERE run_id = %s", (seed_run,)
        ).fetchall()
    }
    assert statuses["TASK-PARSE"] == "READY"
    assert statuses["TASK-A"] == "PENDING"


def test_check_completion_marks_success(conn, seed_run):
    _insert_task(conn, seed_run, "TASK-A", "SUCCESS")
    conn.execute("UPDATE runs SET status = 'RUNNING_EXECUTION' WHERE run_id = %s", (seed_run,))
    check_completion(conn, seed_run)
    status = conn.execute(
        "SELECT status FROM runs WHERE run_id = %s", (seed_run,)
    ).scalar()
    assert status == "SUCCESS"


def test_check_completion_paused_when_failed_and_blocked(conn, seed_run):
    _insert_task(conn, seed_run, "TASK-A", "FAILED_FINAL")
    _insert_task(conn, seed_run, "TASK-B", "BLOCKED_BY_FAILURE", deps=["TASK-A"])
    conn.execute("UPDATE runs SET status = 'RUNNING_EXECUTION' WHERE run_id = %s", (seed_run,))
    check_completion(conn, seed_run)
    status = conn.execute(
        "SELECT status FROM runs WHERE run_id = %s", (seed_run,)
    ).scalar()
    assert status == "PAUSED_FOR_DECISION"


def test_check_completion_paused_on_leaf_failure(conn, seed_run):
    """Leaf task failure (no downstream) also pauses the run."""
    _insert_task(conn, seed_run, "TASK-A", "FAILED_FINAL")
    # No downstream tasks
    conn.execute("UPDATE runs SET status = 'RUNNING_EXECUTION' WHERE run_id = %s", (seed_run,))
    check_completion(conn, seed_run)
    status = conn.execute(
        "SELECT status FROM runs WHERE run_id = %s", (seed_run,)
    ).scalar()
    assert status == "PAUSED_FOR_DECISION"


def test_recover_dead_tasks_creates_retry(conn, config, seed_run):
    tid = _insert_task(conn, seed_run, "TASK-A", "RUNNING")
    # Make heartbeat stale by setting it far in the past
    conn.execute(
        "UPDATE task_runs SET heartbeat_at = now() - interval '300 seconds', "
        "worker_id = 'dead-worker', retry_count = 0 WHERE task_run_id = %s",
        (tid,)
    )
    recover_dead_tasks(conn, seed_run, config)
    row = conn.execute(
        "SELECT status FROM task_runs WHERE task_run_id = %s", (tid,)
    ).fetchone()
    assert row["status"] == "FAILED_RETRYABLE"
    # A retry row should have been created
    count = conn.execute(
        "SELECT COUNT(*) FROM task_runs WHERE run_id = %s AND task_id = 'TASK-A'", (seed_run,)
    ).scalar()
    assert count == 2
```

- [ ] **Step 6.2: Run tests to verify they fail**

```bash
pytest tests/unit/test_background_jobs.py -v
```

Expected: `ModuleNotFoundError: No module named 'ai_dev_system.engine.background'`

- [ ] **Step 6.3: Implement `src/ai_dev_system/engine/background.py`**

```python
# src/ai_dev_system/engine/background.py
import logging
import threading
from typing import Optional

import psycopg

from ai_dev_system.config import Config
from ai_dev_system.db.repos.events import EventRepo
from ai_dev_system.db.repos.task_runs import TaskRunRepo

logger = logging.getLogger(__name__)


def background_loop(
    run_id: str,
    config: Config,
    stop_event: threading.Event,
    conn_factory,
) -> None:
    """Background thread: recover → ready → completion, every poll_interval_s.
    All three jobs run in one transaction per cycle.
    """
    conn = conn_factory()
    try:
        while not stop_event.is_set():
            try:
                conn.execute("BEGIN")
                recover_dead_tasks(conn, run_id, config)
                mark_ready_tasks(conn, run_id)
                check_completion(conn, run_id)
                conn.execute("COMMIT")
            except Exception:
                logger.exception("Background loop error for run %s", run_id)
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
            stop_event.wait(timeout=config.poll_interval_s)
    finally:
        conn.close()


def mark_ready_tasks(conn: psycopg.Connection, run_id: str) -> int:
    """PENDING tasks whose deps are all SUCCESS/SKIPPED → READY (atomic UPDATE).
    Respects retry_at: tasks with future retry_at stay PENDING.
    Returns count of tasks promoted.
    """
    event_repo = EventRepo(conn)
    rows = conn.execute("""
        UPDATE task_runs t
        SET status = 'READY'
        WHERE t.run_id = %s
          AND t.status = 'PENDING'
          AND (t.retry_at IS NULL OR t.retry_at <= now())
          AND NOT EXISTS (
              SELECT 1 FROM task_runs dep
              WHERE dep.run_id = t.run_id
                AND dep.task_id = ANY(t.resolved_dependencies)
                AND dep.status NOT IN ('SUCCESS', 'SKIPPED')
          )
        RETURNING task_run_id
    """, (run_id,)).fetchall()

    for row in rows:
        event_repo.insert(run_id, "TASK_READY", "system", task_run_id=row["task_run_id"])

    return len(rows)


def recover_dead_tasks(
    conn: psycopg.Connection,
    run_id: str,
    config: Config,
) -> None:
    """Detect RUNNING tasks with stale heartbeat → create retry or mark FAILED_FINAL."""
    repo = TaskRunRepo(conn)

    stale = conn.execute("""
        SELECT task_run_id, task_id, attempt_number, retry_count, worker_id
        FROM task_runs
        WHERE run_id = %s
          AND status = 'RUNNING'
          AND worker_id IS NOT NULL
          AND heartbeat_at < now() - interval '1 second' * %s
        FOR UPDATE SKIP LOCKED
    """, (run_id, config.heartbeat_timeout_s)).fetchall()

    for task in stale:
        task = dict(task)
        max_env = config.retry_policy["ENVIRONMENT_ERROR"]["max_retries"]
        delay = config.retry_policy["ENVIRONMENT_ERROR"]["retry_delay_s"]

        if task["retry_count"] < max_env:
            repo.mark_failed_retryable(
                task["task_run_id"], "ENVIRONMENT_ERROR", "worker_heartbeat_timeout"
            )
            repo.create_retry(run_id, task,
                              retry_delay_s=delay, reset_retry_count=False)
            logger.warning("Dead worker detected: task %s rescheduled", task["task_id"])
        else:
            repo.mark_failed_final(
                task["task_run_id"], "ENVIRONMENT_ERROR", "worker_heartbeat_timeout"
            )
            # Import here to avoid circular; propagate_failure is in failure.py
            from ai_dev_system.engine.failure import propagate_failure
            propagate_failure(conn, run_id,
                              failed_task_id=task["task_id"],
                              failed_task_run_id=task["task_run_id"])
            logger.error("Dead worker: task %s exhausted retries → FAILED_FINAL", task["task_id"])


def check_completion(conn: psycopg.Connection, run_id: str) -> None:
    """Detect run SUCCESS or PAUSED_FOR_DECISION."""
    event_repo = EventRepo(conn)

    counts = conn.execute("""
        SELECT
            COUNT(*) FILTER (WHERE status = 'SUCCESS')            AS success_count,
            COUNT(*) FILTER (WHERE status = 'FAILED_FINAL')       AS failed_final_count,
            COUNT(*) FILTER (WHERE status = 'BLOCKED_BY_FAILURE') AS blocked_count,
            COUNT(*) FILTER (WHERE status = 'READY')              AS ready_count,
            COUNT(*) FILTER (WHERE status = 'RUNNING')            AS running_count,
            COUNT(*) FILTER (WHERE status = 'PENDING')            AS pending_count
        FROM task_runs
        WHERE run_id = %s
    """, (run_id,)).fetchone()

    active_count = counts["ready_count"] + counts["running_count"] + counts["pending_count"]

    if (active_count == 0
            and counts["failed_final_count"] == 0
            and counts["blocked_count"] == 0):
        # All done, no failures
        rows = conn.execute("""
            UPDATE runs SET status = 'SUCCESS', completed_at = now()
            WHERE run_id = %s AND status = 'RUNNING_EXECUTION'
        """, (run_id,)).rowcount
        if rows > 0:
            event_repo.insert(run_id, "RUN_COMPLETED", "system",
                              payload={"outcome": "SUCCESS"})
            logger.info("Run %s completed successfully", run_id)

    elif (active_count == 0
          and counts["failed_final_count"] > 0
          and counts["running_count"] == 0
          and counts["ready_count"] == 0):
        # Stuck: nothing can run. Covers both blocked tasks AND leaf failures.
        rows = conn.execute("""
            UPDATE runs SET status = 'PAUSED_FOR_DECISION'
            WHERE run_id = %s AND status = 'RUNNING_EXECUTION'
        """, (run_id,)).rowcount
        if rows > 0:
            logger.warning("Run %s paused for human decision", run_id)

    elif (active_count == 0
          and counts["blocked_count"] > 0
          and counts["failed_final_count"] == 0):
        # Defensive: impossible in correct execution, but catch it
        logger.error(
            "Run %s: inconsistent state — %d BLOCKED but 0 FAILED_FINAL. Forcing PAUSED.",
            run_id, counts["blocked_count"]
        )
        conn.execute("""
            UPDATE runs SET status = 'PAUSED_FOR_DECISION'
            WHERE run_id = %s AND status = 'RUNNING_EXECUTION'
        """, (run_id,))
```

- [ ] **Step 6.4: Run tests to verify they pass**

```bash
pytest tests/unit/test_background_jobs.py -v
```

Expected: all 7 tests PASS.

- [ ] **Step 6.5: Run full suite**

```bash
pytest tests/ -x -q
```

- [ ] **Step 6.6: Commit**

```bash
git add src/ai_dev_system/engine/background.py tests/unit/test_background_jobs.py
git commit -m "feat: add background jobs - mark_ready, recover_dead, check_completion"
```

---

## Task 7: Failure Propagation

**Files:**
- Create: `src/ai_dev_system/engine/failure.py`
- Create: `tests/unit/test_failure.py`

- [ ] **Step 7.1: Write failing tests**

Create `tests/unit/test_failure.py`:

```python
import uuid
import pytest
from ai_dev_system.engine.failure import propagate_failure, _handle_failure
from ai_dev_system.config import Config


def _insert_task(conn, run_id, task_id, status, deps=None):
    tid = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO task_runs (
            task_run_id, run_id, task_id, attempt_number, status,
            agent_type, input_artifact_ids, resolved_dependencies, promoted_outputs,
            retry_count, worker_id, locked_at, heartbeat_at, started_at
        ) VALUES (%s, %s, %s, 1, %s, 'agent', '{}', %s, '[]', 0,
                  'w1', now(), now(), now())
    """, (tid, run_id, task_id, status, deps or []))
    return tid


def test_propagate_failure_blocks_direct_child(conn, seed_run):
    parent_id = _insert_task(conn, seed_run, "TASK-A", "FAILED_FINAL")
    child_id = _insert_task(conn, seed_run, "TASK-B", "PENDING", deps=["TASK-A"])
    propagate_failure(conn, seed_run, "TASK-A", parent_id)
    row = conn.execute(
        "SELECT status FROM task_runs WHERE task_run_id = %s", (child_id,)
    ).fetchone()
    assert row["status"] == "BLOCKED_BY_FAILURE"


def test_propagate_failure_bfs_blocks_grandchild(conn, seed_run):
    """BFS: A fails → B blocked → C blocked."""
    a_id = _insert_task(conn, seed_run, "TASK-A", "FAILED_FINAL")
    b_id = _insert_task(conn, seed_run, "TASK-B", "PENDING", deps=["TASK-A"])
    c_id = _insert_task(conn, seed_run, "TASK-C", "PENDING", deps=["TASK-B"])
    propagate_failure(conn, seed_run, "TASK-A", a_id)
    for tid in (b_id, c_id):
        row = conn.execute(
            "SELECT status FROM task_runs WHERE task_run_id = %s", (tid,)
        ).fetchone()
        assert row["status"] == "BLOCKED_BY_FAILURE", f"Expected BLOCKED for {tid}"


def test_propagate_failure_does_not_overwrite_terminal(conn, seed_run):
    """BFS skips SUCCESS and SKIPPED nodes."""
    a_id = _insert_task(conn, seed_run, "TASK-A", "FAILED_FINAL")
    # B already succeeded; should not be affected
    _insert_task(conn, seed_run, "TASK-B", "SUCCESS", deps=["TASK-A"])
    propagate_failure(conn, seed_run, "TASK-A", a_id)
    row = conn.execute(
        "SELECT status FROM task_runs WHERE run_id = %s AND task_id = 'TASK-B'", (seed_run,)
    ).fetchone()
    assert row["status"] == "SUCCESS"


def test_propagate_failure_creates_escalation(conn, seed_run):
    a_id = _insert_task(conn, seed_run, "TASK-A", "RUNNING")
    conn.execute(
        "UPDATE task_runs SET status = 'FAILED_FINAL' WHERE task_run_id = %s", (a_id,)
    )
    propagate_failure(conn, seed_run, "TASK-A", a_id)
    esc = conn.execute(
        "SELECT * FROM escalations WHERE run_id = %s", (seed_run,)
    ).fetchone()
    assert esc is not None
    assert esc["status"] == "OPEN"
    assert esc["reason"] == "TASK_FAILURE"


def test_handle_failure_creates_retry_when_under_max(conn, seed_run, config):
    task_id = _insert_task(conn, seed_run, "TASK-A", "RUNNING")
    task = {"task_run_id": task_id, "task_id": "TASK-A", "run_id": seed_run,
            "retry_count": 0, "attempt_number": 1,
            "agent_type": "agent", "resolved_dependencies": [],
            "task_graph_artifact_id": None, "agent_routing_key": None,
            "context_snapshot": None}
    _handle_failure(conn, config, task, "exploded", "w1", seed_run, "EXECUTION_ERROR")
    row = conn.execute(
        "SELECT status FROM task_runs WHERE task_run_id = %s", (task_id,)
    ).fetchone()
    assert row["status"] == "FAILED_RETRYABLE"
    # new retry row exists
    count = conn.execute(
        "SELECT COUNT(*) FROM task_runs WHERE run_id = %s AND task_id = 'TASK-A'",
        (seed_run,)
    ).scalar()
    assert count == 2


def test_handle_failure_marks_final_when_max_exceeded(conn, seed_run, config):
    max_retries = config.retry_policy["EXECUTION_ERROR"]["max_retries"]
    task_id = _insert_task(conn, seed_run, "TASK-A", "RUNNING")
    task = {"task_run_id": task_id, "task_id": "TASK-A", "run_id": seed_run,
            "retry_count": max_retries,  # already at max
            "attempt_number": max_retries + 1,
            "agent_type": "agent", "resolved_dependencies": [],
            "task_graph_artifact_id": None, "agent_routing_key": None,
            "context_snapshot": None}
    _handle_failure(conn, config, task, "still broken", "w1", seed_run, "EXECUTION_ERROR")
    row = conn.execute(
        "SELECT status FROM task_runs WHERE task_run_id = %s", (task_id,)
    ).fetchone()
    assert row["status"] == "FAILED_FINAL"
```

- [ ] **Step 7.2: Run tests to verify they fail**

```bash
pytest tests/unit/test_failure.py -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 7.3: Implement `src/ai_dev_system/engine/failure.py`**

```python
# src/ai_dev_system/engine/failure.py
# Imported by worker.py: from ai_dev_system.engine.failure import _handle_failure, propagate_failure
import logging

import psycopg

from ai_dev_system.config import Config
from ai_dev_system.db.repos.escalations import EscalationRepo
from ai_dev_system.db.repos.events import EventRepo
from ai_dev_system.db.repos.task_runs import TaskRunRepo

logger = logging.getLogger(__name__)


def propagate_failure(
    conn: psycopg.Connection,
    run_id: str,
    failed_task_id: str,
    failed_task_run_id: str,
) -> None:
    """BFS: mark all downstream tasks BLOCKED_BY_FAILURE.
    Skips terminal states (SUCCESS, SKIPPED, FAILED_*, ABORTED).
    Raises an escalation (deduplicated by UNIQUE constraint).
    Must be called inside an open transaction.
    """
    event_repo = EventRepo(conn)
    esc_repo = EscalationRepo(conn)

    visited: set[str] = set()
    queue = [failed_task_id]

    while queue:
        current_id = queue.pop(0)

        dependents = conn.execute("""
            SELECT task_run_id, task_id, status
            FROM task_runs
            WHERE run_id = %s
              AND %s = ANY(resolved_dependencies)
              AND status NOT IN (
                  'SUCCESS', 'SKIPPED', 'FAILED_FINAL',
                  'FAILED_RETRYABLE', 'ABORTED'
              )
        """, (run_id, current_id)).fetchall()

        for dep in dependents:
            if dep["task_id"] in visited:
                continue
            visited.add(dep["task_id"])

            rows = conn.execute("""
                UPDATE task_runs
                SET status = 'BLOCKED_BY_FAILURE',
                    error_detail = %s
                WHERE task_run_id = %s
                  AND status IN ('PENDING', 'READY')
            """, (f"dependency_failed:{failed_task_id}", dep["task_run_id"])).rowcount

            if rows > 0:
                logger.debug("Blocked %s due to failure of %s", dep["task_id"], failed_task_id)

            queue.append(dep["task_id"])

    # Raise escalation — UNIQUE constraint deduplicates concurrent calls
    esc_repo.upsert_open(
        run_id=run_id,
        task_run_id=failed_task_run_id,
        reason="TASK_FAILURE",
        options=["retry", "skip", "abort"],
    )
    event_repo.insert(run_id, "ESCALATION_RAISED", "system",
                      task_run_id=failed_task_run_id,
                      payload={"failed_task_id": failed_task_id})


def _handle_failure(
    conn: psycopg.Connection,
    config: Config,
    task: dict,
    error: str,
    worker_id: str,
    run_id: str,
    error_type: str,
) -> None:
    """Mark task FAILED_RETRYABLE (with retry) or FAILED_FINAL (propagate).
    Called inside its own transaction (worker.py opens/commits).
    """
    repo = TaskRunRepo(conn)
    event_repo = EventRepo(conn)

    retry_cfg = config.retry_policy.get(error_type, config.retry_policy["UNKNOWN"])
    can_retry = task.get("retry_count", 0) < retry_cfg["max_retries"]

    if can_retry:
        repo.mark_failed_retryable(task["task_run_id"], error_type, error)
        repo.create_retry(
            run_id, task,
            retry_delay_s=retry_cfg.get("retry_delay_s", 0),
            reset_retry_count=False,
        )
        event_repo.insert(run_id, "TASK_RETRYING", f"worker:{worker_id}",
                          task_run_id=task["task_run_id"],
                          payload={"error_type": error_type, "error": error})
        logger.info("Task %s attempt %d failed (%s), retrying",
                    task["task_id"], task.get("attempt_number", 1), error_type)
    else:
        repo.mark_failed_final(task["task_run_id"], error_type, error)
        event_repo.insert(run_id, "TASK_FAILED", f"worker:{worker_id}",
                          task_run_id=task["task_run_id"],
                          payload={"error_type": error_type, "error": error})
        propagate_failure(conn, run_id,
                          failed_task_id=task["task_id"],
                          failed_task_run_id=task["task_run_id"])
        logger.warning("Task %s exhausted retries → FAILED_FINAL", task["task_id"])
```

- [ ] **Step 7.4: Run tests to verify they pass**

```bash
pytest tests/unit/test_failure.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 7.5: Run full suite**

```bash
pytest tests/ -x -q
```

- [ ] **Step 7.6: Commit**

```bash
git add src/ai_dev_system/engine/failure.py tests/unit/test_failure.py
git commit -m "feat: add failure propagation - BFS blocking and handle_failure with retry"
```

---

## Task 8: Escalation Resolution

**Files:**
- Create: `src/ai_dev_system/engine/escalation.py`
- Create: `tests/unit/test_escalation.py`

- [ ] **Step 8.1: Write failing tests**

Create `tests/unit/test_escalation.py`:

```python
import uuid
import pytest
from ai_dev_system.engine.escalation import resolve_escalation
from ai_dev_system.db.repos.escalations import EscalationRepo


def _insert_failed_final(conn, run_id, task_id="TASK-A"):
    tid = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO task_runs (
            task_run_id, run_id, task_id, attempt_number, status,
            agent_type, input_artifact_ids, resolved_dependencies, promoted_outputs,
            retry_count
        ) VALUES (%s, %s, %s, 3, 'FAILED_FINAL', 'agent', '{}', '{}', '[]', 2)
    """, (tid, run_id, task_id))
    return tid


def _insert_blocked(conn, run_id, task_id, deps):
    tid = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO task_runs (
            task_run_id, run_id, task_id, attempt_number, status,
            agent_type, input_artifact_ids, resolved_dependencies, promoted_outputs,
            retry_count
        ) VALUES (%s, %s, %s, 1, 'BLOCKED_BY_FAILURE', 'agent', '{}', %s, '[]', 0)
    """, (tid, run_id, task_id, deps))
    return tid


def _open_escalation(conn, run_id, task_run_id):
    repo = EscalationRepo(conn)
    return repo.upsert_open(run_id, task_run_id, "TASK_FAILURE", ["retry", "skip", "abort"])


def test_resolve_skip_marks_task_skipped(conn, seed_run):
    conn.execute("UPDATE runs SET status = 'PAUSED_FOR_DECISION' WHERE run_id = %s", (seed_run,))
    failed_id = _insert_failed_final(conn, seed_run)
    esc_id = _open_escalation(conn, seed_run, failed_id)
    resolve_escalation(conn, esc_id, "skip", seed_run)
    row = conn.execute(
        "SELECT status FROM task_runs WHERE task_run_id = %s", (failed_id,)
    ).fetchone()
    assert row["status"] == "SKIPPED"


def test_resolve_skip_unblocks_downstream(conn, seed_run):
    conn.execute("UPDATE runs SET status = 'PAUSED_FOR_DECISION' WHERE run_id = %s", (seed_run,))
    failed_id = _insert_failed_final(conn, seed_run, "TASK-A")
    blocked_id = _insert_blocked(conn, seed_run, "TASK-B", ["TASK-A"])
    esc_id = _open_escalation(conn, seed_run, failed_id)
    resolve_escalation(conn, esc_id, "skip", seed_run)
    row = conn.execute(
        "SELECT status FROM task_runs WHERE task_run_id = %s", (blocked_id,)
    ).fetchone()
    assert row["status"] == "PENDING"


def test_resolve_skip_resumes_run(conn, seed_run):
    conn.execute("UPDATE runs SET status = 'PAUSED_FOR_DECISION' WHERE run_id = %s", (seed_run,))
    failed_id = _insert_failed_final(conn, seed_run)
    esc_id = _open_escalation(conn, seed_run, failed_id)
    resolve_escalation(conn, esc_id, "skip", seed_run)
    status = conn.execute(
        "SELECT status FROM runs WHERE run_id = %s", (seed_run,)
    ).scalar()
    assert status == "RUNNING_EXECUTION"


def test_resolve_retry_creates_new_attempt(conn, seed_run):
    conn.execute("UPDATE runs SET status = 'PAUSED_FOR_DECISION' WHERE run_id = %s", (seed_run,))
    failed_id = _insert_failed_final(conn, seed_run, "TASK-A")
    esc_id = _open_escalation(conn, seed_run, failed_id)
    resolve_escalation(conn, esc_id, "retry", seed_run)
    count = conn.execute(
        "SELECT COUNT(*) FROM task_runs WHERE run_id = %s AND task_id = 'TASK-A'", (seed_run,)
    ).scalar()
    assert count == 2  # original + retry
    new_row = conn.execute(
        "SELECT retry_count FROM task_runs WHERE run_id = %s AND task_id = 'TASK-A' "
        "AND status = 'PENDING'", (seed_run,)
    ).fetchone()
    assert new_row["retry_count"] == 0  # reset for human override


def test_resolve_abort_marks_run_failed(conn, seed_run):
    conn.execute("UPDATE runs SET status = 'PAUSED_FOR_DECISION' WHERE run_id = %s", (seed_run,))
    failed_id = _insert_failed_final(conn, seed_run)
    esc_id = _open_escalation(conn, seed_run, failed_id)
    resolve_escalation(conn, esc_id, "abort", seed_run)
    status = conn.execute(
        "SELECT status FROM runs WHERE run_id = %s", (seed_run,)
    ).scalar()
    assert status == "FAILED"


def test_resolve_is_idempotent(conn, seed_run):
    """Calling resolve_escalation twice on same escalation is safe."""
    conn.execute("UPDATE runs SET status = 'PAUSED_FOR_DECISION' WHERE run_id = %s", (seed_run,))
    failed_id = _insert_failed_final(conn, seed_run)
    esc_id = _open_escalation(conn, seed_run, failed_id)
    resolve_escalation(conn, esc_id, "skip", seed_run)
    resolve_escalation(conn, esc_id, "skip", seed_run)  # second call — no-op
```

- [ ] **Step 8.2: Run tests to verify they fail**

```bash
pytest tests/unit/test_escalation.py -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 8.3: Implement `src/ai_dev_system/engine/escalation.py`**

```python
# src/ai_dev_system/engine/escalation.py
import logging

import psycopg

from ai_dev_system.db.repos.escalations import EscalationRepo
from ai_dev_system.db.repos.events import EventRepo
from ai_dev_system.db.repos.task_runs import TaskRunRepo

logger = logging.getLogger(__name__)


def resolve_escalation(
    conn: psycopg.Connection,
    escalation_id: str,
    resolution: str,   # 'retry' | 'skip' | 'abort'
    run_id: str,
) -> None:
    """Human resolves a stuck run. Idempotent — second call on resolved esc is a no-op.
    Must be called outside any existing transaction (opens its own).
    """
    esc_repo = EscalationRepo(conn)
    repo = TaskRunRepo(conn)
    event_repo = EventRepo(conn)

    conn.execute("BEGIN")
    try:
        esc = esc_repo.get_and_lock(escalation_id)
        if esc is None or esc["status"] != "OPEN":
            conn.execute("ROLLBACK")
            return  # Already resolved — idempotent

        esc_repo.mark_resolved(escalation_id, resolution)
        event_repo.insert(run_id, "HUMAN_DECISION_RECORDED", "human",
                          task_run_id=esc["task_run_id"],
                          payload={"resolution": resolution,
                                   "escalation_id": escalation_id})

        task = repo.get_by_id(esc["task_run_id"])

        if resolution == "retry":
            repo.create_retry(run_id, task, retry_delay_s=0, reset_retry_count=True)
            _unblock_downstream_bfs(conn, run_id, task["task_id"])
            # Resume run
            conn.execute("""
                UPDATE runs SET status = 'RUNNING_EXECUTION'
                WHERE run_id = %s AND status = 'PAUSED_FOR_DECISION'
            """, (run_id,))

        elif resolution == "skip":
            conn.execute("""
                UPDATE task_runs SET status = 'SKIPPED'
                WHERE task_run_id = %s AND status = 'FAILED_FINAL'
            """, (esc["task_run_id"],))
            _unblock_downstream_bfs(conn, run_id, task["task_id"])
            conn.execute("""
                UPDATE runs SET status = 'RUNNING_EXECUTION'
                WHERE run_id = %s AND status = 'PAUSED_FOR_DECISION'
            """, (run_id,))

        elif resolution == "abort":
            conn.execute("""
                UPDATE task_runs SET status = 'ABORTED'
                WHERE run_id = %s
                  AND status NOT IN ('SUCCESS', 'FAILED_FINAL', 'SKIPPED', 'ABORTED')
            """, (run_id,))
            conn.execute("""
                UPDATE runs SET status = 'FAILED', completed_at = now()
                WHERE run_id = %s
            """, (run_id,))
            event_repo.insert(run_id, "RUN_ABORTED", "human",
                              payload={"reason": "human_abort_on_escalation"})
            conn.execute("COMMIT")
            return  # Don't resume

        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _unblock_downstream_bfs(
    conn: psycopg.Connection,
    run_id: str,
    unblocked_task_id: str,
) -> None:
    """BFS: BLOCKED_BY_FAILURE → PENDING for tasks downstream of unblocked_task_id.
    Only unblocks if the task has NO OTHER FAILED_FINAL dependencies.
    mark_ready_tasks() then evaluates which PENDING tasks are actually READY.
    """
    visited: set[str] = set()
    queue = [unblocked_task_id]

    while queue:
        current = queue.pop(0)
        blocked = conn.execute("""
            SELECT task_run_id, task_id
            FROM task_runs
            WHERE run_id = %s
              AND %s = ANY(resolved_dependencies)
              AND status = 'BLOCKED_BY_FAILURE'
        """, (run_id, current)).fetchall()

        for dep in blocked:
            if dep["task_id"] in visited:
                continue
            visited.add(dep["task_id"])

            # Only unblock if this task has no remaining FAILED_FINAL deps
            rows_updated = conn.execute("""
                UPDATE task_runs SET status = 'PENDING', error_detail = NULL
                WHERE task_run_id = %s
                  AND status = 'BLOCKED_BY_FAILURE'
                  AND NOT EXISTS (
                      SELECT 1 FROM task_runs other_dep
                      WHERE other_dep.run_id = %s
                        AND other_dep.task_id = ANY(
                            (SELECT resolved_dependencies FROM task_runs
                             WHERE task_run_id = %s)
                        )
                        AND other_dep.status = 'FAILED_FINAL'
                  )
            """, (dep["task_run_id"], run_id, dep["task_run_id"])).rowcount

            if rows_updated > 0:
                queue.append(dep["task_id"])
            # If 0: task still has other FAILED_FINAL deps → stays BLOCKED, don't recurse
```

- [ ] **Step 8.4: Run tests to verify they pass**

```bash
pytest tests/unit/test_escalation.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 8.5: Run full suite**

```bash
pytest tests/ -x -q
```

- [ ] **Step 8.6: Commit**

```bash
git add src/ai_dev_system/engine/escalation.py tests/unit/test_escalation.py
git commit -m "feat: add escalation resolution - retry/skip/abort with BFS unblock"
```

---

## Task 9: Worker Loop + Runner Entry Point

**Files:**
- Modify: `src/ai_dev_system/engine/worker.py` (add `worker_loop()`)
- Create: `src/ai_dev_system/engine/runner.py`
- Modify: `src/ai_dev_system/agents/base.py` (extend Agent protocol)
- Modify: `src/ai_dev_system/agents/stub.py` (update stub)

The new `worker_loop()` is different from the existing `run_worker_loop()`:
- Takes `stop_event` (threading.Event) instead of `max_iterations`
- Uses HeartbeatThread per task
- Has abort guard (checks run status before promoting)
- Catches `ArtifactResolutionError`
- Does NOT call `resolve_dependencies()` inline (background handles that)

- [ ] **Step 9.1: Extend agent protocol and stub**

In `src/ai_dev_system/agents/base.py`, update `AgentResult` to make `output_path` optional (set by worker, not required upfront), and add `context`/`timeout_s` to the Agent protocol:

```python
# src/ai_dev_system/agents/base.py
from dataclasses import dataclass, field
from typing import Optional, Protocol


@dataclass
class PromotedOutput:
    name: str
    artifact_type: str
    description: str = ""


@dataclass
class AgentResult:
    output_path: str
    promoted_outputs: list["PromotedOutput"] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.error is None


class Agent(Protocol):
    def run(
        self,
        task_id: str,
        output_path: str,
        promoted_outputs: list[PromotedOutput] = (),
        context: Optional[dict] = None,
        timeout_s: float = 3600.0,
    ) -> AgentResult:
        ...
```

Update `src/ai_dev_system/agents/stub.py` to accept new params:

```python
# src/ai_dev_system/agents/stub.py
import os
from ai_dev_system.agents.base import AgentResult, PromotedOutput
from typing import Optional


class StubAgent:
    """Test double that writes a dummy file and reports success."""

    def run(
        self,
        task_id: str,
        output_path: str,
        promoted_outputs=(),
        context: Optional[dict] = None,
        timeout_s: float = 3600.0,
    ) -> AgentResult:
        os.makedirs(output_path, exist_ok=True)
        with open(os.path.join(output_path, "output.txt"), "w") as f:
            f.write(f"stub output for {task_id}")
        return AgentResult(
            output_path=output_path,
            promoted_outputs=list(promoted_outputs),
        )
```

- [ ] **Step 9.2: Run existing tests to verify no regressions**

```bash
pytest tests/ -x -q
```

Expected: all PASS (Protocol changes are backward compatible).

- [ ] **Step 9.3: Add `worker_loop()` to worker.py**

Add to end of `src/ai_dev_system/engine/worker.py`:

```python
# ── New worker loop for runner.py ─────────────────────────────────────────────
import copy
import threading
import socket

from ai_dev_system.engine.heartbeat import HeartbeatThread
from ai_dev_system.engine.failure import _handle_failure
from ai_dev_system.engine.materializer import _resolve_artifact_paths, ArtifactResolutionError


def worker_loop(
    run_id: str,
    config: "Config",
    agent,
    stop_event: threading.Event,
    conn_factory,
) -> None:
    """New worker loop used by runner.py.
    Differences from run_worker_loop():
    - Uses stop_event instead of max_iterations
    - Runs HeartbeatThread per task
    - Checks run status before promoting (abort guard)
    - Catches ArtifactResolutionError
    - Does NOT call resolve_dependencies() (background thread handles that)

    Transaction management: conn_factory() must return autocommit=True connections.
    This avoids psycopg3's implicit transaction management interfering with explicit
    BEGIN/COMMIT/ROLLBACK calls (a long-lived autocommit=False conn would auto-start
    a transaction on the abort-guard SELECT, breaking the subsequent explicit BEGIN).
    runner.py creates the conn_factory with autocommit=True for this reason.
    """
    conn = conn_factory()
    worker_id = f"{socket.gethostname()}-{threading.get_ident()}"
    try:
        while not stop_event.is_set():
            # Abort guard: check run status at loop head (autocommit=True — no tx needed)
            run_status_row = conn.execute(
                "SELECT status FROM runs WHERE run_id = %s", (run_id,)
            ).fetchone()
            if run_status_row and run_status_row["status"] in ("ABORTED", "FAILED", "SUCCESS"):
                break

            # Pickup task
            task = None
            try:
                conn.execute("BEGIN")
                task = _pickup_with_dep_check(conn, config, run_id, worker_id)
                conn.execute("COMMIT")
            except Exception:
                logger.exception("Pickup error in worker_loop")
                conn.execute("ROLLBACK")
                stop_event.wait(timeout=min(config.poll_interval_s, 1.0))
                continue

            if task is None:
                stop_event.wait(timeout=min(config.poll_interval_s, 1.0))
                continue

            # Start heartbeat
            heartbeat = HeartbeatThread(
                conn_factory=conn_factory,
                task_run_id=task["task_run_id"],
                interval_s=config.heartbeat_interval_s,
            )
            heartbeat.start()
            result = None
            try:
                # Resolve artifact paths (retryable on failure)
                try:
                    context = _resolve_artifact_paths(conn, run_id,
                                                      task.get("context_snapshot") or {})
                except ArtifactResolutionError as e:
                    result = AgentResult(
                        output_path=task["temp_path"], error=str(e)
                    )
                else:
                    result = agent.run(
                        task_id=task["task_id"],
                        output_path=task["temp_path"],
                        promoted_outputs=task["promoted_outputs_parsed"],
                        context=copy.deepcopy(context),
                        timeout_s=config.task_timeout_s,
                    )
            except TimeoutError:
                result = AgentResult(
                    output_path=task["temp_path"], error="task_execution_timeout"
                )
            except Exception as e:
                result = AgentResult(output_path=task["temp_path"], error=str(e))
            finally:
                heartbeat.stop()

            # Abort guard before promoting
            run_status_row = conn.execute(
                "SELECT status FROM runs WHERE run_id = %s", (run_id,)
            ).fetchone()
            if run_status_row and run_status_row["status"] in ("ABORTED", "FAILED"):
                conn.execute("""
                    UPDATE task_runs SET status = 'ABORTED'
                    WHERE task_run_id = %s AND status = 'RUNNING'
                """, (task["task_run_id"],))
                conn.commit()
                break

            # Promote or handle failure
            try:
                conn.execute("BEGIN")
                if not result.success:
                    _handle_failure(conn, config, task, result.error or "unknown",
                                    worker_id, run_id,
                                    error_type="EXECUTION_ERROR")
                else:
                    _promote_success(conn, config, task, result, worker_id, run_id)
                conn.execute("COMMIT")
            except Exception:
                logger.exception("Promote/failure error for task %s", task["task_id"])
                conn.execute("ROLLBACK")
    finally:
        conn.close()


def _pickup_with_dep_check(conn, config, run_id, worker_id):
    """Pickup with double-check of deps at pickup time + run status guard."""
    from ai_dev_system.storage.paths import build_temp_path
    import os

    task = conn.execute("""
        SELECT tr.*
        FROM task_runs tr
        WHERE tr.run_id = %s
          AND tr.status = 'READY'
          AND NOT EXISTS (
              SELECT 1 FROM task_runs dep
              WHERE dep.run_id = tr.run_id
                AND dep.task_id = ANY(tr.resolved_dependencies)
                AND dep.status NOT IN ('SUCCESS', 'SKIPPED')
          )
        ORDER BY tr.retry_count ASC, tr.created_at ASC
        LIMIT 1
        FOR UPDATE SKIP LOCKED
    """, (run_id,)).fetchone()

    if task is None:
        return None

    # Run guard inside the lock
    run_status = conn.execute(
        "SELECT status FROM runs WHERE run_id = %s", (run_id,)
    ).scalar()
    if run_status != "RUNNING_EXECUTION":
        return None

    temp_path = build_temp_path(
        config.storage_root, run_id, task["task_id"], task["attempt_number"]
    )
    os.makedirs(temp_path, exist_ok=True)

    conn.execute("""
        UPDATE task_runs
        SET status = 'RUNNING', worker_id = %s,
            locked_at = now(), heartbeat_at = now(), started_at = now()
        WHERE task_run_id = %s
    """, (worker_id, task["task_run_id"]))

    EventRepo(conn).insert(run_id, "TASK_STARTED", f"worker:{worker_id}",
                           task_run_id=task["task_run_id"])

    import json as _json
    promoted_raw = task.get("promoted_outputs") or []
    if isinstance(promoted_raw, str):
        promoted_raw = _json.loads(promoted_raw)
    promoted_outputs = [PromotedOutput(**po) for po in promoted_raw]

    return dict(task) | {"temp_path": temp_path, "promoted_outputs_parsed": promoted_outputs}


def _promote_success(conn, config, task, result, worker_id, run_id):
    """Promote output and mark SUCCESS. Called inside transaction.

    Two paths:
    - Tasks WITH promoted_outputs: promote_output() calls mark_success() internally.
      Do NOT call mark_success() again — that would double-fire.
    - Tasks WITHOUT promoted_outputs: call mark_success() explicitly.
    In both cases, emit TASK_COMPLETED only once (guarded by rows > 0).
    """
    if task["promoted_outputs_parsed"]:
        # promote_output calls mark_success internally — do not duplicate
        for po in task["promoted_outputs_parsed"]:
            promote_output(conn, config, task, po, task["temp_path"])
        # TASK_COMPLETED event: promote_output already emits it; nothing more to do
    else:
        # No promoted outputs — mark success and emit event explicitly
        rows = TaskRunRepo(conn).mark_success(task["task_run_id"], task["temp_path"], None)
        if rows > 0:
            EventRepo(conn).insert(run_id, "TASK_COMPLETED", f"worker:{worker_id}",
                                   task_run_id=task["task_run_id"])
```

- [ ] **Step 9.4: Create `src/ai_dev_system/engine/runner.py`**

```python
# src/ai_dev_system/engine/runner.py
import logging
import threading
import time
from dataclasses import dataclass

import psycopg

from ai_dev_system.config import Config
from ai_dev_system.engine.background import background_loop
from ai_dev_system.engine.materializer import materialize_task_runs
from ai_dev_system.engine.worker import worker_loop

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    run_id: str
    status: str   # SUCCESS | PAUSED_FOR_DECISION | FAILED | ABORTED


def run_execution(
    run_id: str,
    graph_artifact_id: str,
    config: Config,
    agent,
    poll_interval_s: float = 5.0,
) -> ExecutionResult:
    """Full lifecycle: materialize → spawn threads → wait for terminal state.

    Args:
        run_id:              UUID of the run (status must be RUNNING_PHASE_3 or similar)
        graph_artifact_id:   UUID of TASK_GRAPH_APPROVED artifact
        config:              Config (must include heartbeat/poll/retry settings)
        agent:               Agent protocol implementation
        poll_interval_s:     Overrides config.poll_interval_s if provided

    Returns:
        ExecutionResult with final run status.
    """
    effective_config = config
    if poll_interval_s != 5.0:
        import dataclasses
        effective_config = dataclasses.replace(config, poll_interval_s=poll_interval_s)

    def conn_factory():
        """autocommit=True: worker_loop and background_loop manage transactions
        explicitly with BEGIN/COMMIT/ROLLBACK. This avoids psycopg3's implicit
        transaction management interfering with the long-lived connections used
        by these threads."""
        return psycopg.connect(
            effective_config.database_url,
            autocommit=True,
            row_factory=psycopg.rows.dict_row,
        )

    def tx_conn_factory():
        """autocommit=False: for short-lived transactional work (materialization)."""
        return psycopg.connect(
            effective_config.database_url,
            autocommit=False,
            row_factory=psycopg.rows.dict_row,
        )

    # Step 1: Materialize (idempotent, safe to run multiple times)
    with tx_conn_factory() as conn:
        conn.execute("BEGIN")
        try:
            materialize_task_runs(conn, run_id, graph_artifact_id, effective_config)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    stop_event = threading.Event()

    worker_thread = threading.Thread(
        target=worker_loop,
        args=(run_id, effective_config, agent, stop_event, conn_factory),
        name=f"worker-{run_id[:8]}",
        daemon=True,
    )
    background_thread = threading.Thread(
        target=background_loop,
        args=(run_id, effective_config, stop_event, conn_factory),
        name=f"bg-{run_id[:8]}",
        daemon=True,
    )

    worker_thread.start()
    background_thread.start()

    final_status = _wait_for_terminal_state(run_id, effective_config, conn_factory)

    stop_event.set()
    worker_thread.join(timeout=30)
    background_thread.join(timeout=10)

    if worker_thread.is_alive():
        logger.warning("Worker thread did not stop cleanly for run %s", run_id)

    return ExecutionResult(run_id=run_id, status=final_status)


def _wait_for_terminal_state(
    run_id: str,
    config: Config,
    conn_factory,
) -> str:
    """Poll runs.status until terminal. Returns the terminal status string."""
    terminal = {"SUCCESS", "FAILED", "ABORTED", "PAUSED_FOR_DECISION"}
    with conn_factory() as conn:
        while True:
            row = conn.execute(
                "SELECT status FROM runs WHERE run_id = %s", (run_id,)
            ).fetchone()
            if row and row["status"] in terminal:
                return row["status"]
            time.sleep(config.poll_interval_s / 2)
```

- [ ] **Step 9.5: Run unit tests to verify no regressions**

```bash
pytest tests/ -x -q
```

Expected: all PASS. (Integration tests for runner.py come in Task 10.)

- [ ] **Step 9.6: Commit**

```bash
git add src/ai_dev_system/engine/worker.py \
        src/ai_dev_system/engine/runner.py \
        src/ai_dev_system/agents/base.py \
        src/ai_dev_system/agents/stub.py
git commit -m "feat: add worker_loop and run_execution entry point"
```

---

## Task 10: Integration Tests

**Files:**
- Create: `tests/integration/test_runner_golden.py`
- Create: `tests/integration/test_runner_escalation.py`

These tests exercise the full `run_execution()` pipeline end-to-end against a real DB.

- [ ] **Step 10.1: Write golden path integration test**

Create `tests/integration/test_runner_golden.py`:

```python
"""Scenario A: happy path — all tasks succeed.
Graph: PARSE → DESIGN (2 tasks, DESIGN depends on PARSE).
"""
import json
import uuid
import pytest
from ai_dev_system.engine.runner import run_execution
from ai_dev_system.agents.stub import StubAgent
from ai_dev_system.config import Config


def _setup_run(conn, project_id, tmp_path):
    """Create run + graph artifact backed by a file."""
    run_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO runs (run_id, project_id, status, title, current_artifacts, metadata)
        VALUES (%s, %s, 'RUNNING_PHASE_3', 'Golden Test', '{}', '{}')
    """, (run_id, project_id))

    graph = {
        "graph_version": 1,
        "tasks": [
            {
                "id": "TASK-PARSE", "execution_type": "atomic",
                "phase": "parse_spec", "type": "design",
                "agent_type": "SpecAnalyst",
                "objective": "parse", "description": "", "done_definition": "done",
                "verification_steps": [], "deps": [],
                "required_inputs": [], "expected_outputs": ["parsed.json"],
            },
            {
                "id": "TASK-DESIGN", "execution_type": "atomic",
                "phase": "design_solution", "type": "design",
                "agent_type": "Architect",
                "objective": "design", "description": "", "done_definition": "done",
                "verification_steps": [], "deps": ["TASK-PARSE"],
                "required_inputs": [], "expected_outputs": ["design.md"],
            },
        ],
    }
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    (graph_dir / "task_graph.json").write_text(json.dumps(graph))

    artifact_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO artifacts (
            artifact_id, run_id, artifact_type, version, status, created_by,
            input_artifact_ids, content_ref, content_checksum, content_size
        ) VALUES (%s, %s, 'TASK_GRAPH_APPROVED', 1, 'ACTIVE', 'system',
                  '{}', %s, 'stub', 0)
    """, (artifact_id, run_id, str(graph_dir)))
    conn.execute("""
        UPDATE runs SET current_artifacts = jsonb_set(
            current_artifacts, '{task_graph_approved_id}', to_jsonb(%s::text)
        ) WHERE run_id = %s
    """, (artifact_id, run_id))
    conn.commit()
    return run_id, artifact_id


@pytest.mark.integration
def test_golden_run_completes_all_tasks(conn, config, project_id, tmp_path):
    """Full run: materialize → background resolves deps → worker executes → SUCCESS."""
    test_config = Config(
        storage_root=str(tmp_path / "storage"),
        database_url=config.database_url,
        poll_interval_s=0.1,
        heartbeat_interval_s=60.0,
        heartbeat_timeout_s=300.0,
    )
    run_id, artifact_id = _setup_run(conn, project_id, tmp_path)

    result = run_execution(
        run_id=run_id,
        graph_artifact_id=artifact_id,
        config=test_config,
        agent=StubAgent(),
        poll_interval_s=0.1,
    )

    assert result.status == "SUCCESS", f"Expected SUCCESS, got {result.status}"

    task_statuses = {
        r["task_id"]: r["status"]
        for r in conn.execute(
            "SELECT task_id, status FROM task_runs WHERE run_id = %s", (run_id,)
        ).fetchall()
    }
    assert task_statuses.get("TASK-PARSE") == "SUCCESS"
    assert task_statuses.get("TASK-DESIGN") == "SUCCESS"

    events = [
        r["event_type"]
        for r in conn.execute(
            "SELECT event_type FROM events WHERE run_id = %s ORDER BY occurred_at",
            (run_id,)
        ).fetchall()
    ]
    assert "PHASE_STARTED" in events
    assert events.count("TASK_COMPLETED") >= 2
    assert "RUN_COMPLETED" in events
```

- [ ] **Step 10.2: Write escalation integration test**

Create `tests/integration/test_runner_escalation.py`:

```python
"""Scenario C: failure → escalation → skip → downstream runs → SUCCESS."""
import json
import uuid
import threading
import time
import pytest
from ai_dev_system.engine.runner import run_execution
from ai_dev_system.engine.escalation import resolve_escalation
from ai_dev_system.db.repos.escalations import EscalationRepo
from ai_dev_system.agents.base import AgentResult
from ai_dev_system.config import Config


class FailingAgent:
    """Fails TASK-IMPL.BACKEND always; succeeds everything else."""

    def run(self, task_id, output_path, promoted_outputs=(), context=None, timeout_s=3600.0):
        import os
        os.makedirs(output_path, exist_ok=True)
        if task_id == "TASK-IMPL":
            return AgentResult(output_path=output_path, error="intentional failure")
        with open(os.path.join(output_path, "out.txt"), "w") as f:
            f.write(f"output of {task_id}")
        return AgentResult(output_path=output_path)


def _setup_failing_run(conn, project_id, tmp_path):
    run_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO runs (run_id, project_id, status, title, current_artifacts, metadata)
        VALUES (%s, %s, 'RUNNING_PHASE_3', 'Escalation Test', '{}', '{}')
    """, (run_id, project_id))

    graph = {
        "graph_version": 1,
        "tasks": [
            {
                "id": "TASK-PARSE", "execution_type": "atomic",
                "phase": "parse_spec", "type": "design", "agent_type": "agent",
                "objective": "", "description": "", "done_definition": "",
                "verification_steps": [], "deps": [],
                "required_inputs": [], "expected_outputs": [],
            },
            {
                "id": "TASK-IMPL", "execution_type": "atomic",
                "phase": "implement", "type": "coding", "agent_type": "agent",
                "objective": "", "description": "", "done_definition": "",
                "verification_steps": [], "deps": ["TASK-PARSE"],
                "required_inputs": [], "expected_outputs": [],
            },
            {
                "id": "TASK-VALIDATE", "execution_type": "atomic",
                "phase": "validate", "type": "testing", "agent_type": "agent",
                "objective": "", "description": "", "done_definition": "",
                "verification_steps": [], "deps": ["TASK-IMPL"],
                "required_inputs": [], "expected_outputs": [],
            },
        ],
    }
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    (graph_dir / "task_graph.json").write_text(json.dumps(graph))

    artifact_id = str(uuid.uuid4())
    conn.execute("""
        INSERT INTO artifacts (
            artifact_id, run_id, artifact_type, version, status, created_by,
            input_artifact_ids, content_ref, content_checksum, content_size
        ) VALUES (%s, %s, 'TASK_GRAPH_APPROVED', 1, 'ACTIVE', 'system',
                  '{}', %s, 'stub', 0)
    """, (artifact_id, run_id, str(graph_dir)))

    # Keep current_artifacts consistent with _setup_run in golden test.
    # Required so _resolve_artifact_paths finds the artifact if tasks ever
    # reference required_inputs by artifact type key.
    conn.execute("""
        UPDATE runs
        SET current_artifacts = jsonb_set(current_artifacts, '{task_graph_approved_id}',
                                          to_jsonb(%s::text))
        WHERE run_id = %s
    """, (artifact_id, run_id))

    conn.commit()
    return run_id, artifact_id


@pytest.mark.integration
def test_escalation_skip_resumes_to_success(config, project_id, tmp_path):
    """TASK-IMPL fails → BLOCKED VALIDATE → human skips → VALIDATE runs → SUCCESS."""
    import psycopg

    test_config = Config(
        storage_root=str(tmp_path / "storage"),
        database_url=config.database_url,
        poll_interval_s=0.1,
        heartbeat_interval_s=60.0,
        heartbeat_timeout_s=300.0,
        retry_policy={
            "EXECUTION_ERROR":    {"max_retries": 0, "retry_delay_s": 0},
            "ENVIRONMENT_ERROR":  {"max_retries": 0, "retry_delay_s": 0},
            "SPEC_AMBIGUITY":     {"max_retries": 0, "retry_delay_s": 0},
            "SPEC_CONTRADICTION": {"max_retries": 0, "retry_delay_s": 0},
            "UNKNOWN":            {"max_retries": 0, "retry_delay_s": 0},
        },
    )

    conn_direct = psycopg.connect(
        config.database_url, autocommit=True, row_factory=psycopg.rows.dict_row
    )

    run_id, artifact_id = _setup_failing_run(conn_direct, project_id, tmp_path)
    conn_direct.close()

    # Run in a thread — will pause at PAUSED_FOR_DECISION
    result_holder = {}

    def run():
        result_holder["result"] = run_execution(
            run_id=run_id,
            graph_artifact_id=artifact_id,
            config=test_config,
            agent=FailingAgent(),
            poll_interval_s=0.1,
        )

    t = threading.Thread(target=run, daemon=True)
    t.start()

    # Wait for PAUSED_FOR_DECISION
    resolve_conn = psycopg.connect(
        config.database_url, autocommit=False, row_factory=psycopg.rows.dict_row
    )
    for _ in range(50):
        time.sleep(0.2)
        row = resolve_conn.execute(
            "SELECT status FROM runs WHERE run_id = %s", (run_id,)
        ).fetchone()
        if row and row["status"] == "PAUSED_FOR_DECISION":
            break
    else:
        pytest.fail("Run never reached PAUSED_FOR_DECISION")

    # Find open escalation and skip it
    esc_repo = EscalationRepo(resolve_conn)
    open_escs = esc_repo.get_open(run_id)
    assert len(open_escs) == 1
    resolve_escalation(resolve_conn, open_escs[0]["escalation_id"], "skip", run_id)
    resolve_conn.close()

    # Wait for run to finish
    t.join(timeout=30)
    assert "result" in result_holder, "run_execution did not complete"
    assert result_holder["result"].status == "SUCCESS", \
        f"Expected SUCCESS after skip, got {result_holder['result'].status}"
```

- [ ] **Step 10.3: Run golden path test**

```bash
pytest tests/integration/test_runner_golden.py -v -m integration
```

Expected: PASS.

- [ ] **Step 10.4: Run escalation test**

```bash
pytest tests/integration/test_runner_escalation.py -v -m integration
```

Expected: PASS.

- [ ] **Step 10.5: Run full test suite**

```bash
pytest tests/ -x -q
```

Expected: all tests PASS.

- [ ] **Step 10.6: Commit**

```bash
git add tests/integration/test_runner_golden.py tests/integration/test_runner_escalation.py
git commit -m "test: integration tests for run_execution - golden path and escalation skip"
```

---

## Final Verification

- [ ] **Run complete test suite**

```bash
pytest tests/ -v --tb=short 2>&1 | tail -30
```

Expected: all unit and integration tests PASS. No regressions in existing tests.

- [ ] **Verify spec success criteria**

```
1. run_execution() completes 5-task graph → SUCCESS             ✓ test_golden_run_completes_all_tasks
2. Dead worker recovery (heartbeat timeout → retry)              ✓ test_recover_dead_tasks_creates_retry
3. Retry: EXECUTION_ERROR → new attempt, old row FAILED_RETRYABLE ✓ test_handle_failure_creates_retry_when_under_max
4. Failure BFS: FAILED_FINAL → downstream BLOCKED_BY_FAILURE    ✓ test_propagate_failure_bfs_blocks_grandchild
5. Escalation: PAUSED + skip resumes                            ✓ test_escalation_skip_resumes_to_success
6. Idempotency: double materialize → no duplicates              ✓ test_materializer_is_idempotent
7. Abort guard: no promotion after ABORTED/FAILED               ✓ abort guard in worker_loop (manual verify)
8. Thread safety: no shared connections                         ✓ conn_factory pattern throughout
```

- [ ] **Final commit**

```bash
git add -A
git commit -m "feat: execution runner v1 complete - materializer, threads, heartbeat, retry, escalation"
```
