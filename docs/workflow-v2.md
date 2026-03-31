# Luồng làm việc v2: Human-as-Approver

## So sánh v1 vs v2

| | v1 (Human-in-the-Loop) | v2 (Human-as-Approver) |
|---|---|---|
| Brainstorming | Con người trả lời câu hỏi | AI debate, con người duyệt |
| Tạo task | Con người tự tay tạo | AI sinh, con người duyệt |
| Execution | AI thực thi | AI thực thi (giữ nguyên) |
| Quality check | AI kiểm tra | AI kiểm tra (giữ nguyên) |
| Con người làm | Trả lời + tạo task + review | Chỉ review và approve |

## Luồng mới

```
Con người nhập ý tưởng thô
    |
    v
[Skill: Normalize idea → initial brief]
    problem, target users, success criteria, constraints, unknowns
    |
    v
[Skill: Superpowers brainstorming sinh + phân loại câu hỏi]
    REQUIRED (bắt buộc) / STRATEGIC (quan trọng) / OPTIONAL (nice-to-have)
    |
    v
[Engine: run_debate]
    Mỗi câu hỏi (REQUIRED + STRATEGIC) → 2 agent tranh luận:
    Vòng 1:
      1. Agent A: quan điểm + ưu/nhược
      2. Agent B: phản biện + quan điểm riêng
    Vòng 2+:
      1. Agent A: tiếp thu + điều chỉnh
      2. Agent B: tiếp thu + điều chỉnh
    Moderator: tổng hợp → resolution status:
      RESOLVED / RESOLVED_WITH_CAVEAT / ESCALATE_TO_HUMAN / NEED_MORE_EVIDENCE
    Stop khi: converge (confidence ≥ 0.8) hoặc max 5 vòng
    |
    v
[GATE 1: Con người duyệt debate report]
    - ESCALATE_TO_HUMAN: bắt buộc quyết định
    - NEED_MORE_EVIDENCE: cung cấp context hoặc quyết định
    - RESOLVED_WITH_CAVEAT: confirm + nhận diện caveat
    - RESOLVED: 1-click confirm
    → Skill ghi decision_log.json (trace mọi quyết định)
    |
    v
[Engine: finalize_spec → OpenSpec artifact cố định]
    proposal.md / design.md / functional.md / non-functional.md / acceptance-criteria.md
    |
    |-- Spec mâu thuẫn → Skill hỏi user resolve → retry
    |
    v
[Engine: Task Graph Generator]
    Sinh tasks với đầy đủ metadata:
    objective, agent_type, required_inputs, expected_outputs, done_definition, verification_steps
    |
    v
[GATE 2: Con người duyệt task graph]
    - Approve / Thêm task / Sửa dependency / Reject
    |
    |-- Reject → [regenerate_task_graph] → quay lại GATE 2
    |
    v (Approve)
[Rule Registry: match rules theo task type/tags]
    - File rules → inject vào agent backstory
    - Skill rules → Skill invoke trước execution
    |
    v
[Engine: execute-task (lặp theo dependency graph)]
    Mỗi task:
      → fail → retry (max 2 lần)
      → retry hết → Skill escalate user
    Verification sau mỗi task:
      → fail → agent sửa → re-verify (max 3 lần)
      → vẫn fail → Skill escalate user
    |
    v
[Superpowers verification tổng thể]
    |
    v
[Beads audit trail + report]
```

---

## Ví dụ: Forum chia sẻ kiến thức

### Phase 1a: Nhập ý tưởng + Normalize

```
Con người: "Xây forum chia sẻ kiến thức nội bộ công ty"

Skill normalize:
  Problem: Nhân viên không có chỗ chia sẻ kiến thức
  Users: Developers nội bộ
  Success: Post/search bài viết, tagging
  Constraints: Internal only
  Unknowns: Scale? Real-time cần không?
```

### Phase 1b: Phân loại câu hỏi + AI Debate

Skill sinh 8 câu hỏi đã phân loại, Engine debate REQUIRED + STRATEGIC:

