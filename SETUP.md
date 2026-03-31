# Hướng dẫn cài đặt và sử dụng

---

## 1. Prerequisites

| Yêu cầu | Phiên bản | Ghi chú |
|---|---|---|
| Python | **3.12 hoặc 3.13** | ⚠️ Python 3.14 chưa được hỗ trợ (xem bên dưới) |
| PostgreSQL | 15+ | Local hoặc remote |
| Claude Code CLI | latest | `claude` trong PATH |
| API key | Anthropic hoặc OpenAI | |

### ⚠️ Lỗi thường gặp: Python 3.14 không tương thích

```
ERROR: No matching distribution found for crewai>=0.51
```

**Nguyên nhân:** `crewai>=0.51` chỉ hỗ trợ Python `>=3.10,<3.14`. Python 3.14 quá mới.

**Fix:** Cài Python 3.12 từ https://python.org/downloads/ rồi dùng:

```bash
py -3.12 -m pip install -e ".[dev]"
```

---

## 2. Cài đặt

```bash
git clone <repo>
cd ai-development-system
```

### ⚠️ Lỗi thường gặp: `pip` không có trong PATH

```
'pip' is not recognized as an internal or external command
```

**Fix:** Thay `pip` bằng `python -m pip` (hoặc `py -3.12 -m pip`):

```bash
# Thay vì:
pip install -e ".[dev]"

# Dùng:
python -m pip install -e ".[dev]"

# Hoặc nếu có nhiều Python:
py -3.12 -m pip install -e ".[dev]"
```

---

## 3. Cấu hình `.env`

Tạo file `.env` ở root project. Chọn **một trong hai** provider:

### Dùng Anthropic

```env
DATABASE_URL=postgresql://user:password@host/dbname
STORAGE_ROOT=/tmp/ai-dev-storage

LLM_PROVIDER=anthropic
LLM_MODEL=claude-opus-4-6
ANTHROPIC_API_KEY=sk-ant-...

# AI_DEV_STUB_LLM=1
```

### Dùng OpenAI (chỉ có OpenAI key)

```env
DATABASE_URL=postgresql://user:password@host/dbname
STORAGE_ROOT=/tmp/ai-dev-storage

LLM_PROVIDER=openai
LLM_MODEL=gpt-4o
OPENAI_API_KEY=sk-...

# AI_DEV_STUB_LLM=1
```

> **Lưu ý:** Không cần set key của provider kia. Nếu `LLM_PROVIDER=openai` thì `ANTHROPIC_API_KEY` bỏ trống hoặc không có cũng không sao.

### Các model được khuyến nghị

| Provider | Model | Ghi chú |
|---|---|---|
| Anthropic | `claude-opus-4-6` | Mạnh nhất, chậm hơn |
| Anthropic | `claude-sonnet-4-6` | Cân bằng tốt |
| OpenAI | `gpt-4o` | Mặc định cho OpenAI |
| OpenAI | `gpt-4o-mini` | Nhanh hơn, rẻ hơn |

---

## 4. Khởi tạo Database

Chạy migrations theo thứ tự. Nếu không có `psql`, dùng Python:

```bash
python -m python - <<'EOF'
import psycopg, os
conn = psycopg.connect(os.environ["DATABASE_URL"])
conn.autocommit = True
for f in [
    "docs/schema/control-layer-schema.sql",
    "docs/schema/migrations/v2-execution-runner.sql",
    "docs/schema/migrations/v3-debate-engine.sql",
    "docs/schema/migrations/v4-verification.sql",
]:
    conn.execute(open(f).read())
    print(f"Applied: {f}")
conn.close()
EOF
```

Hoặc nếu có `psql`:

```bash
psql $DATABASE_URL -f docs/schema/control-layer-schema.sql
psql $DATABASE_URL -f docs/schema/migrations/v2-execution-runner.sql
psql $DATABASE_URL -f docs/schema/migrations/v3-debate-engine.sql
psql $DATABASE_URL -f docs/schema/migrations/v4-verification.sql
```

---

## 5. Verify cài đặt

```bash
# Unit tests (không cần DATABASE_URL)
python -m pytest tests/unit/ -q

# Integration tests (cần DATABASE_URL)
export $(cat .env | xargs)
python -m pytest tests/integration/ -q
```

Kết quả mong đợi: `202 passed` (unit) và `60 passed` (integration).

---

## 6. Sử dụng — Luồng 3 bước

### Bước 1: `/start-project`

```
/start-project "Xây forum chia sẻ kiến thức nội bộ công ty"
```

Claude hỏi constraints và tên project, sau đó tự chạy:
- Normalize ý tưởng → initial brief
- Sinh câu hỏi (REQUIRED / STRATEGIC / OPTIONAL)
- AI debate tự động (~2-5 phút)

**Output:**
```
✅ Phase A hoàn tất.
   Run ID    : abc123-...
   Questions : 8 tổng (1 ESCALATE, 7 RESOLVED)

→ Chạy /review-debate --run-id abc123-... để bắt đầu Gate 1.
```

---

### Bước 2: `/review-debate <run_id>`

```
/review-debate --run-id abc123-...
```

Claude dẫn qua Gate 1 (4 states):

1. **PRESENT** — Hiển thị toàn bộ kết quả debate
2. **COLLECT_FORCED** — Bạn quyết định các câu `ESCALATE_TO_HUMAN`:
   ```
   Q6 đồng ý moderator
   ```
3. **COLLECT_CONSENSUS** — Confirm các câu AI đã resolved:
   ```
   approve all
   ```
4. **CONFIRM** — Xem lại tóm tắt, xác nhận → ghi `decision_log.json`, build spec, sinh task graph

---

### Bước 3: `/review-verification <run_id>`

Sau khi execution hoàn tất:

```
/review-verification --run-id abc123-...
```

Claude hiển thị verification report. Với mỗi FAIL criterion, chọn:
- `fix` → spawn remediation, chạy lại (tối đa 3 lần)
- `skip` → bỏ qua criterion này

Nếu tất cả pass → run status = `COMPLETED`.

---

## 7. Stub mode (không tốn API credit)

Thêm vào `.env`:

```env
AI_DEV_STUB_LLM=1
```

Toàn bộ pipeline chạy với LLM giả — đủ để test flow mà không gọi API thật.
