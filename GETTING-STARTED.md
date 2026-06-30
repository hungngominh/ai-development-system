# Bắt đầu với AI Dev System

Hướng dẫn này giúp bạn chạy AI Dev System như một bot Telegram chạy liên tục trong Docker.

## Yêu cầu

- **Docker Desktop** (Windows, bật WSL2 backend) đang chạy.
- **Claude Code CLI** đã cài và đăng nhập Claude Max trên máy.
  Kiểm tra: mở terminal, gõ `claude --version`.
- **Python 3.11+** (chỉ cần cho lệnh `ai-dev telegram setup`).
- Tài khoản **Telegram**.

## Bước 1 — Clone repo

```bash
git clone <repo-url>
cd ai-development-system
cp .env.example .env
```

## Bước 2 — Điền CLAUDE_AUTH_DIR

Mở `.env`, sửa dòng `CLAUDE_AUTH_DIR` thành đường dẫn `.claude` của bạn
(dùng forward slash):

```
CLAUDE_AUTH_DIR=C:/Users/ngomi/.claude
```

## Bước 3 — Tạo bot Telegram cho project đầu tiên

Chạy lệnh sau **trên máy host** (không phải trong container) và làm theo hướng dẫn —
lệnh sẽ tự bắt `chat_id` của bạn và ghi vào `.env`:

```bash
pip install -e .          # cài một lần để có lệnh ai-dev
ai-dev telegram setup
```

Wizard sẽ: hỏi token (từ @BotFather) → bảo bạn nhắn cho bot một tin → tự phát hiện
`chat_id` → hỏi tên project → ghi bot vào `.env`.

> Không muốn dùng wizard? Xem [docs/telegram-setup.md](docs/telegram-setup.md) để làm thủ công.

## Bước 4 — Chạy gateway

```bash
docker-compose up -d --build
docker-compose logs -f        # theo dõi log; Ctrl+C để thoát (container vẫn chạy)
```

Thấy log gateway bắt đầu polling và không có lỗi auth là OK.

## Thử ngay — project demo đầu tiên

Mở Telegram, tìm bot vừa tạo, nhắn:

```
Tôi muốn xây một web app quản lý công việc nhóm
```

Hệ thống sẽ:

1. Hỏi vài câu để hiểu rõ project (intake wizard).
2. Gửi thông báo: `🔔 Run ... tới Gate 1 — trả lời để duyệt`.
3. Bạn xem rồi nhắn `ok` / `duyệt` để tiếp tục.
4. Hệ thống sinh spec, dựng task graph, tới Gate 2, rồi thực thi và verify.

## Thêm project mới (bot mới)

```bash
ai-dev telegram setup     # chạy lại, thêm bot thứ 2 vào .env
docker-compose restart
```

## Quản lý container

```bash
docker-compose ps         # trạng thái
docker-compose logs -f    # xem log
docker-compose restart    # nạp lại .env sau khi sửa
docker-compose down       # dừng (dữ liệu /data vẫn còn trong volume)
```

## Troubleshooting

- **Bot không trả lời** → `docker-compose logs gateway` để xem lỗi.
- **`claude: command not found` trong container** → rebuild: `docker-compose build --no-cache`.
- **Lỗi auth / "not logged in"** → chạy `claude login` trên Windows host, rồi `docker-compose restart`. Kiểm tra `CLAUDE_AUTH_DIR` trỏ đúng `.claude` và dùng forward slash.
- **Bot không phản hồi đúng người** → kiểm tra `chat_ids` trong `.env` đúng ID của bạn (chạy lại `ai-dev telegram setup`).
- **Sửa `.env` nhưng không có tác dụng** → phải `docker-compose restart` (hoặc `up -d`) để nạp lại.

## Image lớn (~800MB)?

Bình thường — image gồm Python + Node.js + `claude` CLI. Đây là đánh đổi để dùng
Claude Max miễn phí trong container.
