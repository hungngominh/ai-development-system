import asyncio
import json
import os
from ai_dev_system.harness.tools.dev_pipeline import make_dev_pipeline_tools
from ai_dev_system.harness.tools.chat_task_store import ChatTaskStore


class _Cfg:
    def __init__(self, tmp, bots):
        self.storage_root = str(tmp)
        self.telegram_bots = bots
        self.database_url = "sqlite:///:memory:"


class _Bot:
    def __init__(self, label, repo_path="", base_branch=""):
        self.label, self.repo_path, self.base_branch = label, repo_path, base_branch


def _find(tools, name):
    for t in tools:
        if (getattr(t, "name", None) or getattr(t, "__name__", "")) == name:
            return t
    raise AssertionError(name)


def test_task_start_guard_when_no_repo(tmp_path):
    cfg = _Cfg(tmp_path, [_Bot("tg")])  # no repo_path
    tools = make_dev_pipeline_tools(
        surface="tg", chat_id="1", conn_factory=lambda: None, config=cfg,
        link_store=None, chat_task_store=ChatTaskStore(str(tmp_path)),
    )
    start = _find(tools, "dev_task_start")
    out = asyncio.run(start.handler({"task_description": "add logout"}))
    assert "chưa gắn repo" in out["content"][0]["text"].lower() or "repo" in out["content"][0]["text"].lower()


def test_task_start_spawns_worker_and_records_pending(tmp_path):
    cfg = _Cfg(tmp_path, [_Bot("tg", repo_path="/repos/app", base_branch="main")])
    spawned = []
    store = ChatTaskStore(str(tmp_path))
    tools = make_dev_pipeline_tools(
        surface="tg", chat_id="1", conn_factory=lambda: None, config=cfg,
        link_store=None, chat_task_store=store,
        spawn_task_worker=lambda argv, **kw: spawned.append(argv),
        make_spec_id=lambda: "spec123",
    )
    start = _find(tools, "dev_task_start")
    out = asyncio.run(start.handler({"task_description": "add logout button"}))
    # worker argv carries the bound repo + the generated spec id + the idea
    assert any(a == "--repo" for a in spawned[0])
    assert "/repos/app" in spawned[0] and "spec123" in spawned[0]
    assert "add logout button" in spawned[0]
    pending = store.get_pending("tg", "1")
    assert pending["spec_id"] == "spec123" and pending["repo"] == "/repos/app"


# ---------------------------------------------------------------------------
# Task 7: Plan summary, approval, executor spawn, PR reply
# ---------------------------------------------------------------------------

def _seed_spec(tmp_path, spec_id, repo="/repos/app"):
    d = tmp_path / "task_specs"; d.mkdir(parents=True, exist_ok=True)
    (d / f"{spec_id}.json").write_text(json.dumps({
        "status": "done", "idea": "add logout", "repo": repo,
        "task": {"title": "Add logout"}, "facets": {},
    }), encoding="utf-8")


def test_status_shows_plan_when_spec_ready(tmp_path, monkeypatch):
    cfg = _Cfg(tmp_path, [_Bot("tg", repo_path="/repos/app", base_branch="main")])
    store = ChatTaskStore(str(tmp_path))
    store.set_pending("tg", "1", spec_id="s1", repo="/repos/app", base_branch="main")
    _seed_spec(tmp_path, "s1")
    tools = make_dev_pipeline_tools(
        surface="tg", chat_id="1", conn_factory=lambda: None, config=cfg,
        link_store=None, chat_task_store=store,
    )
    status = _find(tools, "dev_run_status")
    out = asyncio.run(status.handler({"run_id": ""}))
    txt = out["content"][0]["text"]
    assert "plan" in txt.lower() and "duyệt" in txt.lower()
    assert (tmp_path / "task_specs" / "s1-plan.json").exists()  # plan materialized


def test_approve_spawns_executor(tmp_path):
    cfg = _Cfg(tmp_path, [_Bot("tg", repo_path="/repos/app", base_branch="main")])
    store = ChatTaskStore(str(tmp_path))
    store.set_pending("tg", "1", spec_id="s2", repo="/repos/app", base_branch="main")
    _seed_spec(tmp_path, "s2")
    # pre-build the plan so approve_plan finds it
    from ai_dev_system.task_graph.single_task_plan import plan_single_task
    plan_single_task({"task": {"title": "t"}, "facets": {}}, "s2", storage_root=str(tmp_path))
    spawned = []
    tools = make_dev_pipeline_tools(
        surface="tg", chat_id="1", conn_factory=lambda: None, config=cfg,
        link_store=None, chat_task_store=store,
        spawn_executor=lambda argv, **kw: spawned.append(argv),
    )
    gate = _find(tools, "dev_answer_gate")
    out = asyncio.run(gate.handler({"run_id": "", "text": "duyệt"}))
    assert "s2" in spawned[0] and "single_task_executor" in " ".join(spawned[0])
    assert "đang chạy" in out["content"][0]["text"].lower() or "execution" in out["content"][0]["text"].lower()


