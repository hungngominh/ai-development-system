import json
import os
import shutil
from datetime import datetime, timezone
from typing import Optional

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
    Promotion protocol Steps 2-7 (Step 1 = task execution is caller responsibility).

    Step 1 (caller): agent writes output files into temp_output_path.
    Steps 2-7 (this function): validate, move, checksum, DB transaction.

    MUST be called inside an open transaction. Caller (worker.py) manages
    the transaction boundary - pickup is a separate transaction from promotion
    so that the task_run row lock is not held during agent execution.

    Returns artifact_id (str).
    """
    run_id = task_run["run_id"]
    task_run_id = task_run["task_run_id"]
    artifact_type = promoted_output.artifact_type

    # Step 2: Wait for stable output
    wait_until_stable(temp_output_path, poll_interval_ms=100, stable_duration_ms=200)

    # Step 3: Validate (v1: existence check only)
    if not os.path.exists(temp_output_path):
        raise FileNotFoundError(f"temp_output_path does not exist: {temp_output_path}")

    # Step 4: Disk space check (v1: skip)

    # Step 5: Two-phase atomic move
    # Lock version first so we can build the final path (and adjacent staging path)
    version_lock_repo = VersionLockRepo(conn)
    artifact_repo = ArtifactRepo(conn)
    task_run_repo = TaskRunRepo(conn)
    run_repo = RunRepo(conn)
    event_repo = EventRepo(conn)

    # 7a: Lock and get next version (must happen before building paths)
    next_version = version_lock_repo.lock_and_increment(run_id, artifact_type)

    final_path = build_artifact_path(config.storage_root, run_id, artifact_type, next_version)
    # staging is adjacent to final (same filesystem) — guarantees atomic rename
    staging_path = final_path + ".staging"

    os.makedirs(staging_path, exist_ok=False)
    for item in os.listdir(temp_output_path):
        shutil.move(os.path.join(temp_output_path, item), staging_path)

    staging_checksum, staging_size = checksum_artifact(staging_path)

    os.makedirs(os.path.dirname(final_path), exist_ok=True)
    os.rename(staging_path, final_path)

    # Step 6: Verify integrity after rename
    content_checksum, content_size = checksum_artifact(final_path)
    if content_checksum != staging_checksum or content_size != staging_size:
        raise IntegrityError(
            f"Integrity mismatch after rename: "
            f"checksum {staging_checksum!r} vs {content_checksum!r}, "
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
    # Recompute after marker is added
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

    # 7c: Supersede old active artifact of same type
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

    # 7e: Update task_run — idempotency guard in WHERE
    updated = task_run_repo.mark_success(task_run_id, final_path, artifact_id)
    if updated == 0:
        raise PromotionConflictError(f"task_run {task_run_id} was updated by another worker")

    # 7f: Update runs.current_artifacts (only for types that map to a key)
    artifact_key = ARTIFACT_TYPE_TO_KEY.get(artifact_type)
    if artifact_key is not None:
        run_repo.update_current_artifact(run_id, artifact_key, artifact_id)

    # 7g: Emit events
    event_repo.insert(run_id, "ARTIFACT_CREATED", "system", task_run_id,
                      {"artifact_id": artifact_id, "version": next_version})
    event_repo.insert(run_id, "TASK_COMPLETED", "system", task_run_id,
                      {"artifact_id": artifact_id})

    return artifact_id
