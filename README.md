# AI Development System

Hệ thống tự động hóa phát triển phần mềm theo mô hình **Human-as-Approver**: AI tranh luận, lập kế hoạch, và thực thi — con người chỉ duyệt tại các gate quan trọng.

---

## Mô hình hoạt động

```
Ý tưởng thô
    │
    ▼
[AI debate] ──── 2 agent tranh luận mỗi câu hỏi, Moderator tổng hợp
    │
    ▼
[Gate 1] ──────── Bạn duyệt kết quả debate, quyết định ESCALATE items
    │
    ▼
[Build spec] ─── 5 artifact cố định: proposal, design, functional, non-functional, acceptance-criteria
    │
    ▼
[Task graph] ─── AI sinh tasks + dependencies + metadata đầy đủ
    │
    ▼
[Gate 2] ──────── Bạn approve/sửa task graph
    │
    ▼
[Execution] ──── CrewAI thực thi theo dependency order, retry tự động
    │
    ▼
[Gate 3] ──────── Bạn duyệt verification report, quyết định fix/skip/abort
    │
    ▼
COMPLETED
```

**Con người làm:** Duyệt debate report (~5 phút) + Approve task graph (~2 phút) + Review verification (~5 phút)

**AI làm:** Mọi thứ còn lại.

---

## Kiến trúc

```
src/ai_dev_system/
├── intake/               # Intake wizard — front door: normalize brief + sinh câu hỏi
├── debate/               # AI debate engine (agents, rounds, moderator)
├── gate/                 # Gate 1 review — approval logic + bridges
├── spec/                 # Build 5-artifact spec bundle (planner + grounding + repair + tracer)
├── task_graph/           # Sinh + validate task graph (+ single-task executor)
├── rules/                # Rule Registry — inject rules vào agent
├── engine/               # Execution runner (worker loop, retry, escalation) — ⚠️ multi-task partial
├── agents/               # LLM provider wrapper — ClaudeMaxAgent qua `claude` CLI
├── verification/         # LLM judge acceptance criteria
├── eval/                 # Eval harness — golden dataset + 18 metrics
├── beads/                # Beads audit trail sync
├── storage/              # Artifact storage trên disk
├── migration/            # Schema migration helpers
├── db/                   # SQLite repos (stdlib sqlite3, không cần driver)
└── cli/                  # CLI entry points
```

> Các module top-level đi kèm: `normalize.py`, `spec_bundle.py`, `finalize_spec.py`,
> `pipeline.py`, `webui.py`, `feature_flags.py`, `config.py`.

**Skills (Claude Code slash commands):**

| Command | Chức năng |
|---|---|
| `/start-project` | Phase 1a — nhận ý tưởng, chạy debate pipeline |
| `/review-debate` | Gate 1 — duyệt debate report, ghi decision log |
| `/review-verification` | Gate 3 — duyệt verification report, đóng run |

## Trạng thái

- **1620 tests** — tất cả pass (SQLite in-memory, không cần DB ngoài)
- Đầy đủ pipeline từ **intake wizard** → debate → spec → task graph → verification
- Persistence: **SQLite** (stdlib `sqlite3`, không cần driver) với full audit trail
- Execution: **single-task** đã hoạt động (TDD-first: test phase → impl phase); ⚠️ **multi-task** graph execution (required_inputs/promoted_outputs) chưa hoàn chỉnh
- LLM provider: `ClaudeMaxAgent` qua `claude` CLI — không cần API key

---

## Bắt đầu

Xem [SETUP.md](SETUP.md) để cài đặt và chạy.