| Câu hỏi | Loại | Agent A | Agent B | Status |
|---|---|---|---|---|
| Feature chính MVP? | REQUIRED | Product Manager | Backend Architect | RESOLVED (2 vòng) |
| Primary users? | REQUIRED | Product Manager | Backend Architect | RESOLVED (1 vòng) |
| Backend tech stack? | STRATEGIC | Backend Architect | DevOps Specialist | RESOLVED (3 vòng) |
| Frontend framework? | STRATEGIC | Backend Architect | DevOps Specialist | RESOLVED (2 vòng) |
| Database schema? | STRATEGIC | Database Specialist | Backend Architect | RESOLVED (2 vòng) |
| Authentication? | STRATEGIC | Security Specialist | Product Manager | ESCALATE_TO_HUMAN (5 vòng) |
| API endpoints? | STRATEGIC | Backend Architect | DevOps Specialist | RESOLVED (2 vòng) |
| Testing strategy? | OPTIONAL | QA Engineer | Backend Architect | RESOLVED (3 vòng) |

### Phase 1c: Gate 1 — Duyệt đáp án + Decision Log

Con người nhận Debate Report:

```markdown
## Cần bạn quyết định (ESCALATE_TO_HUMAN)
Q6: "Authentication?" — 5 vòng bất đồng, confidence 0.55
  Security Specialist: OAuth2 + JWT (stateless, scale tốt)
  Product Manager: Session + Redis (đơn giản, revoke ngay)
  Moderator đề xuất: OAuth2 + JWT + short expiry
  Caveat: Session revocation speed requirement chưa rõ
  → [x] Đồng ý moderator

## Đã resolved (RESOLVED) — confirm nhanh
Q1-Q5, Q7, Q8: Đã đồng thuận
  → [x] OK tất cả
```

Skill ghi `decision_log.json` — mọi quyết định, options đã xem xét, rationale, timestamp.

### Phase 2: Task Graph tự động (với metadata đầy đủ)

Sau khi duyệt, Engine sinh task graph:

```
TASK-1: Thiết kế PostgreSQL schema
  agent: Database Specialist | priority: high | risk: low
  inputs: functional.md, non-functional.md
  outputs: schema.sql, erd.md
  done: Schema cover đủ entities trong spec

TASK-2: Setup OAuth2 + JWT (blocked by TASK-1)
  agent: Security Specialist | priority: high | risk: medium

TASK-3: API endpoints FastAPI (blocked by TASK-1, TASK-2)
TASK-4: React + TypeScript setup (blocked by TASK-3)
TASK-5: UI components (blocked by TASK-4)
TASK-6: Testing + QA (blocked by TASK-3, TASK-5)
  verification: pytest pass, coverage ≥ 80%
```

Con người duyệt: **Approve** (hoặc sửa trước khi approve)

### Phase 3-5: Execution với failure handling

CrewAI + agency-agents thực thi theo dependency graph với rule injection.
Mỗi task: fail → retry (max 2) → escalate user.
Verification: fail → agent sửa → re-verify (max 3) → escalate user.
Superpowers kiểm tra (TDD, code review, verification).
Beads lưu audit trail.

---

## Sequence Diagram