def test_status_creates_pr_when_exec_completed(tmp_path):
    cfg = _Cfg(tmp_path, [_Bot("tg", repo_path="/repos/app", base_branch="main")])
    store = ChatTaskStore(str(tmp_path))
    store.set_pending("tg", "1", spec_id="s3", repo="/repos/app", base_branch="main")
    _seed_spec(tmp_path, "s3")
    d = tmp_path / "task_specs"
    (d / "s3-exec.json").write_text(json.dumps({
        "branch": "ai-dev/s3", "base_branch": "main", "exec_status": "COMPLETED",
    }), encoding="utf-8")
    pr_calls = []
    def fake_create_pr(repo, branch, base, title, body="", **kw):
        pr_calls.append((repo, branch, base))
        return {"ok": True, "pr_url": "https://github.com/o/r/pull/9", "pushed": True, "error": None}
    tools = make_dev_pipeline_tools(
        surface="tg", chat_id="1", conn_factory=lambda: None, config=cfg,
        link_store=None, chat_task_store=store, create_pr=fake_create_pr,
    )
    status = _find(tools, "dev_run_status")
    out = asyncio.run(status.handler({"run_id": ""}))
    assert pr_calls and pr_calls[0][1] == "ai-dev/s3"
    assert "pull/9" in out["content"][0]["text"]
    # On successful PR the pending record is cleared (corrected terminal-state behavior)
    assert store.get_pending("tg", "1") is None


# ---------------------------------------------------------------------------
# Final-review fixes: clear pending on terminal states; refuse concurrent task
# ---------------------------------------------------------------------------

def test_status_clears_pending_after_pr(tmp_path):
    """On COMPLETED with successful PR, pending record must be cleared."""
    cfg = _Cfg(tmp_path, [_Bot("tg", repo_path="/repos/app", base_branch="main")])
    store = ChatTaskStore(str(tmp_path))
    store.set_pending("tg", "1", spec_id="s4", repo="/repos/app", base_branch="main")
    _seed_spec(tmp_path, "s4")
    d = tmp_path / "task_specs"
    (d / "s4-exec.json").write_text(json.dumps({
        "branch": "ai-dev/s4", "base_branch": "main", "exec_status": "COMPLETED",
    }), encoding="utf-8")

    def fake_create_pr(repo, branch, base, title, body="", **kw):
        return {"ok": True, "pr_url": "https://github.com/o/r/pull/10", "pushed": True, "error": None}

    tools = make_dev_pipeline_tools(
        surface="tg", chat_id="1", conn_factory=lambda: None, config=cfg,
        link_store=None, chat_task_store=store, create_pr=fake_create_pr,
    )
    status = _find(tools, "dev_run_status")
    out = asyncio.run(status.handler({"run_id": ""}))
    assert "pull/10" in out["content"][0]["text"]
    assert store.get_pending("tg", "1") is None  # cleared


def test_status_clears_pending_on_failed(tmp_path):
    """On FAILED execution, pending record must be cleared."""
    cfg = _Cfg(tmp_path, [_Bot("tg", repo_path="/repos/app", base_branch="main")])
    store = ChatTaskStore(str(tmp_path))
    store.set_pending("tg", "1", spec_id="s5", repo="/repos/app", base_branch="main")
    _seed_spec(tmp_path, "s5")
    d = tmp_path / "task_specs"
    (d / "s5-exec.json").write_text(json.dumps({
        "exec_status": "FAILED", "error": "boom",
    }), encoding="utf-8")

    tools = make_dev_pipeline_tools(
        surface="tg", chat_id="1", conn_factory=lambda: None, config=cfg,
        link_store=None, chat_task_store=store,
    )
    status = _find(tools, "dev_run_status")
    out = asyncio.run(status.handler({"run_id": ""}))
    txt = out["content"][0]["text"]
    assert "FAILED" in txt or "boom" in txt
    assert store.get_pending("tg", "1") is None  # cleared


def test_status_keeps_pending_when_pr_fails(tmp_path):
    """On COMPLETED but PR creation fails, pending must NOT be cleared (retry possible)."""
    cfg = _Cfg(tmp_path, [_Bot("tg", repo_path="/repos/app", base_branch="main")])
    store = ChatTaskStore(str(tmp_path))
    store.set_pending("tg", "1", spec_id="s6", repo="/repos/app", base_branch="main")
    _seed_spec(tmp_path, "s6")
    d = tmp_path / "task_specs"
    (d / "s6-exec.json").write_text(json.dumps({
        "branch": "ai-dev/s6", "base_branch": "main", "exec_status": "COMPLETED",
    }), encoding="utf-8")

    def fake_create_pr(repo, branch, base, title, body="", **kw):
        return {"ok": False, "pr_url": None, "error": "no remote"}

    tools = make_dev_pipeline_tools(
        surface="tg", chat_id="1", conn_factory=lambda: None, config=cfg,
        link_store=None, chat_task_store=store, create_pr=fake_create_pr,
    )
    status = _find(tools, "dev_run_status")
    out = asyncio.run(status.handler({"run_id": ""}))
    txt = out["content"][0]["text"]
    assert "no remote" in txt or "lỗi" in txt or "PR" in txt
    assert store.get_pending("tg", "1") is not None  # NOT cleared


def test_task_start_refuses_when_pending_exists(tmp_path):
    """dev_task_start must refuse if a pending task already exists for this chat."""
    cfg = _Cfg(tmp_path, [_Bot("tg", repo_path="/repos/app", base_branch="main")])
    store = ChatTaskStore(str(tmp_path))
    store.set_pending("tg", "1", spec_id="old", repo="/repos/app", base_branch="main")
    spawned = []
    tools = make_dev_pipeline_tools(
        surface="tg", chat_id="1", conn_factory=lambda: None, config=cfg,
        link_store=None, chat_task_store=store,
        spawn_task_worker=lambda argv, **kw: spawned.append(argv),
        make_spec_id=lambda: "new_spec",
    )
    start = _find(tools, "dev_task_start")
    out = asyncio.run(start.handler({"task_description": "x"}))
    txt = out["content"][0]["text"]
    assert "chờ duyệt" in txt or "đang có" in txt.lower()
    assert spawned == []  # worker NOT spawned
    assert store.get_pending("tg", "1")["spec_id"] == "old"  # not overwritten
