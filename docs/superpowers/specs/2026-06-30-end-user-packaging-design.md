# End-User Packaging — Design Spec
_Date: 2026-06-30_

## Context

AI Dev System hiện tại chỉ có hướng dẫn cho developer biết codebase. Spec này mô tả cách đóng gói để một team member mới có thể tự setup trong vài bước: clone repo → tạo Telegram bot → chạy Docker → dùng thử ngay.

**Constraints đã xác định:**
- Target: dev nội bộ, Windows + Docker Desktop (WSL2 backend)
- LLM provider: `claude_code` (Claude Max subscription, không trả per-token)
- Mỗi dev tự chạy gateway riêng, mỗi project một Telegram bot riêng
- Multi-bot đã được hỗ trợ qua `AI_DEV_TELEGRAM_BOTS` JSON array

---

## Files mới cần tạo

```
Dockerfile
docker-compose.yml
.dockerignore
.env.example
GETTING-STARTED.md
docs/telegram-setup.md
src/ai_dev_system/cli/commands/telegram.py   ← command mới
```

---

## Dockerfile

```dockerfile
FROM python:3.12-slim

# Node.js + claude CLI (bắt buộc cho claude_code provider)
RUN apt-get update && apt-get install -y nodejs npm \
    && npm install -g @anthropic-ai/claude-code \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Cài Python deps (layer riêng để cache hiệu quả)
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir -e .

RUN mkdir -p /data/storage

ENV PYTHONUNBUFFERED=1
ENV PYTHONIOENCODING=utf-8

CMD ["ai-dev", "gateway"]
```

**Tại sao Node.js trong image:** `claude_code` provider spawn `claude` CLI subprocess. CLI cần được cài trong container (Linux binary, không dùng được Windows binary từ host).

**Tại sao mount `~/.claude` chứ không cài trong image:** Auth token Claude Max là per-user, không embed được vào image. Mount read-only từ host là cách chuẩn.

---

## docker-compose.yml

```yaml
services:
  gateway:
    build: .
    container_name: ai-dev-gateway
    restart: unless-stopped
    volumes:
      # Claude Max auth — mount từ Windows host (read-only)
      # CLAUDE_AUTH_DIR phải trỏ tới C:\Users\<tên>\\.claude
      - "${CLAUDE_AUTH_DIR}:/root/.claude:ro"
      # SQLite DB + storage (persist qua restart và image update)
      - ai-dev-data:/data
      # Config: Telegram tokens, feature flags
      - ./.env:/app/.env:ro
    environment:
      # Override paths cho môi trường container (Windows paths trong .env bị bỏ qua)
      DATABASE_URL: sqlite:////data/control.db
      STORAGE_ROOT: /data/storage
      LLM_PROVIDER: claude_code
      PYTHONIOENCODING: utf-8

volumes:
  ai-dev-data:
```

**Tại sao override `DATABASE_URL` trong compose:** `.env` của dev có thể chứa Windows paths (ví dụ `sqlite:///C:/Users/...`). Container dùng Linux path `/data/control.db` thay thế, không cần sửa `.env`.

---

## .env.example

```bash
# ── Claude Auth ───────────────────────────────────────────────────────────────
# Đường dẫn tới thư mục .claude trên máy bạn (chứa auth token Claude Max)
# Thường là: C:/Users/<tên của bạn>/.claude  (dùng forward slash cho Docker)
CLAUDE_AUTH_DIR=C:/Users/yourname/.claude

# ── LLM ──────────────────────────────────────────────────────────────────────
LLM_PROVIDER=claude_code
LLM_MODEL=sonnet

# ── Telegram Bots ─────────────────────────────────────────────────────────────
# Mỗi project một bot. Dùng lệnh `ai-dev telegram setup` để thêm bot tự động.
# Hoặc điền thủ công theo format dưới đây:
#   label  = tên nhận dạng project (chữ thường, gạch ngang)
#   token  = token từ @BotFather
#   chat_ids = danh sách Telegram user ID được phép dùng bot
AI_DEV_TELEGRAM_BOTS=[]

# ── Feature Flags ─────────────────────────────────────────────────────────────
FF_USE_INTAKE_WIZARD=true
FF_USE_DEBATE_V2=true
FF_USE_GATE1_V2=true
FF_USE_SPEC_GEN_V2=true
FF_EVAL_HARNESS_ENABLED=true
```

