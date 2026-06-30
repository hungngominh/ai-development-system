"""Logic for `ai-dev telegram setup`: detect chat_id via getUpdates and merge a
bot entry into AI_DEV_TELEGRAM_BOTS in a .env file. Pure helpers are network- and
IO-free so they can be unit-tested directly."""
from __future__ import annotations

import json
import time
from pathlib import Path

BOTS_KEY = "AI_DEV_TELEGRAM_BOTS"


def extract_chat_id(updates: list) -> tuple[int, str] | None:
    """Return (chat_id, username) from the first message update, or None."""
    for upd in updates:
        msg = upd.get("message") or {}
        chat = msg.get("chat") or {}
        cid = chat.get("id")
        if cid is not None:
            uname = (msg.get("from") or {}).get("username", "") or ""
            return int(cid), uname
    return None


def container_repo_path(label: str) -> str:
    """Container-side mount point for a bot's repo."""
    return f"/repos/{label}"


def add_bot_mount(override_text: str, label: str, host_repo: str) -> str:
    """Add a gateway volume mapping host_repo -> /repos/<label> to a
    docker-compose.override.yml body. Idempotent on the container target.
    Kept line-based (not a YAML lib) to avoid a new dependency; the file is
    wizard-owned so the shape is fixed."""
    target = container_repo_path(label)
    mount = f'      - "{host_repo}:{target}:rw"'
    lines = override_text.splitlines() if override_text.strip() else []
    if any(f":{target}:rw" in ln for ln in lines):
        return override_text if override_text.endswith("\n") else override_text + "\n"
    if not lines:
        lines = ["services:", "  gateway:", "    volumes:"]
    else:
        # Ensure the services/gateway/volumes scaffold exists.
        if "    volumes:" not in lines:
            # Append scaffold if a different shape; simplest: rebuild minimal block.
            if "  gateway:" not in lines:
                lines += ["  gateway:"]
            lines += ["    volumes:"]
    lines.append(mount)
    return "\n".join(lines) + "\n"


def upsert_bot_in_env(env_text: str, label: str, token: str, chat_ids,
                      repo_path: str = "", base_branch: str = "") -> str:
    """Add a bot to the AI_DEV_TELEGRAM_BOTS line (single-line JSON), preserving
    all other lines. Raise ValueError on duplicate label."""
    lines = env_text.splitlines()
    line_idx = None
    bots: list = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(f"{BOTS_KEY}=") and not stripped.startswith("#"):
            line_idx = i
            raw = stripped[len(BOTS_KEY) + 1:].strip()
            if raw:
                try:
                    bots = json.loads(raw)
                except ValueError:
                    bots = []
            break

    if not isinstance(bots, list):
        bots = []

    if any(isinstance(b, dict) and b.get("label") == label for b in bots):
        raise ValueError(f"Bot với label '{label}' đã tồn tại. Dùng tên khác.")

    entry = {"label": label, "token": token, "chat_ids": list(chat_ids)}
    if repo_path:
        entry["repo_path"] = repo_path
    if base_branch:
        entry["base_branch"] = base_branch
    bots.append(entry)
    new_line = f"{BOTS_KEY}={json.dumps(bots, ensure_ascii=False)}"

    if line_idx is not None:
        lines[line_idx] = new_line
    else:
        lines.append(new_line)
    return "\n".join(lines) + "\n"


def run_telegram_setup(
    env_path,
    *,
    transport=None,
    input_fn=input,
    sleep_fn=time.sleep,
    clock=time.monotonic,
    max_wait_s: float = 60.0,
) -> int:
    """Interactive wizard: validate a bot token, wait for the user's first message
    to capture chat_id, then write the bot into env_path. Returns process exit code."""
    from ai_dev_system.gateway.telegram_client import get_updates, TelegramError

    env_path = Path(env_path)

    print("\n=== Telegram bot setup ===\n")
    print("Bước 1 — Tạo bot: mở @BotFather trên Telegram, gửi /newbot, làm theo hướng dẫn.")
    token = input_fn("Nhập bot token: ").strip()
    if not token:
        print("Chưa nhập token. Huỷ.")
        return 1

    # Validate token: a bad token makes Telegram return ok:false → TelegramError.
    try:
        get_updates(token, timeout=0, transport=transport)
    except TelegramError:
        print("Token không hợp lệ. Kiểm tra lại token từ @BotFather.")
        return 1

    print("\nBước 2 — Tìm bot vừa tạo trên Telegram và nhắn cho nó BẤT KỲ tin nhắn nào.")
    print(f"⏳ Đang đợi tin nhắn (tối đa {int(max_wait_s)} giây)...")

    found = None
    offset = None
    deadline = clock() + max_wait_s
    while clock() < deadline:
        try:
            updates = get_updates(token, offset=offset, timeout=0, transport=transport)
        except TelegramError:
            updates = []
        found = extract_chat_id(updates)
        if found:
            break
        if updates:
            offset = updates[-1].get("update_id", 0) + 1
        sleep_fn(2)

    if not found:
        print("Không nhận được tin nhắn. Bot đã được tạo và bạn đã nhắn cho nó chưa? Thử lại.")
        return 1

    chat_id, uname = found
    who = f" (từ @{uname})" if uname else ""
    print(f"✅ Phát hiện: chat_id = {chat_id}{who}")

    label = input_fn("\nBước 3 — Đặt tên cho project này (VD: my-app): ").strip()
    if not label:
        print("Chưa đặt tên project. Huỷ.")
        return 1

    host_repo = input_fn(
        "Đường dẫn repo trên host (Enter để bỏ qua — bot chỉ tạo project mới): "
    ).strip()
    repo_path = ""
    base_branch = ""
    if host_repo:
        from pathlib import Path as _P
        if not (_P(host_repo) / ".git").is_dir():
            print(f"⚠ {host_repo} không phải git repo — bỏ qua binding repo.")
        else:
            repo_path = container_repo_path(label)
            base_branch = "main"
            override = _P("docker-compose.override.yml")
            override_text = override.read_text(encoding="utf-8") if override.exists() else ""
            override.write_text(add_bot_mount(override_text, label, host_repo), encoding="utf-8")
            print(f"✅ Mount: {host_repo} → {repo_path} (docker-compose.override.yml)")

    env_text = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    try:
        new_text = upsert_bot_in_env(env_text, label, token, [chat_id],
                                     repo_path=repo_path, base_branch=base_branch)
    except ValueError as exc:
        print(str(exc))
        return 1
    env_path.write_text(new_text, encoding="utf-8")

    print(f"\n✅ Đã thêm bot '{label}' vào {env_path}.")
    print("Chạy `docker-compose restart` (hoặc `docker-compose up -d`) để áp dụng.")
    return 0
