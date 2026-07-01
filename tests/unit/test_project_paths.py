import os
from pathlib import Path

import pytest

from ai_dev_system.config import ProjectPaths, resolve_project


def test_resolve_project_pure_paths_no_io(tmp_path):
    repo = tmp_path / "myrepo"  # does not exist on disk
    p = resolve_project(str(repo), ensure=False)
    assert isinstance(p, ProjectPaths)
    assert p.repo_path == os.path.abspath(str(repo))
    assert p.root == os.path.join(p.repo_path, ".ai-dev", "state")
    assert p.storage_root == os.path.join(p.root, "storage")
    assert p.database_url == f"sqlite:///{os.path.join(p.root, 'control.db')}"
    # ensure=False must not create anything
    assert not (repo / ".ai-dev").exists()


def test_resolve_project_two_repos_distinct_db(tmp_path):
    a = resolve_project(str(tmp_path / "a"), ensure=False)
    b = resolve_project(str(tmp_path / "b"), ensure=False)
    assert a.database_url != b.database_url


def test_resolve_project_blank_raises():
    with pytest.raises(ValueError):
        resolve_project("", ensure=False)
    with pytest.raises(ValueError):
        resolve_project("   ", ensure=False)


def _table_names(db_url: str) -> set[str]:
    from ai_dev_system.db.connection import get_connection
    conn = get_connection(db_url)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        return {r["name"] for r in rows}
    finally:
        conn.close()


def test_ensure_creates_dirs_gitignore_and_schema(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    p = resolve_project(str(repo), ensure=True)
    assert Path(p.root).is_dir()
    assert Path(p.storage_root).is_dir()
    gi = repo / ".ai-dev" / ".gitignore"
    assert gi.exists()
    assert "state/" in gi.read_text(encoding="utf-8").splitlines()
    # schema applied: a known control-layer table exists
    assert "runs" in _table_names(p.database_url)


def test_ensure_is_idempotent(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    resolve_project(str(repo), ensure=True)
    resolve_project(str(repo), ensure=True)  # must not raise
    gi = repo / ".ai-dev" / ".gitignore"
    # exactly one state/ line
    assert gi.read_text(encoding="utf-8").splitlines().count("state/") == 1


def test_ensure_preserves_existing_gitignore(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".ai-dev").mkdir(parents=True)
    gi = repo / ".ai-dev" / ".gitignore"
    gi.write_text("# custom\nnotes.txt\n", encoding="utf-8")
    resolve_project(str(repo), ensure=True)
    lines = gi.read_text(encoding="utf-8").splitlines()
    assert "notes.txt" in lines and "# custom" in lines
    assert lines.count("state/") == 1
