"""Push a branch and open (or recover) a GitHub PR via git + gh. Never raises —
git/gh failures are returned in the result dict so callers can fall back."""
from __future__ import annotations

import subprocess


def _default_runner(argv, cwd):
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def create_pr(repo: str, branch: str, base: str, title: str, body: str = "",
              *, runner=None) -> dict:
    runner = runner or _default_runner
    result: dict = {"ok": False, "pr_url": None, "pushed": False, "error": None}
    if not (branch and repo):
        result["error"] = "thiếu branch hoặc repo path"
        return result

    try:
        push = runner(["git", "push", "-u", "origin", branch], repo)
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"git push lỗi: {exc}"
        return result
    if push.returncode != 0:
        detail = (push.stderr or push.stdout or "").strip()
        result["error"] = f"git push thất bại: {detail[:500]}"
        return result
    result["pushed"] = True

    pr_title = (title or branch)[:120]
    pr_body = body or (
        "Tạo tự động bởi ai-dev single-task executor.\n\n"
        f"Branch: {branch}\nReview diff trước khi merge."
    )
    try:
        pr = runner(["gh", "pr", "create", "--base", base, "--head", branch,
                     "--title", pr_title, "--body", pr_body], repo)
    except FileNotFoundError:
        result["error"] = "gh CLI không tìm thấy (branch đã được push lên origin)"
        return result
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"gh pr create lỗi: {exc} (branch đã được push)"
        return result

    if pr.returncode != 0:
        err = (pr.stderr or pr.stdout or "").strip()
        try:
            existing = runner(["gh", "pr", "view", branch, "--json", "url", "-q", ".url"], repo)
            url = (existing.stdout or "").strip()
            if existing.returncode == 0 and url.startswith("http"):
                result["ok"] = True
                result["pr_url"] = url
                return result
        except Exception:  # noqa: BLE001
            pass
        result["error"] = f"gh pr create thất bại: {err[:500]} (branch đã được push)"
        return result

    out = (pr.stdout or "").strip()
    url = next((ln.strip() for ln in out.splitlines() if ln.strip().startswith("http")), "")
    result["ok"] = True
    result["pr_url"] = url or out
    return result