```mermaid
sequenceDiagram
    participant U as Con nguoi
    participant SK as Skill (Interface)
    participant DC as Debate Crew
    participant AG as agency-agents
    participant OS as OpenSpec
    participant TG as Task Generator
    participant RR as Rule Registry
    participant SP as Superpowers
    participant CR as CrewAI
    participant BD as Beads

    Note over U,BD: Phase 1a — Normalize + Sinh cau hoi
    U->>SK: Y tuong tho
    SK->>SK: Normalize → initial_brief.json
    SK->>SP: Superpowers brainstorming (feed initial_brief)
    SP-->>SK: N cau hoi (REQUIRED/STRATEGIC/OPTIONAL)

    Note over U,BD: Phase 1b — AI Debate
    SK->>DC: run_debate (cau hoi da phan loai)
    loop Moi cau hoi REQUIRED + STRATEGIC
        DC->>AG: Chon cap agent theo domain
        AG-->>DC: Agent A + Agent B backstories
        DC->>DC: Debate (toi da 5 vong)
        DC->>DC: Moderator: resolution status + confidence
    end
    DC-->>SK: Debate results (structured)
    SK->>SK: Ghi debate_report.json

    Note over U,BD: Gate 1 — Duyet + Decision Log
    SK-->>U: ESCALATE_TO_HUMAN truoc, RESOLVED cuoi
    U->>U: Quyet dinh ESCALATE items (bat buoc)
    U-->>SK: Dap an da duyet
    SK->>SK: Ghi decision_log.json + approved_answers.json

    Note over U,BD: Phase 1d — Build spec bundle
    SK->>OS: build_spec_bundle (approved_answers.json)
    OS->>OS: proposal + design + functional + non-functional + acceptance-criteria
    alt Spec mau thuan
        OS-->>SK: Conflict warning
        SK-->>U: Hoi resolve
        U-->>SK: Resolve
        SK->>OS: build_spec_bundle lai
    end

    Note over U,BD: Phase 2a — Task Graph (voi metadata day du)
    OS->>TG: Spec bundle lam dau vao
    TG->>TG: Sinh tasks (voi agent_type, inputs, outputs, done_definition)
    TG-->>SK: task_graph.generated.json

    Note over U,BD: Gate 2 — Duyet task graph
    SK-->>U: Present task graph
    U->>U: Review, approve/edit/reject
    alt Reject
        U->>SK: Reject
        SK->>TG: regenerate_task_graph
        TG-->>SK: task_graph.generated.json moi
        SK-->>U: Present lai
    end
    U-->>SK: Approve (voi edits neu co)
    SK->>SK: Ghi task_graph.approved.json
    SK->>BD: bd create + bd dep add

    Note over U,BD: Phase 3 — Rule matching + Execution + Failure handling
    loop Moi task (theo dependency order)
        SK->>RR: match-rules
        RR-->>SK: file_rules + skill_rules
        SK->>SP: Invoke skill_rules
        SK->>CR: execute-task (voi enriched backstory)
        CR->>AG: Agent thuc thi
        alt Task fail
            CR-->>SK: Fail
            SK->>CR: Retry (max 2)
            alt Van fail
                SK-->>U: Escalate: skip/fix/abort?
            end
        end
        SK->>SP: Quality verification
        alt Verification fail
            SP-->>SK: Fail
            SK->>CR: Agent sua + re-verify (max 3)
            alt Van fail
                SK-->>U: Escalate
            end
        end
        BD->>BD: Cap nhat status + audit trail
    end
    SP->>U: Verification report
    BD->>U: Audit trail + thong ke
```

---

---

## Thiết kế Conversation Flow: Gate 1 (Skill)

Gate 1 chạy trong Claude Code Skill — không phải terminal `input()`. Skill dẫn dắt user qua 4 state tuần tự.

### State Machine

```
[PRESENT] → [COLLECT_FORCED] → [COLLECT_CONSENSUS] → [CONFIRM] → [DONE]
                                                           ↑           |
                                                           └───────────┘
                                                         (nếu user muốn sửa)
```

---

### State 1: PRESENT

Skill hiển thị debate report (markdown), rồi gửi **một message duy nhất** tóm tắt:

```
📋 Debate hoàn tất. 8 câu hỏi đã được debate.

❗ CẦN QUYẾT ĐỊNH (1 câu):
  Q6: Authentication — 5 vòng, confidence 0.55
    • Security: OAuth2 + JWT (stateless, scale tốt)
    • Product: Session + Redis (đơn giản, revoke ngay)
    • Moderator đề xuất: OAuth2 + JWT + short expiry
    ⚠️ Caveat: Session revocation speed requirement chưa rõ

✅ ĐÃ RESOLVED (7 câu): Q1, Q2, Q3, Q4, Q5, Q7, Q8

→ Quyết định Q6 để tiếp tục (hoặc "xem Q3" để xem chi tiết câu nào).
```

**Nguyên tắc:** Không hỏi từng câu một. Trình bày toàn bộ picture ngay.

---

### State 2: COLLECT_FORCED

Skill track `{ "Q6": None }` — chờ user điền. Chấp nhận mọi dạng input:

| User nói | Skill parse thành |
|----------|-------------------|
| `"Q6 đồng ý moderator"` | `Q6: APPROVED_MODERATOR` |
| `"Q6: JWT + Redis"` | `Q6: OVERRIDE("JWT + Redis")` |
| `"chọn option A cho Q6"` | `Q6: APPROVED_AGENT_A` |
| `"Q6 dùng Clerk.dev"` | `Q6: OVERRIDE("Clerk.dev")` |

Nếu parse ambiguous → clarify ngay (không để sai đi vào spec):
```
Bạn muốn dùng Clerk.dev cho authentication — hiểu đúng chưa?
```

Nếu còn FORCED chưa quyết định → nhắc nhẹ sau mỗi message của user:
```
Còn Q6 chưa quyết định. Bạn chọn gì?
```

**Chặn:** Nếu user nói `"approve all"` khi còn FORCED → Skill từ chối, giải thích FORCED không thể skip.

---

### State 3: COLLECT_CONSENSUS

