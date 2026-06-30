import asyncio
import json
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