---

## `ai-dev telegram setup` command

**File:** `src/ai_dev_system/cli/commands/telegram.py`

**Luồng:**

```
ai-dev telegram setup

  Bước 1 — Tạo bot
    Mở @BotFather, gửi /newbot, làm theo hướng dẫn.
    Nhập bot token: ___

  Bước 2 — Xác nhận kết nối
    [poll getUpdates 1 lần] → in ra "Token hợp lệ ✓"

  Bước 3 — Lấy chat_id
    Tìm bot vừa tạo trên Telegram, nhắn bất kỳ tin nhắn nào...
    ⏳ Đang đợi tin nhắn (tối đa 60 giây)...
    ✅ Phát hiện: chat_id = 5913726934 (từ @yourname)

  Bước 4 — Đặt tên project
    Đặt tên cho project này (VD: my-app): ___

  Bước 5 — Ghi .env
    Đọc AI_DEV_TELEGRAM_BOTS từ .env hiện tại
    Append entry mới {"label": "...", "token": "...", "chat_ids": [...]}
    Ghi lại .env (không xóa các bot cũ)
    ✅ Đã thêm vào .env

  Gợi ý cuối: "Chạy `docker-compose restart` để áp dụng."
```

**Chi tiết kỹ thuật:**
- Dùng `TelegramClient.get_updates(offset=0, timeout=60)` từ `telegram_client.py` đã có sẵn
- Validate token trước (gọi `get_updates` với timeout ngắn) — nếu lỗi HTTP 401, báo token sai ngay
- Poll trong vòng lặp (mỗi 2s) cho đến khi nhận được message hoặc hết 60s
- Đọc/ghi `.env` bằng regex để preserve comments và các vars khác
- Nếu `.env` chưa có `AI_DEV_TELEGRAM_BOTS`, tạo mới với `[]`
- Nếu đã có, parse JSON và append (không duplicate label)

**Error cases:**
- Token sai (401): "Token không hợp lệ, kiểm tra lại @BotFather"
- Timeout 60s không có message: "Không nhận được tin nhắn. Bot đã được tạo chưa? Thử lại."
- Label trùng: "Bot với label 'my-app' đã tồn tại. Dùng tên khác."
- `.env` không tồn tại: tạo mới từ `.env.example`

---

## GETTING-STARTED.md — cấu trúc

```markdown
# Bắt đầu với AI Dev System

## Yêu cầu
- Docker Desktop (Windows, bật WSL2 backend)
- Claude Code CLI đã cài và đăng nhập Claude Max
  (kiểm tra: mở terminal, gõ `claude --version`)
- Tài khoản Telegram

## Bước 1 — Clone repo
git clone <url>
cd ai-development-system
cp .env.example .env

## Bước 2 — Điền CLAUDE_AUTH_DIR
Mở .env, tìm dòng CLAUDE_AUTH_DIR, thay yourname bằng tên user Windows của bạn.
Ví dụ: CLAUDE_AUTH_DIR=C:/Users/ngomi/.claude  (dùng forward slash)

## Bước 3 — Tạo bot Telegram cho project đầu tiên
Chạy lệnh sau TRÊN HOST (không phải trong container) và làm theo hướng dẫn:
  pip install -e .          # cài một lần
  ai-dev telegram setup     # wizard tự động lấy chat_id và ghi .env

## Bước 4 — Chạy gateway
  docker-compose up -d
  docker-compose logs -f    # theo dõi logs, Ctrl+C để thoát

Thấy dòng "Gateway started, polling..." là OK.

## Thử ngay — Demo project đầu tiên
Mở Telegram, tìm bot vừa tạo, nhắn:
  "Tôi muốn xây một web app quản lý công việc nhóm"

System sẽ:
1. Hỏi ~8 câu để hiểu rõ project (intake wizard)
2. Gửi thông báo: "Run đến Gate 1 — trả lời để duyệt debate"
3. Bạn review và nhắn "ok" để tiếp tục
4. System sinh spec, tạo task graph
5. Gate 2, Gate 3...

## Thêm project mới (bot mới)
  ai-dev telegram setup    # chạy lại, thêm bot thứ 2
  docker-compose restart

## Cài đặt Python (cho lệnh ai-dev telegram setup)
Yêu cầu Python 3.11+.
  pip install -e .
Sau đó dùng lệnh ai-dev như bình thường.
Khi chạy gateway thật thì dùng Docker (không cần Python trên host).

## Troubleshooting
- Bot không trả lời → kiểm tra `docker-compose logs gateway`
- "claude: command not found" trong container → image chưa build lại,
  chạy `docker-compose build --no-cache`
- claude auth expired → chạy `claude login` trên Windows host,
  rồi `docker-compose restart`
- Sai CLAUDE_AUTH_DIR → logs sẽ báo lỗi auth, kiểm tra đường dẫn
```

