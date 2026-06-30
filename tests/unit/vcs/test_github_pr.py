from ai_dev_system.vcs.github_pr import create_pr


class _R:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def test_create_pr_push_then_pr():
    calls = []
    def runner(argv, cwd):
        calls.append(argv)
        if argv[:2] == ["git", "push"]:
            return _R(0)
        if argv[:3] == ["gh", "pr", "create"]:
            return _R(0, out="https://github.com/o/r/pull/7\n")
        return _R(1)
    res = create_pr("/repo", "ai-dev/x", "main", "title", runner=runner)
    assert res["ok"] and res["pr_url"] == "https://github.com/o/r/pull/7"
    assert calls[0][:2] == ["git", "push"]
    assert calls[1][:3] == ["gh", "pr", "create"]


def test_create_pr_existing_pr_recovers_url():
    def runner(argv, cwd):
        if argv[:2] == ["git", "push"]:
            return _R(0)
        if argv[:3] == ["gh", "pr", "create"]:
            return _R(1, err="a pull request already exists")
        if argv[:3] == ["gh", "pr", "view"]:
            return _R(0, out="https://github.com/o/r/pull/3")
        return _R(1)
    res = create_pr("/repo", "b", "main", "t", runner=runner)
    assert res["ok"] and res["pr_url"].endswith("/pull/3")


def test_create_pr_missing_branch_or_repo():
    res = create_pr("", "", "main", "t", runner=lambda *a, **k: _R(0))
    assert not res["ok"] and "branch" in res["error"]
