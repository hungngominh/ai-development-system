import subprocess
from unittest.mock import patch, MagicMock
import pytest
from ai_dev_system.beads.sync import beads_sync

SAMPLE_GRAPH = {
    "tasks": [
        {"id": "T1", "objective": "Set up auth", "deps": []},
        {"id": "T2", "objective": "Build API", "deps": ["T1"]},
        {"id": "T3", "objective": "Write tests", "deps": ["T2"]},
    ]
}


def test_beads_sync_calls_bd_create_for_each_task():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr=b"")
        beads_sync("r1", SAMPLE_GRAPH, None)
        create_calls = [c for c in mock_run.call_args_list
                        if c.args[0][1] == "create"]
        assert len(create_calls) == 3


def test_beads_sync_adds_deps():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr=b"")
        beads_sync("r1", SAMPLE_GRAPH, None)
        dep_calls = [c for c in mock_run.call_args_list
                     if len(c.args[0]) > 1 and c.args[0][1] == "dep"]
        assert len(dep_calls) == 2  # T2→T1, T3→T2


def test_beads_sync_skips_when_bd_not_found():
    """If bd is not in PATH, skip entirely — no exception raised."""
    with patch("subprocess.run", side_effect=FileNotFoundError("bd not found")):
        beads_sync("r1", SAMPLE_GRAPH, None)  # should not raise


def test_beads_sync_logs_warning_on_nonzero_exit():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr=b"some error")
        beads_sync("r1", SAMPLE_GRAPH, None)  # should not raise
