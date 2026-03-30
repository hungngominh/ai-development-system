import os

ARTIFACT_TYPE_TO_KEY = {
    "INITIAL_BRIEF":        "initial_brief_id",
    "DEBATE_REPORT":        "debate_report_id",
    "DECISION_LOG":         "decision_log_id",
    "APPROVED_ANSWERS":     "approved_answers_id",
    "APPROVED_BRIEF":       "approved_brief_id",
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
