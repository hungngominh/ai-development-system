"""ai-dev telegram setup — tạo bot Telegram cho project và ghi vào .env."""
from __future__ import annotations

import typer

from ai_dev_system.cli.core.registry import command


@command(noun="telegram", verb="setup",
         help="Tạo Telegram bot cho project (tự lấy chat_id) và ghi vào .env.")
def telegram_setup_cmd(
    env_file: str = typer.Option(".env", "--env-file", help="Đường dẫn .env để ghi bot."),
) -> None:
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    from pathlib import Path
    from ai_dev_system.cli.telegram_setup import run_telegram_setup

    rc = run_telegram_setup(Path(env_file))
    raise typer.Exit(rc)
