import os
from pathlib import Path

import pytest

from ai_dev_system.config import apply_project_env


def test_overlays_env_and_returns_paths(tmp_path, monkeypatch):
    # Track these keys so monkeypatch restores them on teardown even though
    # apply_project_env mutates os.environ directly.
    monkeypatch.setenv("STORAGE_ROOT", "sentinel")
    monkeypatch.setenv("DATABASE_URL", "sentinel")
    repo = tmp_path / "repo"
    paths = apply_project_env(str(repo))
    assert os.environ["STORAGE_ROOT"] == paths.storage_root
    assert os.environ["DATABASE_URL"] == paths.database_url
    assert paths.storage_root == os.path.join(paths.repo_path, ".ai-dev", "state", "storage")
    assert Path(paths.root).is_dir()          # ensure=True created it


def test_blank_repo_raises(monkeypatch):
    monkeypatch.setenv("STORAGE_ROOT", "sentinel")
    with pytest.raises(ValueError):
        apply_project_env("   ")
