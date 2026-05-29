# Hướng dẫn cài đặt và sử dụng

---

## Quick Start (2 bước)

```bash
# 1. Cài đặt (cần Python 3.12 hoặc 3.13 — Python 3.14 chưa hỗ trợ)
pip install -e ".[dev]"

# 2. Setup tương tác — nhập DB, API key, tự chạy migration và cài skills
ai-dev setup
```

Xong. Mở Claude Code ở bất kỳ thư mục nào và gõ `/start-project`.

---

## Chi tiết từng bước

### 1. Cài đặt

```bash
git clone <repo>
cd ai-development-system
pip install -e ".[dev]"
```

> **Python 3.14?** `crewai>=0.51` chưa hỗ trợ. Dùng Python 3.12: `py -3.12 -m pip install -e ".[dev]"`

> **`pip` not found?** Dùng: `python -m pip install -e ".[dev]"`

### 2. Setup

```bash
ai-dev setup
```

Wizard hỏi lần lượt:

```
=== AI Dev System Setup ===

DATABASE_URL (sqlite:///path/to/file.db) [sqlite:///~/.ai-dev-system/control.db]:
STORAGE_ROOT [~/.ai-dev-system/storage]:
LLM Provider:
  1. anthropic
  2. openai
  3. azure
Choose [1/2/3]: 2
LLM_MODEL [gpt-4o]:
OPENAI_API_KEY: sk-...

Config saved to ~/.ai-dev-system/.env
Applying database schema...
  OK   schema applied
Installing Claude Code skills...
  OK   start-project.md
  OK   review-debate.md
  OK   review-verification.md

=== Setup complete ===
```

Config lưu tại `~/.ai-dev-system/.env` — tự động load, không cần export thủ công.

### 3. Chạy lại setup

Chạy `ai-dev setup` bất kỳ lúc nào để thay đổi config — wizard nhớ giá trị cũ.

---

## Sử dụng

### Qua Claude Code (khuyến nghị)

Mở Claude Code ở bất kỳ thư mục nào:

```
/start-project "Xây forum chia sẻ kiến thức"
```

Sau đó theo hướng dẫn: `/review-debate` → Gate 2 → `/review-verification`.

### Qua CLI trực tiếp

```bash
# Phase 1a: normalize + debate
ai-dev start --project-name "my-forum" --idea "Xay forum..." --constraints ""

# Phase B: spec → task graph → execution → verification
ai-dev run --run-id <run_id>
```

---

## LLM Providers

| Provider | Env vars cần thiết |
|---|---|
| `anthropic` | `ANTHROPIC_API_KEY` |
| `openai` | `OPENAI_API_KEY` |
| `azure` | `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_VERSION` |

> **Azure:** `LLM_MODEL` là **deployment name** (tên khi deploy trong Azure Portal), không phải tên model gốc.

---

## Stub mode (không tốn API credit)

Khi setup, chọn "Dung stub LLM? y" — toàn bộ pipeline chạy với LLM giả.

---

## Chạy tests

```bash
python -m pytest tests/ -q   # 406 passed, 9 xfailed — SQLite in-memory, không cần DB ngoài
```

---

## Database backend

Hệ thống dùng **SQLite** (stdlib Python — không cần cài driver). DB file mặc định:
`~/.ai-dev-system/control.db`. Override qua `DATABASE_URL` trong `.env`:

```
DATABASE_URL=sqlite:///path/to/your/control.db
DATABASE_URL=sqlite:///:memory:        # in-memory (test only)
```

PostgreSQL backend đã bị bỏ trong M0.5 để mọi máy đều chạy được không cần Postgres server.
