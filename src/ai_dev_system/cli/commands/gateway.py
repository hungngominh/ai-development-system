"""ai-dev gateway — launch the chat-gateway daemon (Telegram). Reply-only in Plan 3;
proactive run-status push (the notifier) lands in Plan 5."""
from __future__ import annotations

import logging
import subprocess
import typer

from ai_dev_system.cli.core.registry import command
from ai_dev_system.gateway.notifier import RunStatusWatcher

logger = logging.getLogger(__name__)


def build_gateway(cfg, *, transport=None, sender=None, poll_timeout: int = 30):
    """Wire a GatewayDaemon from config, or return None if no platform is enabled."""
    from ai_dev_system.gateway.registry import PlatformRegistry
    from ai_dev_system.gateway.daemon import GatewayDaemon
    from ai_dev_system.gateway.clarify_watcher import ClarifyWatcher
    from ai_dev_system.gateway.project_registry import ProjectRegistry
    from ai_dev_system.assistant.factory import build_assistant_factory
    from ai_dev_system.assistant.memory import assistant_home
    from ai_dev_system.assistant.session import SessionStore
    from ai_dev_system.assistant.run_links import RunLinkStore
    from ai_dev_system.harness.tools.chat_task_store import ChatTaskStore
    from ai_dev_system.db.connection import get_connection
    from ai_dev_system.config import repo_path_for_label

    registry = PlatformRegistry.from_config(cfg, transport=transport, sender=sender)
    if not registry.enabled():
        return None

    platforms_by_name = {p.name: p for p in registry.adapters()}
    project_registry = ProjectRegistry()

    # Global (fallback) resources for non-repo bots.
    gw_conn = get_connection(cfg.database_url)

    def global_conn_factory():
        return gw_conn

    global_link_store = RunLinkStore(global_conn_factory)
    global_session_store = SessionStore(global_conn_factory)

    factory = build_assistant_factory(
        model=None,
        link_store=global_link_store,
        config=cfg,
        conn_factory=global_conn_factory,
        project_registry=project_registry,
    )

    # Distinct bound repos → one watcher pair each; global pair iff any non-repo bot.
    repos: list[str] = []
    has_non_repo = False
    for b in getattr(cfg, "telegram_bots", ()):
        rp = repo_path_for_label((b,), b.label)
        if rp:
            if rp not in repos:
                repos.append(rp)
        else:
            has_non_repo = True

    watchers = []            # (RunStatusWatcher, ClarifyWatcher)
    resume_stores = []       # session stores to mark resume-pending on unclean restart

    for rp in repos:
        res = project_registry.get(rp)
        rw = RunStatusWatcher(res.conn_factory, res.link_store, platforms_by_name)
        cwt = ClarifyWatcher(
            ChatTaskStore(res.paths.storage_root), platforms_by_name,
            res.session_store, res.paths.storage_root,
        )
        watchers.append((rw, cwt))
        resume_stores.append(res.session_store)

    if has_non_repo or not repos:
        rw = RunStatusWatcher(global_conn_factory, global_link_store, platforms_by_name)
        cwt = ClarifyWatcher(
            ChatTaskStore(cfg.storage_root), platforms_by_name,
            global_session_store, str(cfg.storage_root),
        )
        watchers.append((rw, cwt))
        resume_stores.append(global_session_store)

    def _post_poll():
        for rw, cwt in watchers:
            rw.check_once()
            cwt.check_once()

    class _ResumeFanout:
        """Daemon calls mark_recent_resume_pending() once; fan it out to every store."""
        def mark_recent_resume_pending(self):
            for s in resume_stores:
                try:
                    s.mark_recent_resume_pending()
                except Exception:  # noqa: BLE001
                    logger.exception("gateway: resume-pending mark failed")

    daemon = GatewayDaemon(
        factory=factory, platforms=registry.adapters(), home=assistant_home(),
        session_store=_ResumeFanout(),
        poll_timeout=poll_timeout,
        post_poll_hook=_post_poll,
    )
    daemon._project_registry = project_registry  # closed in gateway_cmd finally
    return daemon


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


def _ensure_git_ready() -> None:
    """Best-effort: make git/gh usable in the container for repo-bound bots.
    Failures are non-fatal (a new-project-only deployment without gh still boots)."""
    for argv in (
        ["gh", "auth", "setup-git"],
        ["git", "config", "--global", "--add", "safe.directory", "*"],
    ):
        try:
            subprocess.run(argv, capture_output=True, text=True)
        except Exception:  # noqa: BLE001
            pass


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
    _ensure_git_ready()
    daemon = build_gateway(cfg, poll_timeout=poll_timeout)
    if daemon is None:
        typer.echo("No gateway platform enabled (set AI_DEV_TELEGRAM_TOKEN).", err=True)
        raise typer.Exit(1)
    try:
        daemon.run(max_iterations=1 if once else (max_iterations or None))
    finally:
        reg = getattr(daemon, "_project_registry", None)
        if reg is not None:
            reg.close_all()
    raise typer.Exit(0)
