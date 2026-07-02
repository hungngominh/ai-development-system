# AI Development System — Project Overview

> Ngày: 2026-07-02 · Tác giả: Hung Ngo Minh

---

## 1. Vấn đề

Developer mất phần lớn thời gian cho **công việc lặp đi lặp lại**:

- Viết spec → viết plan → implement → tạo PR → review → sửa → merge
- Mỗi bước đòi hỏi context switching, chờ đợi, và dễ mắc lỗi nhỏ

**Câu hỏi:** Nếu AI có thể thay thế toàn bộ pipeline đó — từ một câu mô tả ngắn đến một PR hoàn chỉnh — thì developer chỉ cần làm gì?

> **Chỉ cần phê duyệt.**

---

## 2. Ý tưởng cốt lõi

```
Developer gõ một câu vào Telegram
        ↓
AI tự sinh Spec → Plan → Code → Test → PR
        ↓
Developer đọc, bấm "duyệt" hoặc phản hồi
        ↓
PR lên GitHub
```

Developer là **người phê duyệt**, không phải người thực hiện.

---

## 3. Kiến trúc tổng thể

```
┌─────────────────────────────────────────────────────────┐
│                      SURFACES                           │
│   Telegram Bot   │   Web UI   │   Local REPL (CLI)      │
└────────────┬─────────────┬────────────┬─────────────────┘
             │             │            │
             ▼             ▼            ▼
┌─────────────────────────────────────────────────────────┐
│                   GATEWAY DAEMON                        │
│  • Long-poll Telegram (multi-bot)                       │
│  • Route tin nhắn → Assistant                           │
│  • Proactive push (spec done / error / clarify)         │
└───────────────────────┬─────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────┐
│                    ASSISTANT (Hermes)                   │
│  • Owned tool-use loop (Claude Agent SDK + Max sub)     │
│  • Memory: MEMORY.md + USER.md (cross-session)          │
│  • Crash-resumable sessions (SQLite)                    │
│  • Budget tracking                                      │
└───────────────────────┬─────────────────────────────────┘
                        │  calls tools
                        ▼
┌─────────────────────────────────────────────────────────┐
│                  PIPELINE TOOLS                         │
│                                                         │
│  dev_task_start   dev_run_status   dev_answer_gate      │
│  dev_answer_clarify                                     │
└────────────┬────────────────────────────────────────────┘
             │ spawns subprocess
             ▼
┌─────────────────────────────────────────────────────────┐
│               SINGLE-TASK PIPELINE (Worker)             │
│                                                         │
│  1. SPEC GENERATION                                     │
│     └─ Agentic (claude CLI) → spec + facets             │
│     └─ Grounding + repair (traceability vs codebase)    │
│     └─ Self-review critic (4 dims: placeholder /        │
│        ambiguity / scope / consistency)                 │
│     └─ Clarify watcher → hỏi dev qua Telegram nếu cần  │
│                                                         │
│  2. PLAN GENERATION                                     │
│     └─ Task graph deterministic từ spec                 │
│     └─ TDD gate: test-spec trước, impl sau             │
│     └─ Dev review → approve / sửa                      │
│                                                         │
│  3. EXECUTION                                           │
│     └─ Branch tạo tự động                              │
│     └─ Agentic executor (claude CLI) chạy từng task     │
│     └─ Idle watchdog: kill nếu im lặng > 180s          │
│     └─ Failure learning: ghi rule vào .ai-dev/rules     │
│                                                         │
│  4. PR CREATION                                         │
│     └─ gh pr create → link gửi về Telegram             │
└─────────────────────────────────────────────────────────┘
```

---

## 4. Flow từ tin nhắn đến PR

```
[Telegram] "Thêm endpoint GET /users/:id trả về profile"
    │
    ▼
[Assistant] nhận, gọi dev_task_start
    │
    ▼
[Worker - Spec phase]
    ├─ Claude CLI phân tích codebase
    ├─ Sinh spec (acceptance criteria, edge cases, constraints)
    ├─ Kiểm tra spec có mâu thuẫn với code thực tế không?
    ├─ Self-review critic chạy 4 chiều
    └─ Nếu cần làm rõ → hỏi dev qua Telegram bot
    │
    ▼ (spec OK)
[Worker - Plan phase]
    ├─ Tạo task graph (test-spec tasks + impl tasks)
    ├─ Plan lưu vào repo: .ai-dev/tasks/<name>-plan.md
    └─ Link GitHub gửi về Telegram: "📋 Plan sẵn sàng"
    │
    ▼ Dev bấm "duyệt" hoặc gửi phản hồi
[Worker - Exec phase]
    ├─ Tạo branch: ai-dev/<task>
    ├─ Chạy từng task: test-spec → impl (TDD)
    ├─ Idle watchdog giữ claude CLI sống
    └─ Failure learning ghi lại bài học
    │
    ▼
[PR Created]
    └─ "✅ PR: github.com/.../pull/42"
```

