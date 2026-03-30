import json
import os
from pathlib import Path

from ai_dev_system.config import Config
from ai_dev_system.normalize import normalize_idea, validate_brief
from ai_dev_system.spec_bundle import generate_spec_bundle, validate_spec_bundle
from ai_dev_system.gate.core import run_gate_1
from ai_dev_system.agents.base import PromotedOutput
from ai_dev_system.db.repos.runs import RunRepo
from ai_dev_system.db.repos.task_runs import TaskRunRepo
from ai_dev_system.db.repos.events import EventRepo
from ai_dev_system.spec_bundle import SpecBundle
from ai_dev_system.storage.paths import build_temp_path
from ai_dev_system.storage.promote import promote_output


class PipelineAborted(Exception):
    """User rejected at a gate."""


class ValidationError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(f"Validation failed: {errors}")


def run_spec_pipeline(raw_idea: str, config: Config, conn, project_id: str, io) -> "SpecBundle":
    """Full pipeline: normalize -> gate 1 -> spec bundle.
    Synchronous, blocking. Each step creates DB records.
    """
    run_repo = RunRepo(conn)
    task_run_repo = TaskRunRepo(conn)
    event_repo = EventRepo(conn)

    run_id = run_repo.create(project_id=project_id, pipeline_type="spec_pipeline")

    # Step 1: Normalize
    brief = normalize_idea(raw_idea)
    errors = validate_brief(brief)
    if errors:
        raise ValidationError(errors)

    task_run = task_run_repo.create_sync(run_id, task_type="normalize_idea")
    task_run["input_artifact_ids"] = []
    event_repo.insert(run_id, "TASK_STARTED", "pipeline", task_run["task_run_id"])
    temp_path = _write_json_to_temp(config, task_run, brief)
    promoted = PromotedOutput(name="initial_brief", artifact_type="INITIAL_BRIEF",
                              description="Normalized idea brief")
    promote_output(conn, config, task_run, promoted, temp_path)

    # Step 2: Gate 1
    task_run = task_run_repo.create_sync(run_id, task_type="human_gate")
    task_run["input_artifact_ids"] = []
    event_repo.insert(run_id, "TASK_STARTED", "pipeline", task_run["task_run_id"])

    result = run_gate_1(brief, io)

    if result.status == "rejected":
        task_run_repo.mark_failed(task_run["task_run_id"], "EXECUTION_ERROR", "user_rejected")
        raise PipelineAborted("User rejected brief at Gate 1")

    errors = validate_brief(result.brief)
    if errors:
        task_run_repo.mark_failed(task_run["task_run_id"], "EXECUTION_ERROR", str(errors))
        raise ValidationError(errors)

    temp_path = _write_json_to_temp(config, task_run, result.brief)
    promoted = PromotedOutput(name="approved_brief", artifact_type="APPROVED_BRIEF",
                              description="Human-approved brief")
    promote_output(conn, config, task_run, promoted, temp_path)

    # Step 3: Spec Bundle
    task_run = task_run_repo.create_sync(run_id, task_type="generate_spec")
    task_run["input_artifact_ids"] = []
    event_repo.insert(run_id, "TASK_STARTED", "pipeline", task_run["task_run_id"])

    temp_path = build_temp_path(config.storage_root, run_id,
                                task_run["task_id"], task_run["attempt_number"])
    bundle = generate_spec_bundle(result.brief, Path(temp_path))
    validate_spec_bundle(bundle.root_dir)  # warnings only, don't fail

    promoted = PromotedOutput(name="spec_bundle", artifact_type="SPEC_BUNDLE",
                              description="5-file spec bundle")
    artifact_id = promote_output(conn, config, task_run, promoted, temp_path)

    # promote_output moves files to final artifact path; update bundle root_dir
    row = conn.execute(
        "SELECT content_ref FROM artifacts WHERE artifact_id = %s",
        (artifact_id,)
    ).fetchone()
    bundle = SpecBundle(version=bundle.version, root_dir=Path(row["content_ref"]),
                        files={name: Path(row["content_ref"]) / name for name in bundle.files})

    return bundle


def _write_json_to_temp(config: Config, task_run: dict, data: dict) -> str:
    """Write dict as JSON to temp path. Returns temp_path directory."""
    temp_path = build_temp_path(config.storage_root, task_run["run_id"],
                                task_run["task_id"], task_run["attempt_number"])
    os.makedirs(temp_path, exist_ok=True)
    with open(os.path.join(temp_path, f"{task_run['task_id']}.json"), "w") as f:
        json.dump(data, f, indent=2)
    return temp_path
