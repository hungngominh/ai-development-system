# tests/unit/harness/tools/test_dev_pipeline_clarify.py
import asyncio, json
from pathlib import Path

from ai_dev_system.harness.tools.dev_pipeline import make_dev_pipeline_tools
from ai_dev_system.harness.tools.chat_task_store import ChatTaskStore


class Cfg:
    def __init__(self, root):
        self.storage_root = root
        self.database_url = "sqlite://"
        from ai_dev_system.config import TelegramBotConfig
        self.telegram_bots = (TelegramBotConfig(label="Sigo", token="1:x",
                              allowed_chat_ids=(5913,), repo_path="/repos/Sigo",
                              base_branch="main"),)


def _tools(tmp_path, spawned):
    store = ChatTaskStore(str(tmp_path))
    tools = make_dev_pipeline_tools(
        surface="Sigo", chat_id="5913", conn_factory=lambda: None, config=Cfg(str(tmp_path)),
        link_store=None, spawn_task_worker=lambda argv, **k: spawned.append(argv),
        spawn_executor=lambda *a, **k: None, create_pr=lambda *a, **k: {},
        make_spec_id=lambda: "specid", chat_task_store=store,
    )
    return {t.name: t for t in tools}, store


def _write_spec(tmp_path, clarify):
    d = Path(tmp_path) / "task_specs"; d.mkdir(parents=True, exist_ok=True)
    (d / "specid.json").write_text(
        json.dumps({"facets": {}, "clarify": clarify}), encoding="utf-8")


def test_task_start_stores_idea(tmp_path):
    spawned = []
    tools, store = _tools(tmp_path, spawned)
    asyncio.run(tools["dev_task_start"].handler({"task_description": "add OwnerId"}))
    assert store.get_pending("Sigo", "5913")["idea"] == "add OwnerId"


def test_run_status_shows_questions_when_blocking(tmp_path):
    spawned = []
    tools, store = _tools(tmp_path, spawned)
    store.set_pending("Sigo", "5913", spec_id="specid", repo="/r", base_branch="main", idea="x")
    _write_spec(tmp_path, {"needed": True, "questions": ["GUID hay PK?"]})
    out = asyncio.run(tools["dev_run_status"].handler({}))
    assert "GUID hay PK?" in out["content"][0]["text"]
    assert store.get_pending("Sigo", "5913")["phase"] == "awaiting_clarify"


def test_run_status_plan_ready_when_clean(tmp_path, monkeypatch):
    spawned = []
    tools, store = _tools(tmp_path, spawned)
    store.set_pending("Sigo", "5913", spec_id="specid", repo="/r", base_branch="main", idea="x")
    _write_spec(tmp_path, {"needed": False, "questions": []})
    import ai_dev_system.task_graph.single_task_plan as sp
    monkeypatch.setattr(sp, "load_plan", lambda *a, **k: {"graph": {"tasks": [1, 2]}})
    out = asyncio.run(tools["dev_run_status"].handler({}))
    assert "Plan sẵn sàng" in out["content"][0]["text"]


def test_answer_clarify_merges_and_respawns(tmp_path):
    spawned = []
    tools, store = _tools(tmp_path, spawned)
    store.set_pending("Sigo", "5913", spec_id="specid", repo="/r", base_branch="main",
                      idea="add OwnerId")
    store.update("Sigo", "5913", phase="awaiting_clarify", clarify_questions=["GUID hay PK?"])
    out = asyncio.run(tools["dev_answer_clarify"].handler({"answer": "GUID thật"}))
    assert spawned, "worker re-spawned"
    argv = spawned[-1]
    merged = argv[argv.index("--idea") + 1]
    assert "add OwnerId" in merged and "GUID thật" in merged
    rec = store.get_pending("Sigo", "5913")
    assert rec["phase"] == "generating" and rec["round"] == 1


def test_answer_clarify_noop_when_not_awaiting(tmp_path):
    spawned = []
    tools, store = _tools(tmp_path, spawned)
    store.set_pending("Sigo", "5913", spec_id="specid", repo="/r", base_branch="main", idea="x")
    out = asyncio.run(tools["dev_answer_clarify"].handler({"answer": "hi"}))
    assert not spawned
    assert "không có câu hỏi" in out["content"][0]["text"].lower()