---

## docs/telegram-setup.md — cấu trúc

```markdown
# Hướng dẫn tạo Telegram bot

Tài liệu này mô tả thủ công nếu bạn không dùng `ai-dev telegram setup`.

## Tạo bot qua @BotFather
1. Mở Telegram, tìm @BotFather
2. Gửi /newbot
3. Nhập tên hiển thị: "My Project Bot"
4. Nhập username (phải kết thúc bằng _bot): my_project_123_bot
5. Copy token: 1234567890:AAH...

## Lấy chat_id thủ công
1. Nhắn bất kỳ gì tới bot vừa tạo
2. Mở trình duyệt:
   https://api.telegram.org/bot<TOKEN>/getUpdates
3. Tìm trường "from": {"id": 5913726934} — đó là chat_id
4. Điền vào .env

## Thêm bot vào .env thủ công
AI_DEV_TELEGRAM_BOTS=[
  {"label": "my-project", "token": "1234567890:AAH...", "chat_ids": [5913726934]}
]

## Thêm nhiều bot (nhiều project)
AI_DEV_TELEGRAM_BOTS=[
  {"label": "project-a", "token": "TOKEN_A", "chat_ids": [111111]},
  {"label": "project-b", "token": "TOKEN_B", "chat_ids": [222222]}
]
```

---

## .dockerignore

```
.git
.pytest_cache
.eval_runs
.worktrees
__pycache__
*.pyc
*.pyo
.env
.venv
venv
tests/
research/
ai_dev.db
*.db-journal
```

---

## Thứ tự implement

1. `Dockerfile` + `docker-compose.yml` + `.dockerignore`
2. `.env.example`
3. `ai-dev telegram setup` command (`telegram.py`)
4. `GETTING-STARTED.md`
5. `docs/telegram-setup.md`
6. Smoke test: build image, `docker-compose up`, gửi message qua bot thật

---

## Risks & verify khi implement

- **`claude-agent-sdk` trên PyPI:** pyproject.toml có dep này nhưng nó có thể là Anthropic-internal. Khi build Dockerfile, nếu `pip install -e .` fail với `PackageNotFoundError`, cần xử lý: hoặc loại khỏi deps runtime (chỉ cần ở dev), hoặc cài từ source trong Dockerfile. Verify trước khi viết Dockerfile thật.
- **claude CLI npm package size:** `@anthropic-ai/claude-code` kéo theo Node.js + npm, image có thể ~800MB. Chấp nhận được cho internal use, nhưng cần ghi chú.
- **Windows path trong CLAUDE_AUTH_DIR:** Docker Desktop WSL2 backend hỗ trợ `C:/Users/...` (forward slash). Backslash `C:\` có thể gây lỗi volume mount. `.env.example` đã dùng forward slash.

---

## Out of scope

- Push image lên Docker Hub (dùng local build)
- CI/CD pipeline
- Multi-user / shared gateway
- Cài đặt claude CLI trong image không cần Node.js (alternative: binary download)