Sau khi FORCED xong, hỏi một lần:

```
7 câu còn lại đã đồng thuận. Approve tất cả, hay muốn xem/sửa câu nào?
```

| User nói | Hành động |
|----------|-----------|
| `"approve all"` / `"ok hết"` | Mark tất cả `APPROVED_CONSENSUS`, sang CONFIRM |
| `"xem Q3"` | Show Q3 detail, hỏi quyết định, loop |
| `"approve all, Q4 dùng Vue thay React"` | Parse batch: Q4 → OVERRIDE, còn lại → APPROVED_CONSENSUS |

---

### State 4: CONFIRM

Skill show structured summary của toàn bộ 8 quyết định:

```
📝 Tóm tắt quyết định:

Q1 Feature MVP         → ✅ Consensus (forum + search + tags)
Q2 Primary users       → ✅ Consensus (internal developers)
Q3 Backend stack       → ✅ Consensus (FastAPI + PostgreSQL)
Q4 Frontend            → ✏️  Override: Vue.js (thay React)
Q5 Database schema     → ✅ Consensus (users/posts/tags)
Q6 Authentication      → ✏️  Approve moderator: OAuth2 + JWT
Q7 API endpoints       → ✅ Consensus (REST + OpenAPI)
Q8 Testing             → ✅ Consensus (pytest + Playwright)

Xác nhận để tiếp tục? (hoặc "sửa Q4" nếu muốn thay đổi)
```

- `"ok"` / `"xác nhận"` → Skill ghi `approved_answers.json`, gọi `finalize_spec`
- `"sửa Q4"` → quay về COLLECT_CONSENSUS cho Q4, re-CONFIRM

---

### Nguyên tắc thiết kế

| Nguyên tắc | Lý do |
|-----------|-------|
| Không hỏi từng câu một | Bị interrupt 8 lần = mất tập trung |
| Batch input được phép | Giữ triết lý Human-as-Approver |
| CONFIRM bắt buộc | Safety net trước `finalize_spec` (không undo được) |
| Ambiguous → clarify ngay | Không để sai đi sâu vào spec |
| FORCED trước CONSENSUS | Ưu tiên cái quan trọng trước |

### Edge Cases

1. **Skip FORCED** — `"approve all"` khi còn FORCED → Skill chặn, nhắc rõ
2. **Override mâu thuẫn nội bộ** — Skill flag cảnh báo, không chặn (user tự quyết)
3. **Đổi ý sau CONFIRM** — Skill cảnh báo đã gọi `finalize_spec`, hỏi có muốn `regenerate_task_graph` không

---

## Thay đổi so với v1

### Thêm mới

1. **Idea Normalization** — normalize ý tưởng thô thành initial brief trước khi brainstorming
2. **Question Classification** — REQUIRED / STRATEGIC / OPTIONAL để debate tập trung
3. **Debate Crew** — cơ chế tranh luận vòng lặp đối xứng (tối đa 5 vòng)
4. **Agent Pairing** — ghép cặp agent đối lập theo domain câu hỏi
5. **Moderator Structured Output** — resolution status (RESOLVED / ESCALATE_TO_HUMAN / NEED_MORE_EVIDENCE) thay vì binary
6. **Approval Gate 1** — duyệt đáp án debate
7. **Decision Log** — artifact traceability cho mọi quyết định tại Gate 1
8. **OpenSpec Output Contract** — 5 file cố định thay vì free-form
9. **Task Execution Metadata** — agent_type, inputs, outputs, done_definition, verification_steps
10. **Approval Gate 2** — duyệt task graph
11. **Task Graph Generator** — tự động sinh tasks và dependencies
12. **Failure / Retry Policy** — retry + escalate thay vì crash silently

### Giữ nguyên

- Phase 3: CrewAI + agency-agents execution
- Phase 4: Superpowers quality gates (TDD, code review, verification)
- Phase 5: Beads audit trail + reporting
- Toàn bộ cơ chế memory (LanceDB, Dolt, files)

### Con người chỉ cần làm

| Trước (v1) | Sau (v2) |
|---|---|
| Trả lời 8+ câu hỏi brainstorming | Duyệt report, chỉ quyết định FORCED items |
| Chạy `bd create` cho từng task | 1-click approve task graph |
| Chạy `bd dep add` cho từng dependency | Sửa nếu cần, approve |
| Tổng thời gian: 30-60 phút | Tổng thời gian: 5-10 phút |
