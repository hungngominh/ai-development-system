from ai_dev_system.storage.paths import (
    build_artifact_path,
    build_task_output_path,
    build_temp_path,
)


def test_artifact_path_format():
    path = build_artifact_path("/data", "abc-123", "SPEC_BUNDLE", 2)
    assert path.replace("\\", "/") == "/data/runs/abc-123/artifacts/spec_bundle/v2"


def test_artifact_path_type_lowercased():
    path = build_artifact_path("/data", "r1", "TASK_GRAPH_APPROVED", 1)
    assert "task_graph_approved" in path


def test_task_output_path():
    path = build_task_output_path("/data", "r1", "TASK-3", 2)
    assert path.replace("\\", "/") == "/data/runs/r1/tasks/TASK-3/attempt-2"


def test_temp_path():
    path = build_temp_path("/data", "r1", "TASK-3", 1)
    assert path.replace("\\", "/") == "/data/tmp/runs/r1/tasks/TASK-3/attempt-1"


def test_artifact_type_to_key_maps_correctly():
    from ai_dev_system.storage.paths import ARTIFACT_TYPE_TO_KEY
    assert ARTIFACT_TYPE_TO_KEY["SPEC_BUNDLE"] == "spec_bundle_id"
    assert ARTIFACT_TYPE_TO_KEY["EXECUTION_LOG"] is None
