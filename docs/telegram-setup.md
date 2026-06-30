# Tạo Telegram bot (thủ công)

Tài liệu này mô tả cách làm thủ công. Cách nhanh hơn là chạy `ai-dev telegram setup`
(tự bắt `chat_id` và ghi `.env`).

## 1. Tạo bot qua @BotFather

1. Mở Telegram, tìm **@BotFather**.
2. Gửi `/newbot`.
3. Nhập tên hiển thị, ví dụ: `My Project Bot`.
4. Nhập username (phải kết thúc bằng `bot`), ví dụ: `my_project_123_bot`.
5. Copy token, dạng `1234567890:AAH...`.

## 2. Lấy chat_id thủ công

1. Nhắn bất kỳ tin nhắn nào tới bot vừa tạo.
2. Mở trình duyệt, thay `<TOKEN>` bằng token của bạn:
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. Tìm trường `"from": {"id": 5913726934}` — đó là `chat_id` của bạn.

## 3. Ghi vào .env (single-line JSON)

`AI_DEV_TELEGRAM_BOTS` phải nằm trên **một dòng** (python-dotenv không đọc value
nhiều dòng):

```
AI_DEV_TELEGRAM_BOTS=[{"label": "my-project", "token": "1234567890:AAH...", "chat_ids": [5913726934]}]
```

## 4. Nhiều project (nhiều bot)

Tạo bot riêng cho từng project, gộp vào cùng một mảng:

```
AI_DEV_TELEGRAM_BOTS=[{"label": "project-a", "token": "TOKEN_A", "chat_ids": [111111]}, {"label": "project-b", "token": "TOKEN_B", "chat_ids": [222222]}]
```

- `label` — tên nhận dạng project (chữ thường, gạch ngang). Cũng dùng để định tuyến
  thông báo run-status về đúng bot.
- `chat_ids` — danh sách Telegram user ID được phép dùng bot (allowlist).

Sau khi sửa `.env`, chạy `docker-compose restart`.
