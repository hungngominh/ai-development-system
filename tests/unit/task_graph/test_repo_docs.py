# tests/unit/task_graph/test_repo_docs.py
from pathlib import Path

from ai_dev_system.task_graph import repo_docs, git_ops


def test_slugify_handles_vietnamese_and_empty():
    assert repo_docs.slugify("Bổ sung trường OwnerId") == "bo-sung-truong-ownerid"
    assert repo_docs.slugify("") == "task"
    assert repo_docs.slugify("!!!") == "task"


def test_relpaths_unique_and_task_named():
    sp = repo_docs.spec_doc_relpath("abcd1234ef", "Add logout")
    pl = repo_docs.plan_doc_relpath("abcd1234ef", "Add logout")
    assert sp == ".ai-dev/tasks/task-abcd1234-add-logout-spec.md"
    assert pl == ".ai-dev/tasks/task-abcd1234-add-logout-plan.md"
    # different spec_id → different path
    assert repo_docs.spec_doc_relpath("zzzz9999aa", "Add logout") != sp


def test_render_spec_md_sections():
    spec = {
        "idea": "Add OwnerId to List endpoint",
        "task": {"title": "Add OwnerId", "objective": "expose owner id"},
        "facets": {"scope": {"status": "filled", "value": "endpoint List"}},
        "findings": ["needs index on OwnerId"],
    }
    md = repo_docs.render_spec_md(spec, "abcd1234ef")
    assert "# Add OwnerId" in md
    assert "Mục tiêu" in md
    assert "expose owner id" in md
    assert "scope" in md
    assert "needs index on OwnerId" in md


def test_render_plan_md_steps_and_gate():
    spec = {"task": {"title": "Add OwnerId"}}
    plan = {
        "spec_id": "abcd1234ef", "branch": "ai-dev/task-abcd1234", "tdd_gate": True,
        "graph": {"tasks": [
            {"id": "T-TEST", "objective": "write tests", "agent_type": "TestAuthorAgent",
             "phase": "test", "done_definition": "failing tests committed", "deps": []},
            {"id": "T-IMPL", "objective": "implement", "agent_type": "RepoBranchAgent",
             "phase": "implementation", "done_definition": "code committed", "deps": ["T-TEST"]},
        ]},
    }
    md = repo_docs.render_plan_md(spec, plan)
    assert "# Plan — Add OwnerId" in md
    assert "2 bước" in md
    assert "TestAuthorAgent" in md and "RepoBranchAgent" in md
    assert "T-TEST" in md  # dep shown


def _init_repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    p = str(path)
    for a in (["init"], ["config", "user.email", "t@t.t"], ["config", "user.name", "t"],
              ["checkout", "-b", "master"]):
        git_ops.run_git(a, p)
    (path / "README.md").write_text("hi", encoding="utf-8")
    git_ops.run_git(["add", "-A"], p)
    git_ops.run_git(["commit", "-m", "init"], p)
    return p


def test_publish_doc_commits_on_branch_and_updates(tmp_path):
    # bare remote so push -u origin succeeds offline
    bare = tmp_path / "remote.git"
    git_ops.run_git(["init", "--bare", str(bare)], str(tmp_path))
    repo = _init_repo(tmp_path / "work")
    git_ops.run_git(["remote", "add", "origin", str(bare)], repo)

    rel = ".ai-dev/tasks/task-abcd1234-x-spec.md"
    url = repo_docs.publish_doc(repo, "ai-dev/task-abcd1234", rel, "v1", "docs: spec")
    # non-GitHub (file) remote → no blob URL, but file is committed on the branch
    assert url is None
    assert git_ops.current_branch(repo) == "ai-dev/task-abcd1234"
    assert (Path(repo) / rel).read_text(encoding="utf-8") == "v1"
    log1 = git_ops.run_git(["log", "--oneline", "ai-dev/task-abcd1234"], repo).stdout
    assert "docs: spec" in log1

    # second publish rewrites + adds a new commit (no force-push)
    repo_docs.publish_doc(repo, "ai-dev/task-abcd1234", rel, "v2", "docs: update spec")
    assert (Path(repo) / rel).read_text(encoding="utf-8") == "v2"
    log2 = git_ops.run_git(["log", "--oneline", "ai-dev/task-abcd1234"], repo).stdout
    assert "docs: update spec" in log2 and "docs: spec" in log2


def test_publish_doc_no_repo_returns_none(tmp_path):
    assert repo_docs.publish_doc("", "b", "x.md", "c", "m") is None
