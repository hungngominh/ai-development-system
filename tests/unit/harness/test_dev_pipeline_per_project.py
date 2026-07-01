# tests/unit/harness/test_dev_pipeline_per_project.py
import asyncio
from ai_dev_system.harness.tools.dev_pipeline import make_dev_pipeline_tools
from ai_dev_system.harness.tools.chat_task_store import ChatTaskStore


class _Cfg:
    def __init__(self, tmp, bots):
        self.storage_root = str(tmp / "global")
        self.telegram_bots = bots
        self.database_url = "sqlite:///global.db"


class _Bot:
    def __init__(self, label, repo_path="", base_branch=""):
        self.label, self.repo_path, self.base_branch = label, repo_path, base_branch


def _find(tools, name):
    for t in tools:
        if (getattr(t, "name", None) or getattr(t, "__name__", "")) == name:
            return t
    raise AssertionError(name)


def test_task_start_spawn_uses_per_project_storage_and_db(tmp_path):
    cfg = _Cfg(tmp_path, [_Bot("tg", repo_path="/repos/app", base_branch="main")])
    proj_sr = str(tmp_path / "proj" / "storage")
    proj_db = "sqlite:///proj/control.db"
    spawned = []
    tools = make_dev_pipeline_tools(
        surface="tg", chat_id="1", conn_factory=lambda: None, config=cfg,
        link_store=None, chat_task_store=ChatTaskStore(proj_sr),
        storage_root=proj_sr, database_url=proj_db,
        spawn_task_worker=lambda argv, **kw: spawned.append(argv),
        make_spec_id=lambda: "spec123",
    )
    start = _find(tools, "dev_task_start")
    asyncio.run(start.handler({"task_description": "do it"}))
    argv = spawned[0]
    assert proj_sr in argv and proj_db in argv  # per-project flags, not global


def test_newproject_spawn_injects_per_project_env(tmp_path):
    cfg = _Cfg(tmp_path, [_Bot("tg", repo_path="/repos/app")])
    proj_sr = str(tmp_path / "proj" / "storage")
    proj_db = "sqlite:///proj/control.db"
    captured = {}

    def rec_spawn(argv, **kw):
        captured["argv"] = argv
        captured["env"] = kw.get("env")

    class _Conn:
        def execute(self, *a, **k):
            class _R:  # no run row yet
                def fetchone(self_):
                    return None
            return _R()

    class _Link:
        def link(self, *a): pass
        def add_pending(self, *a): pass

    tools = make_dev_pipeline_tools(
        surface="tg", chat_id="1", conn_factory=lambda: _Conn(), config=cfg,
        link_store=_Link(), storage_root=proj_sr, database_url=proj_db,
        spawn_start=rec_spawn,
    )
    npt = _find(tools, "dev_newproject_start")
    asyncio.run(npt.handler({"project_name": "P", "idea": "x"}))
    assert captured["env"]["STORAGE_ROOT"] == proj_sr
    assert captured["env"]["DATABASE_URL"] == proj_db
