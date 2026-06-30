import json
from pathlib import Path

from ai_dev_system.cli.commands.gateway import build_gateway, _ensure_schema
from ai_dev_system.harness.tools.chat_task_store import ChatTaskStore


def test_post_poll_hook_pushes_clarify(tmp_path, monkeypatch):
    db = tmp_path / "c.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("AI_DEV_TELEGRAM_BOTS",
                       json.dumps([{"label": "Sigo", "token": "1:x", "chat_ids": [5913]}]))
    _ensure_schema(f"sqlite:///{db}")

    sent = []
    def fake_sender(token, chat_id, text, *, transport=None): sent.append((chat_id, text))

    from ai_dev_system.config import Config
    cfg = Config.from_env()
    daemon = build_gateway(cfg, sender=fake_sender)

    store = ChatTaskStore(str(cfg.storage_root))
    store.set_pending("Sigo", "5913", spec_id="ab", repo="/r", base_branch="main", idea="x")
    specs = Path(cfg.storage_root) / "task_specs"; specs.mkdir(parents=True, exist_ok=True)
    (specs / "ab.json").write_text(
        json.dumps({"clarify": {"needed": True, "questions": ["GUID hay PK?"]}}), encoding="utf-8")

    daemon._post_poll_hook()                       # run one sweep directly
    assert sent and "GUID hay PK?" in sent[0][1]
