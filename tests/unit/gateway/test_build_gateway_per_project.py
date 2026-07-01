# tests/unit/gateway/test_build_gateway_per_project.py
from unittest.mock import MagicMock, patch
from ai_dev_system.config import Config, TelegramBotConfig
from ai_dev_system.cli.commands import gateway as gw


def _cfg(tmp_path, bots):
    return Config(storage_root=str(tmp_path / "global"),
                  database_url=f"sqlite:///{tmp_path/'global.db'}",
                  telegram_bots=tuple(bots))


def test_build_gateway_makes_one_watcher_pair_per_repo(tmp_path, monkeypatch):
    bots = [
        TelegramBotConfig(label="a", token="t", repo_path=str(tmp_path / "A")),
        TelegramBotConfig(label="b", token="t", repo_path=str(tmp_path / "B")),
    ]
    cfg = _cfg(tmp_path, bots)

    run_watchers, clarify_watchers = [], []
    monkeypatch.setattr(gw, "RunStatusWatcher",
                        lambda *a, **k: run_watchers.append((a, k)) or MagicMock(check_once=lambda: 0))
    # ClarifyWatcher is imported inside build_gateway; patch where it is defined
    import ai_dev_system.gateway.clarify_watcher as cw
    monkeypatch.setattr(cw, "ClarifyWatcher",
                        lambda *a, **k: clarify_watchers.append((a, k)) or MagicMock(check_once=lambda: 0))

    daemon = gw.build_gateway(cfg, transport=MagicMock(), sender=MagicMock())
    # a registry-backed watcher pair for each of the 2 repos (no non-repo bot → no global pair)
    assert len(run_watchers) == 2
    assert len(clarify_watchers) == 2