---

## 5. Multi-bot, Multi-project

Một gateway phục vụ nhiều dự án đồng thời:

```
Gateway Container
├── Bot @sigo_backend_bot  → repo /repos/sigo-backend
├── Bot @mobile_api_bot    → repo /repos/mobile-api
└── (non-repo bot)         → new-project intake flow
```

- Mỗi bot có **token riêng** (BotFather)
- Mỗi project có **database riêng**: `<repo>/.ai-dev/state/control.db`
- Spec + Plan được commit vào repo: `<repo>/.ai-dev/tasks/*.md`
- Data tự động isolated — không lẫn giữa các project

---

## 6. Vòng lặp học hỏi (Failure Learning)

```
Task chạy thất bại
    │
    ▼
LLM phân tích lỗi → sinh rule ngắn gọn
    │
    ▼
Rule ghi vào: <repo>/.ai-dev/rules  (per-project)
              ~/.ai-dev-system/rules (global)
    │
    ▼
Lần chạy sau: rules nạp vào system prompt của executor
    │
    ▼
Không mắc lỗi cũ nữa
```

---

## 7. Proactive Push

Bot **chủ động báo** thay vì chờ được hỏi:

| Sự kiện | Bot gửi |
|---------|---------|
| Spec xong | 📄 Spec sẵn sàng, đợi duyệt |
| Spec lỗi | ❌ Spec thất bại: \<lý do\> |
| Spec cần làm rõ | ❓ \<câu hỏi cụ thể\> |
| Plan xong | 📋 Plan sẵn sàng — \<GitHub link\> |
| PR tạo xong | ✅ PR: \<url\> |
| Exec thất bại | ❌ Task thất bại: \<lý do\> |

---

## 8. Tech Stack

| Thành phần | Công nghệ |
|-----------|-----------|
| AI engine | Claude (claude CLI / Claude Agent SDK via Max subscription) |
| Language | Python 3.11 |
| Database | SQLite (per-project + global) |
| Messaging | Telegram Bot API (long-poll) |
| VCS | Git + GitHub (`gh` CLI) |
| Deploy | Docker + docker-compose |
| Testing | pytest (1976 tests, 0 failed) |

---

## 9. Trạng thái hiện tại (2026-07-02)

### Đã hoàn thành

- [x] **Hermes MVP** — 7 plans, hoàn chỉnh: owned harness → Telegram gateway → multi-bot → spec self-review
- [x] **Single-task pipeline** — spec → plan → exec → PR (TDD-first)
- [x] **Multi-project isolation** — per-repo DB, per-repo storage, docker compose tự cấu hình
- [x] **Clarify in Telegram** — bot hỏi khi spec cần làm rõ
- [x] **Idle watchdog** — kill claude CLI khi im lặng, không phải timeout cứng
- [x] **SpecStatusWatcher** — proactive push khi spec done/error
- [x] **Failure learning** — per-project + global rules từ task failures
- [x] **Per-step model/effort** — debate/executor/judge dùng opus, grounding dùng sonnet

### Còn lại

- [ ] Live smoke test với Sigo-Backend trên Docker (user thực hiện)
- [ ] SP-5: Native TUI using resolve_project(cwd)
- [ ] Multi-task graph từ chat (new-project flow hoàn chỉnh)

---

## 10. Roadmap tiếp theo

```
Phase hiện tại (MVP)          Phase tiếp theo
─────────────────────         ─────────────────────────
Single-task, one bot    →     Multi-task graph từ chat
Manual smoke            →     CI/CD tự động
Per-project learning    →     Cross-project knowledge base
Telegram + WebUI        →     Discord / TUI / Voice
Docker manual deploy    →     One-command production setup
```

---

## 11. Tóm tắt

> **Developer gõ → AI làm → Developer duyệt → PR lên**

Hệ thống này không thay thế developer. Nó **loại bỏ phần nhàm chán nhất** của công việc — để developer tập trung vào phán đoán, thiết kế, và phê duyệt.

Toàn bộ pipeline chạy trong container Docker, self-contained, không cần API key ngoài Claude Max subscription.

---

*1976 tests · 0 failed · master @ b587d1d*
