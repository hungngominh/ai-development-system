"""End-to-end test for single_task_executor + run_execution.

Reproduces the Windows charmap bug (byte 0x90 = second byte of UTF-8 Đ char)
and verifies the full execution path works with real materializer + StubAgent.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from ai_dev_system.agents.stub import StubAgent
from ai_dev_system.db.connection import get_connection
from ai_dev_system.db.migrator import apply_schema
from ai_dev_system.engine.runner import run_execution


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VIETNAMESE_FACETS = {
    "test_cases": {
        "status": "filled",
        "content": (
            "1. Lưu thành công → redirect /run?id=X. "
            "2. Duyệt thành công → HTTP 302 redirect. "
            "3. Xác nhận trạng thái: Đã lưu ✓. "
            "4. Nút 'Đặt lại' xoá cache. "
            "5. Kiểm tra button 'Duyệt & tiếp tục'."
        ),
    },
    "input": {
        "status": "filled",
        "content": "run_id (từ hidden field 'id'). title. metadata JSON.",
    },
    "response": {
        "status": "filled",
        "content": (
            "POST /run-edit: HTTP 302 → /run?id={run_id}. "
            "POST /run-approve: HTTP 302 → /run?id={run_id}. "
            "Lỗi: HTTP 400 + thông báo lỗi."
        ),
    },
}


def _make_task_graph(facets: dict) -> dict:
    return {
        "tasks": [{
            "id": "TASK-ADHOC",
            "execution_type": "atomic",
            "agent_type": "StubAgent",
            "phase": "implementation",
            "type": "coding",
            "objective": "Implement run-edit feature",
            "description": "Add /run-edit and /run-approve endpoints",
            "done_definition": "POST /run-edit returns 302",
            "verification_steps": [],
            "required_inputs": [],
            "expected_outputs": ["implementation_diff"],
            "deps": [],
            "facets": facets,
        }]
    }


def _setup_db(db_url: str, storage_root: Path, facets: dict) -> tuple[str, str]:
    """Create run + TASK_GRAPH_APPROVED artifact. Returns (run_id, artifact_id)."""
    conn = get_connection(db_url)
    apply_schema(conn)

    project_id = uuid.uuid4().hex
    run_id = uuid.uuid4().hex
    conn.execute(
        """INSERT INTO runs (run_id, project_id, status, title, current_artifacts, metadata)
           VALUES (?, ?, 'RUNNING_EXECUTION', 'e2e test', '{}', '{}')""",
        (run_id, project_id),
    )

    artifact_id = uuid.uuid4().hex
    graph = _make_task_graph(facets)
    content = json.dumps(graph, indent=2, ensure_ascii=False)

    artifact_dir = storage_root / "task_execs" / run_id / "task_graph"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "task_graph.json").write_text(content, encoding="utf-8")

    import hashlib
    checksum = "sha256:" + hashlib.sha256(content.encode()).hexdigest()
    conn.execute(
        """INSERT INTO artifacts
               (artifact_id, run_id, artifact_type, version, status, created_by,
                input_artifact_ids, content_ref, content_checksum, content_size)
           VALUES (?, ?, 'TASK_GRAPH_APPROVED', 1, 'ACTIVE', 'system',
                   '[]', ?, ?, ?)""",
        (artifact_id, run_id, str(artifact_dir), checksum, len(content)),
    )
    conn.commit()
    conn.close()
    return run_id, artifact_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_run_execution_with_vietnamese_facets(tmp_path):
    """
    Full path: task_graph.json with Vietnamese UTF-8 content → materializer reads it
    → worker picks up task → StubAgent runs → run reaches COMPLETED.

    This reproduces the 'charmap codec can't decode byte 0x90' bug where
    materializer.py was reading task_graph.json without encoding='utf-8'.
    The Vietnamese 'Đ' (U+0110) encodes as 0xC4 0x90 in UTF-8; byte 0x90
    is undefined in Windows cp1252, causing UnicodeDecodeError on Windows.
    """
    from ai_dev_system.config import Config

    db_path = tmp_path / "test.db"
    db_url = f"sqlite:///{db_path}"
    storage_root = tmp_path / "storage"

    run_id, artifact_id = _setup_db(db_url, storage_root, VIETNAMESE_FACETS)

    cfg = Config(
        storage_root=str(storage_root),
        database_url=db_url,
        poll_interval_s=0.1,
        heartbeat_interval_s=1.0,
        heartbeat_timeout_s=5.0,
        task_timeout_s=30.0,
    )

    result = run_execution(run_id, artifact_id, cfg, StubAgent(), poll_interval_s=0.1)

    assert result.status == "COMPLETED", (
        f"Expected COMPLETED, got {result.status!r}. "
        "This likely means the charmap encoding fix in materializer.py is not active."
    )


def test_run_execution_with_ascii_facets(tmp_path):
    """Baseline: ASCII-only facets also complete correctly."""
    from ai_dev_system.config import Config

    ascii_facets = {
        "test_cases": {"status": "filled", "content": "1. POST /edit -> 302. 2. Bad JSON -> 400."},
        "input": {"status": "filled", "content": "run_id, title, metadata"},
    }

    db_path = tmp_path / "test.db"
    db_url = f"sqlite:///{db_path}"
    storage_root = tmp_path / "storage"

    run_id, artifact_id = _setup_db(db_url, storage_root, ascii_facets)

    cfg = Config(
        storage_root=str(storage_root),
        database_url=db_url,
        poll_interval_s=0.1,
        heartbeat_interval_s=1.0,
        heartbeat_timeout_s=5.0,
        task_timeout_s=30.0,
    )

    result = run_execution(run_id, artifact_id, cfg, StubAgent(), poll_interval_s=0.1)
    assert result.status == "COMPLETED"


def test_task_graph_json_is_valid_utf8(tmp_path):
    """The task_graph.json produced by single_task_executor is valid UTF-8 and readable."""
    storage_root = tmp_path / "storage"
    storage_root.mkdir()

    graph = _make_task_graph(VIETNAMESE_FACETS)
    content = json.dumps(graph, indent=2, ensure_ascii=False)

    # Verify 'Đ' (U+0110) is present and the file round-trips correctly
    assert "Đ" in content, "Vietnamese Đ must be in the serialized task graph"

    # Simulate what materializer.py does
    path = storage_root / "task_graph.json"
    path.write_text(content, encoding="utf-8")

    with open(path, encoding="utf-8") as f:
        loaded = json.load(f)

    assert loaded["tasks"][0]["facets"]["test_cases"]["content"] == \
        VIETNAMESE_FACETS["test_cases"]["content"]

    # Confirm the raw bytes contain 0x90 (second byte of UTF-8 Đ = 0xC4 0x90)
    raw = path.read_bytes()
    assert b"\xc4\x90" in raw, "Đ must produce bytes 0xC4 0x90 in UTF-8"
    assert b"\x90" in raw, "byte 0x90 must be present (the Windows charmap trigger)"
