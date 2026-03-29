# Luồng dữ liệu v2: Human-as-Approver

Biểu đồ này thể hiện luồng dữ liệu trong mô hình mới, nơi con người chỉ duyệt thay vì tự tay làm.
So với v1: thêm Debate Crew, Approval Gates, và Task Graph Generator.

```mermaid
sequenceDiagram
    participant U as Con nguoi
    participant SP as Superpowers
    participant DC as Debate Crew
    participant MOD as Moderator
    participant AA as Agent A
    participant AB as Agent B
    participant OS as OpenSpec
    participant TG as Task Generator
    participant BD as Beads
    participant CR as CrewAI
    participant AG as agency-agents
    participant CM as CrewAI Memory

    Note over U,CM: === PHASE 1a: NHAP Y TUONG + SINH CAU HOI ===
    U->>SP: Y tuong tho ("Forum chia se kien thuc")
    SP->>SP: Superpowers brainstorming sinh N cau hoi

    Note over U,CM: === PHASE 1b: AI DEBATE ===
    SP->>DC: Chuyen N cau hoi sang Engine
    DC->>DC: run_debate: bat dau tranh luan
    loop Moi cau hoi
        DC->>AG: Chon cap agent theo domain
        AG-->>AA: Load backstory Agent A
        AG-->>AB: Load backstory Agent B
        AA->>AA: Buoc 1: Dua ra quan diem
        AB->>AB: Buoc 2: Phan bien
        AA->>AA: Buoc 3: Phan hoi
        MOD->>MOD: Buoc 4: Tong hop + Confidence
    end
    DC-->>DC: Tao Debate Report

    Note over U,CM: === GATE 1: DUYET DAP AN ===
    DC-->>U: Debate Report<br/>(FORCED truoc, CONSENSUS cuoi)
    U->>U: Review FORCED items (bat buoc)<br/>Confirm CONSENSUS items (1-click)
    U-->>DC: Dap an da duyet/override

    Note over U,CM: === PHASE 1d: FORMALIZE ===
    DC->>OS: Dap an da duyet
    OS->>OS: Tao proposal.md + specs/ + design.md

    Note over U,CM: === PHASE 2a: SINH TASK GRAPH ===
    OS->>TG: Spec lam dau vao
    TG->>TG: Planner xac dinh tasks
    TG->>TG: Architect xac dinh dependencies
    TG-->>TG: Task graph (JSON)

    Note over U,CM: === GATE 2: DUYET TASK GRAPH ===
    TG-->>U: Task Graph Report<br/>(tasks + dependencies)
    U->>U: Review, approve/edit/reject
    alt Reject
        U->>TG: Reject -> regenerate_task_graph
        TG->>TG: Sinh lai task graph
        TG-->>U: Task Graph Report moi
        U->>U: Review lai
    end
    U-->>BD: Approved tasks<br/>bd create + bd dep add

    Note over U,CM: === PHASE 3: THUC THI ===
    BD->>CR: Tasks da len ke hoach
    CR->>AG: Doc agent prompts
    AG-->>CR: Role definitions
    Note right of CR: Rule Registry: match rules theo task type/tags
    CR->>CR: Inject file rules vao agent backstory
    SP->>SP: Invoke skill rules (TDD, etc.)
    CR->>CM: Luu shared memory
    loop Moi task trong graph
        CR->>CR: Dieu phoi agent (voi rules da inject)
        SP->>CR: Quality gates
        BD->>BD: Cap nhat status + audit trail
        CR->>CM: Luu ket qua
    end

    Note over U,CM: === PHASE 4-5: KIEM TRA & BAO CAO (giu nguyen v1) ===
    SP->>CR: Verification
    SP->>CR: Code Review
    BD->>BD: Bao cao tong ket
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
U -> SP -> DC(debate) -> [GATE 1] -> OS -> TG(gen tasks) -> [GATE 2] -> CR -> SP -> BD -> U
                          duyệt                               duyệt
```

## Điểm khác biệt chính

| Bước | v1 Data | v2 Data |
|---|---|---|
| Brainstorming | SP hỏi -> User trả lời (text) | SP sinh câu hỏi -> DC tranh luận -> Report (structured) |
| Quyết định | User suy nghĩ + trả lời | AI debate + status -> User approve/override |
| Tạo task | User chạy `bd create` (manual) | TG sinh JSON -> User approve (1-click) |
| Dependency | User chạy `bd dep add` (manual) | TG tự phân tích -> User approve (1-click) |
| Execution | Giữ nguyên | Giữ nguyên |
