# Luồng dữ liệu v2: Human-as-Approver

Biểu đồ này thể hiện luồng dữ liệu trong mô hình mới, nơi con người chỉ duyệt thay vì tự tay làm.
So với v1: thêm Idea Normalization, Question Classification, Debate Crew, Decision Log, Approval Gates, Task Graph Generator, và Failure Handling.

```mermaid
sequenceDiagram
    participant U as Con nguoi
    participant SK as Skill
    participant SP as Superpowers
    participant DC as Debate Crew
    participant MOD as Moderator
    participant AA as Agent A
    participant AB as Agent B
    participant OS as OpenSpec
    participant TG as Task Generator
    participant RR as Rule Registry
    participant BD as Beads
    participant CR as CrewAI
    participant AG as agency-agents

    Note over U,AG: === PHASE 1a: NORMALIZE + SINH CAU HOI ===
    U->>SK: Y tuong tho ("Forum chia se kien thuc")
    SK->>SK: Normalize → initial brief (problem, users, criteria, constraints)
    SK->>SP: Superpowers brainstorming
    SP-->>SK: N cau hoi phan loai (REQUIRED/STRATEGIC/OPTIONAL)

    Note over U,AG: === PHASE 1b: AI DEBATE ===
    SK->>DC: run_debate (REQUIRED + STRATEGIC questions)
    loop Moi cau hoi
        DC->>AG: Chon cap agent theo domain
        AG-->>AA: Load backstory Agent A
        AG-->>AB: Load backstory Agent B
        AA->>AA: Buoc 1: Dua ra quan diem
        AB->>AB: Buoc 2: Phan bien
        AA->>AA: Buoc 3: Phan hoi (lap toi da 5 vong)
        MOD->>MOD: Buoc 4: Resolution status + confidence
        Note right of MOD: RESOLVED / RESOLVED_WITH_CAVEAT<br/>ESCALATE_TO_HUMAN / NEED_MORE_EVIDENCE
    end
    DC-->>SK: Debate Report (structured)

    Note over U,AG: === GATE 1: DUYET + DECISION LOG ===
    SK-->>U: Debate Report<br/>(ESCALATE_TO_HUMAN truoc, RESOLVED cuoi)
    U->>U: Quyet dinh ESCALATE items (bat buoc)<br/>Confirm RESOLVED items (1-click)
    U-->>SK: Dap an da duyet
    SK->>SK: Ghi decision_log.json (trace moi quyet dinh)

    Note over U,AG: === PHASE 1d: FORMALIZE (contract co dinh) ===
    SK->>OS: finalize_spec
    OS->>OS: proposal + design + functional + non-functional + acceptance-criteria
    alt Spec mau thuan
        OS-->>SK: Conflict
        SK-->>U: Hoi resolve
        SK->>OS: finalize_spec lai
    end

    Note over U,AG: === PHASE 2a: SINH TASK GRAPH (voi metadata) ===
    OS->>TG: Spec lam dau vao
    TG->>TG: Planner xac dinh tasks (voi agent_type, done_definition)
    TG->>TG: Architect xac dinh dependencies
    TG-->>SK: Task graph JSON (day du metadata)

    Note over U,AG: === GATE 2: DUYET TASK GRAPH ===
    SK-->>U: Task Graph Report
    U->>U: Review, approve/edit/reject
    alt Reject
        U->>SK: Reject
        SK->>TG: regenerate_task_graph
        TG-->>SK: Task Graph moi
        SK-->>U: Present lai
    end
    SK->>BD: bd create + bd dep add

    Note over U,AG: === PHASE 3: RULE MATCHING + EXECUTION + FAILURE HANDLING ===
    loop Moi task (theo dependency order)
        SK->>RR: match-rules (task type/tags)
        RR-->>SK: file_rules + skill_rules
        SK->>SP: Invoke skill_rules
        SK->>CR: execute-task (enriched backstory)
        CR->>AG: Agent thuc thi
        alt Task fail
            CR-->>SK: Fail → retry (max 2)
            alt Van fail
                SK-->>U: Escalate: skip/fix/abort?
            end
        end
        SK->>SP: Verification
        alt Verification fail
            SP-->>SK: Fail → re-verify (max 3)
            alt Van fail
                SK-->>U: Escalate
            end
        end
        BD->>BD: Cap nhat status + audit trail
    end

    Note over U,AG: === PHASE 4-5: TONG KET ===
    SP->>U: Verification report
    BD->>U: Audit trail + thong ke
    CR->>U: San pham hoan chinh
```

## So sánh Data Flow v1 vs v2

### v1: Con người ở giữa mỗi bước

```
U -> SP -> U -> OS -> U -> BD -> CR -> SP -> BD -> U
     hỏi    trả lời   tạo task       thực thi
```

### v2: Con người chỉ ở 2 approval gates

```
U -> Normalize -> SP(questions) -> DC(debate) -> [GATE 1+log] -> OS -> TG -> [GATE 2] -> CR(+rules+retry) -> U
```

## Điểm khác biệt chính

| Bước | v1 Data | v2 Data |
|---|---|---|
| Input | Ý tưởng thô | Ý tưởng thô → normalized brief |
| Brainstorming | SP hỏi → User trả lời | SP sinh câu hỏi phân loại → DC tranh luận → Report |
| Quyết định | User suy nghĩ + trả lời | Resolution status → User approve/override + decision log |
| Spec | Free-form | 5 artifact cố định (contract) |
| Tạo task | User chạy `bd create` (manual) | TG sinh JSON với metadata → User approve |
| Dependency | User chạy `bd dep add` (manual) | TG tự phân tích → User approve |
| Execution | Không retry | Retry policy + escalate |
| Execution | Agent không biết rules | Rule Registry inject đúng rules theo task type |
