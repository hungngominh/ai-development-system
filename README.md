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
├── normalize.py          # Normalize ý tưởng thô → initial brief
├── debate/               # AI debate engine (agents, rounds, moderator)
├── gate/                 # Gate 1, 2, 3 — approval logic + bridges
├── spec_bundle.py        # Build 5-artifact spec bundle
├── finalize_spec.py      # Finalize spec sau Gate 1
├── task_graph/           # Sinh + validate task graph
├── rules/                # Rule Registry — inject rules vào agent
├── engine/               # Execution engine (worker loop, retry, escalation)
├── agents/               # CrewAI agent wrapper
├── verification/         # LLM judge acceptance criteria
├── beads/                # Beads audit trail sync
├── storage/              # Artifact storage trên disk
├── db/                   # PostgreSQL repos
└── cli/                  # CLI entry points
```

**Skills (Claude Code slash commands):**

| Command | Chức năng |
|---|---|
| `/start-project` | Phase 1a — nhận ý tưởng, chạy debate pipeline |
| `/review-debate` | Gate 1 — duyệt debate report, ghi decision log |
| `/review-verification` | Gate 3 — duyệt verification report, đóng run |

---
```mermaid
sequenceDiagram
    actor U as User
    participant W as webui
    participant X as Executor
    participant G as Git
    participant E as Engine
    participant R as Router
    participant TA as TestAuthor
    participant TR as TestReviewer
    participant IM as Implementer
    participant RV as Reviewer
    participant C as claude
    participant DB as DB

    U->>W: Approve task spec
    W->>X: spawn executor
    X->>G: checkout -b ai-dev/task
    X->>DB: create run + TASK_GRAPH [TEST, IMPL deps TEST]
    X->>E: run_execution agent=Router
    E->>DB: materialize 2 task_runs

    Note over E,DB: TASK-TEST ready first

    E->>R: run TASK-TEST phase=test
    R->>TA: delegate
    loop test-review-repair max N
        TA->>C: write tests from AC, no impl
        C->>G: write tests, run must be RED, commit
        TA->>TR: review tests vs AC
        TR->>C: check coverage / red / tautology
        C-->>TR: verdict
        alt blocking
            TR-->>TA: fix and repeat
        else clean
            TR-->>TA: pass
        end
    end
    TA-->>E: success
    E->>DB: TASK-TEST SUCCESS, resolve deps, IMPL ready

    Note over E,DB: TASK-IMPL ready after TEST

    E->>R: run TASK-IMPL phase=implementation
    R->>IM: delegate
    IM->>C: make RED tests pass, do not weaken tests
    C->>G: write code, run tests GREEN, commit
    loop review-repair max N
        IM->>RV: review diff + tests vs AC
        RV->>C: run suite, check integration and weakened tests
        C-->>RV: verdict
        alt blocking
            RV-->>IM: fix and repeat
        else clean
            RV-->>IM: pass
        end
    end
    IM-->>E: success
    E->>DB: TASK-IMPL SUCCESS
    E-->>X: COMPLETED
    X->>DB: exec status done

    U->>W: open task-exec, view diff
    alt Accept
        U->>W: Accept
        W->>G: push + gh pr create
    else Reject
        U->>W: Reject, delete branch, mint learning rule
    end
```
## Trạng thái

- **262 tests** (204 unit + 60 integration) — tất cả pass
- Đầy đủ pipeline từ normalize → verification
- PostgreSQL-backed với full audit trail

---

## Bắt đầu

Xem [SETUP.md](SETUP.md) để cài đặt và chạy.
