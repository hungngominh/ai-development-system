"""ai-dev gateway — launch the chat-gateway daemon (Telegram). Reply-only in Plan 3;
proactive run-status push (the notifier) lands in Plan 5."""
from __future__ import annotations

import typer

from ai_dev_system.cli.core.registry import command


def build_gateway(cfg, *, transport=None, sender=None, poll_timeout: int = 30):
    """Wire a GatewayDaemon from config, or return None if no platform is enabled."""
    from ai_dev_system.gateway.registry import PlatformRegistry
    from ai_dev_system.gateway.daemon import GatewayDaemon
    from ai_dev_system.gateway.notifier import RunStatusWatcher
    from ai_dev_system.assistant.factory import build_assistant_factory
    from ai_dev_system.assistant.memory import assistant_home
    from ai_dev_system.assistant.session import SessionStore
    from ai_dev_system.assistant.run_links import RunLinkStore
    from ai_dev_system.db.connection import get_connection

    registry = PlatformRegistry.from_config(cfg, transport=transport, sender=sender)
    if not registry.enabled():
        return None

    # Single shared connection for this daemon (single-threaded)
    gw_conn = get_connection(cfg.database_url)

    def conn_factory():
        return gw_conn

    link_store = RunLinkStore(conn_factory)

    factory = build_assistant_factory(
        model=None,
        link_store=link_store,
        config=cfg,
        conn_factory=conn_factory,
    )

    platforms_by_name = {p.name: p for p in registry.adapters()}

    watcher = RunStatusWatcher(conn_factory, link_store, platforms_by_name)

    return GatewayDaemon(
        factory=factory, platforms=registry.adapters(), home=assistant_home(),
        session_store=SessionStore(conn_factory),
        poll_timeout=poll_timeout,
        post_poll_hook=watcher.check_once,
    )


def _ensure_schema(database_url: str) -> None:
    """Apply the control-layer schema (idempotent) so a fresh DB doesn't crash the daemon.
    Raise if the schema could not be applied, rather than letting the daemon crash later
    on a missing table with a confusing error."""
    from ai_dev_system.db.connection import get_connection
    from ai_dev_system.db.migrator import apply_schema

    results = apply_schema(get_connection(database_url))
    failed = [r for r in results if r.error or (not r.applied and r.skipped_reason == "file not found")]
    if failed:
        details = "; ".join(f"{r.name}: {r.error or r.skipped_reason}" for r in failed)
        raise RuntimeError(f"DB schema apply failed: {details}")


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

    cfg = Config.from_env()
    _ensure_schema(cfg.database_url)
    daemon = build_gateway(cfg, poll_timeout=poll_timeout)
    if daemon is None:
        typer.echo("No gateway platform enabled (set AI_DEV_TELEGRAM_TOKEN).", err=True)
        raise typer.Exit(1)
    daemon.run(max_iterations=1 if once else (max_iterations or None))
    raise typer.Exit(0)
