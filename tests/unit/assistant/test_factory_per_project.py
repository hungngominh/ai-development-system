# tests/unit/assistant/test_factory_per_project.py
from unittest.mock import MagicMock
from ai_dev_system.assistant.factory import AssistantFactory


class _Bot:
    def __init__(self, label, repo_path):
        self.label, self.repo_path, self.base_branch = label, repo_path, ""


def _factory(**kw):
    defaults = dict(
        runtime=MagicMock(), memory_store=MagicMock(),
        session_store=MagicMock(), budget=MagicMock(),
        base_prompt="P",
    )
    defaults.update(kw)
    return AssistantFactory(**defaults)


def test_for_chat_repo_bound_uses_registry(monkeypatch):
    cfg = MagicMock()
    cfg.telegram_bots = (_Bot("tg", "/repos/app"),)
    proj = MagicMock()
    proj.session_store.load_or_create.return_value = "sid-proj"
    registry = MagicMock()
    registry.get.return_value = proj

    captured = {}
    import ai_dev_system.assistant.factory as fac

    def fake_tools(**kw):
        captured.update(kw)
        return []
    monkeypatch.setattr(fac, "make_dev_pipeline_tools", fake_tools, raising=False)

    f = _factory(link_store=MagicMock(), config=cfg, conn_factory=lambda: None,
                 project_registry=registry)
    # a minimal base runtime so _build_chat_runtime can read its attrs
    f._runtime = MagicMock(_permission_callback=None, _model=None, _max_turns=20, _client_factory=None)
    f.for_chat("tg", "42")
    registry.get.assert_called_once_with("/repos/app")
    # dev tools built with the project's storage_root + conn_factory
    assert captured["storage_root"] == proj.paths.storage_root
    assert captured["conn_factory"] is proj.conn_factory
    assert captured["link_store"] is proj.link_store


def test_for_chat_non_repo_uses_global(monkeypatch):
    cfg = MagicMock()
    cfg.telegram_bots = (_Bot("tg", ""),)  # bound but no repo
    registry = MagicMock()
    gstore = MagicMock(); gstore.load_or_create.return_value = "sid-global"
    import ai_dev_system.assistant.factory as fac
    captured = {}
    monkeypatch.setattr(fac, "make_dev_pipeline_tools", lambda **kw: captured.update(kw) or [], raising=False)

    f = _factory(link_store=MagicMock(), config=cfg, conn_factory=lambda: "gconn",
                 project_registry=registry, session_store=gstore)
    f._runtime = MagicMock(_permission_callback=None, _model=None, _max_turns=20, _client_factory=None)
    f.for_chat("tg", "42")
    registry.get.assert_not_called()               # no repo → no registry use
    assert captured["storage_root"] is None or captured["storage_root"] == cfg.storage_root
