"""ai-dev gateway — launch the chat-gateway daemon (Telegram). Reply-only in Plan 3;
proactive run-status push (the notifier) lands in Plan 5."""
from __future__ import annotations

import typer

from ai_dev_system.cli.core.registry import command


def build_gateway(cfg, *, transport=None, sender=None, poll_timeout: int = 30):
    """Wire a GatewayDaemon from config, or return None if no platform is enabled."""
    from ai_dev_system.gateway.registry import PlatformRegistry
    from ai_dev_system.gateway.daemon import GatewayDaemon
    from ai_dev_system.assistant.factory import build_assistant_factory
    from ai_dev_system.assistant.memory import assistant_home
    from ai_dev_system.assistant.session import SessionStore
    from ai_dev_system.db.connection import get_connection

    registry = PlatformRegistry.from_config(cfg, transport=transport, sender=sender)
    if not registry.enabled():
        return None
    factory = build_assistant_factory(model=None)
    return GatewayDaemon(
        factory=factory, platforms=registry.adapters(), home=assistant_home(),
        session_store=SessionStore(lambda: get_connection(cfg.database_url)),
        poll_timeout=poll_timeout,
    )


@command(verb="gateway", help="Launch the chat-gateway daemon (Telegram).")
def gateway_cmd(
    once: bool = typer.Option(False, "--once", help="Poll a single batch then exit (smoke)."),
    max_iterations: int = typer.Option(0, "--max-iterations", help="0 = run forever."),
    poll_timeout: int = typer.Option(30, "--poll-timeout", help="Telegram long-poll seconds."),
) -> None:
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    from ai_dev_system.config import Config

    daemon = build_gateway(Config.from_env(), poll_timeout=poll_timeout)
    if daemon is None:
        typer.echo("No gateway platform enabled (set AI_DEV_TELEGRAM_TOKEN).", err=True)
        raise typer.Exit(1)
    daemon.run(max_iterations=1 if once else (max_iterations or None))
    raise typer.Exit(0)
