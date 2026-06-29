import pytest


@pytest.fixture(autouse=True)
def _clear(monkeypatch, tmp_path):
    monkeypatch.delenv("AI_DEV_TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("AI_DEV_TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    monkeypatch.setenv("AI_DEV_ASSISTANT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'ctl.db'}")


def test_gateway_cmd_exits_1_when_no_platform():
    import typer
    from ai_dev_system.cli.commands.gateway import gateway_cmd
    with pytest.raises(typer.Exit) as exc:
        gateway_cmd(once=True, max_iterations=0, poll_timeout=1)
    assert exc.value.exit_code == 1


def test_gateway_command_registered():
    import ai_dev_system.cli.commands  # noqa: F401
    from ai_dev_system.cli.core.registry import get_app
    assert "gateway" in {c.name for c in get_app().registered_commands}


def test_build_gateway_returns_none_without_token():
    from ai_dev_system.config import Config
    from ai_dev_system.cli.commands.gateway import build_gateway
    assert build_gateway(Config.from_env()) is None
