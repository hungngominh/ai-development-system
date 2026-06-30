import subprocess
from pathlib import Path

import pytest

from ai_dev_system.task_graph import git_ops


def test_normalize_github_url_variants():
    assert git_ops.normalize_github_url("https://github.com/o/r.git") == "https://github.com/o/r"
    assert git_ops.normalize_github_url("git@github.com:o/r.git") == "https://github.com/o/r"
    assert git_ops.normalize_github_url("ssh://git@github.com/o/r.git") == "https://github.com/o/r"
    assert git_ops.normalize_github_url("https://github.com/o/r/") == "https://github.com/o/r"


def test_blob_url_github_and_non_github():
    assert (
        git_ops.blob_url("git@github.com:o/r.git", "ai-dev/task-ab12", ".ai-dev/tasks/x-spec.md")
        == "https://github.com/o/r/blob/ai-dev/task-ab12/.ai-dev/tasks/x-spec.md"
    )
    # backslashes normalized to forward slashes
    assert git_ops.blob_url(
        "https://github.com/o/r", "b", ".ai-dev\\tasks\\x.md"
    ) == "https://github.com/o/r/blob/b/.ai-dev/tasks/x.md"
    # non-GitHub remote → None
    assert git_ops.blob_url("https://gitlab.com/o/r.git", "b", "x.md") is None


def _init_repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    p = str(path)
    git_ops.run_git(["init"], p)
    git_ops.run_git(["config", "user.email", "t@t.t"], p)
    git_ops.run_git(["config", "user.name", "t"], p)
    git_ops.run_git(["checkout", "-b", "master"], p)
    (path / "README.md").write_text("hi", encoding="utf-8")
    git_ops.run_git(["add", "-A"], p)
    git_ops.run_git(["commit", "-m", "init"], p)
    return p


def test_ensure_branch_from_base_creates_then_reuses(tmp_path):
    p = _init_repo(tmp_path / "repo")
    git_ops.ensure_branch_from_base(p, "ai-dev/task-xyz")
    assert git_ops.current_branch(p) == "ai-dev/task-xyz"
    # idempotent: switch away then ensure again → checks out existing branch
    git_ops.run_git(["checkout", "master"], p)
    git_ops.ensure_branch_from_base(p, "ai-dev/task-xyz")
    assert git_ops.current_branch(p) == "ai-dev/task-xyz"


def test_commit_paths_returns_false_on_nothing_to_commit(tmp_path):
    p = _init_repo(tmp_path / "repo")
    (Path(p) / "a.txt").write_text("x", encoding="utf-8")
    assert git_ops.commit_paths(p, ["a.txt"], "add a") is True
    assert git_ops.commit_paths(p, ["a.txt"], "noop") is False  # no changes
