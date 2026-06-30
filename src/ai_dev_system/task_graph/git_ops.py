"""Shared git CLI helpers for single-task flows (executor + repo docs).

Every helper is best-effort and small; the heavier helpers (publish, push)
never raise into the caller so a missing remote / auth failure degrades to
"no link" rather than sinking the run.
"""
from __future__ import annotations

import subprocess


def run_git(args: list[str], cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def current_branch(repo_path: str) -> str:
    proc = run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo_path)
    if proc.returncode != 0:
        raise RuntimeError(f"git rev-parse failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def base_branch(repo_path: str) -> str:
    """Repo's default integration branch (master/main). Never an ai-dev/ branch."""
    current = current_branch(repo_path)
    if not current.startswith("ai-dev/"):
        return current
    for candidate in ("master", "main"):
        if run_git(["rev-parse", "--verify", candidate], repo_path).returncode == 0:
            return candidate
    proc = run_git(["symbolic-ref", "refs/remotes/origin/HEAD", "--short"], repo_path)
    if proc.returncode == 0:
        return proc.stdout.strip().removeprefix("origin/")
    return "master"


def checkout_branch(repo_path: str, branch_name: str) -> None:
    """Checkout branch, creating it from the CURRENT ref if missing."""
    if run_git(["checkout", branch_name], repo_path).returncode == 0:
        return
    proc = run_git(["checkout", "-b", branch_name], repo_path)
    if proc.returncode != 0:
        raise RuntimeError(f"git checkout -b {branch_name!r} failed: {proc.stderr.strip()}")


def ensure_branch_from_base(repo_path: str, branch: str) -> None:
    """Make `branch` the current branch. If it exists, check it out; otherwise
    fork it from the repo's real base (master/main), NOT from a leftover
    ai-dev/ branch."""
    if run_git(["rev-parse", "--verify", branch], repo_path).returncode == 0:
        co = run_git(["checkout", branch], repo_path)
        if co.returncode != 0:
            raise RuntimeError(f"git checkout {branch!r} failed: {co.stderr.strip()}")
        return
    base = base_branch(repo_path)
    run_git(["checkout", base], repo_path)  # best-effort; may already be on base
    created = run_git(["checkout", "-b", branch], repo_path)
    if created.returncode != 0:
        co = run_git(["checkout", branch], repo_path)  # race: created meanwhile
        if co.returncode != 0:
            raise RuntimeError(f"git checkout -b {branch!r} failed: {created.stderr.strip()}")


def commit_paths(repo_path: str, paths: list[str], message: str) -> bool:
    """Stage `paths` and commit. Returns True if a commit was created, False if
    there was nothing to commit (identical content)."""
    run_git(["add", *paths], repo_path)
    proc = run_git(["commit", "-m", message], repo_path)
    return proc.returncode == 0


def normalize_github_url(remote: str) -> str:
    remote = (remote or "").strip()
    if remote.endswith(".git"):
        remote = remote[:-4]
    if remote.startswith("git@github.com:"):
        remote = "https://github.com/" + remote[len("git@github.com:"):]
    elif remote.startswith("ssh://git@github.com/"):
        remote = "https://github.com/" + remote[len("ssh://git@github.com/"):]
    return remote.rstrip("/")


def push_branch_compare(repo_path: str, branch: str, base: str) -> dict:
    """Push `branch` to origin and build a GitHub compare URL. Never raises."""
    info: dict = {"pushed": False, "compare_url": None, "push_error": None}
    push = run_git(["push", "-u", "origin", branch], repo_path)
    if push.returncode != 0:
        info["push_error"] = (push.stderr or push.stdout or "").strip()[:300]
        return info
    info["pushed"] = True
    remote = run_git(["remote", "get-url", "origin"], repo_path)
    if remote.returncode == 0 and remote.stdout.strip():
        base_url = normalize_github_url(remote.stdout.strip())
        if "github.com/" in base_url:
            info["compare_url"] = f"{base_url}/compare/{base}...{branch}"
    return info


def blob_url(remote_url: str, branch: str, relpath: str) -> str | None:
    """GitHub blob URL for a file on a branch, or None for non-GitHub remotes."""
    base = normalize_github_url(remote_url)
    if "github.com/" not in base:
        return None
    rel = relpath.replace("\\", "/").lstrip("/")
    return f"{base}/blob/{branch}/{rel}"
