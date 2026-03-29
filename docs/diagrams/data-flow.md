# Luong du lieu chi tiet

Bieu do nay mo ta luong du lieu thuc te giua cac thanh phan trong he thong. Khac voi bieu do tong quan, o day the hien ro Superpowers tham gia o MOI giai doan, Beads theo doi XUYEN SUOT, va CrewAI doc agent prompts tu agency-agents lam backstory cho tung agent.

```mermaid
sequenceDiagram
    participant U as 👤 Nguoi dung
    participant SP as Superpowers<br/>Phuong phap
    participant OS as OpenSpec<br/>Quy trinh
    participant BD as Beads<br/>Luu vet
    participant CR as CrewAI<br/>Dieu phoi
    participant AG as agency-agents<br/>Chuyen mon
    participant CM as CrewAI Memory<br/>LanceDB

    Note over U,CM: === GIAI DOAN 1: Tiep nhan yeu cau ===

    U->>OS: Gui yeu cau moi
    SP->>OS: Brainstorming - phan tich yeu cau,<br/>kham pha y dinh nguoi dung
    OS->>OS: Tao Proposal → Specs → Design → Tasks

    Note over U,CM: === GIAI DOAN 2: Lap ke hoach ===

    OS->>BD: Specs thanh dau vao tao Beads tasks
    BD->>BD: Khoi tao Task Graph tu specs
    BD->>BD: Ghi Audit Trail - bat dau du an

    Note over U,CM: === GIAI DOAN 3: Thuc thi ===

    BD->>CR: Chuyen tasks da len ke hoach
    CR->>AG: Doc agent prompts lam backstory
    AG-->>CR: Tra ve role definitions,<br/>system prompts cho tung agent
    CR->>CM: Luu shared memory (cross-agent)
    CM-->>CR: Tra ve context tu phien truoc<br/>(cross-session memory)

    loop Moi task trong Task Graph
        CR->>CR: Dieu phoi agent (Sequential hoac Hierarchical)
        SP->>CR: TDD - viet test truoc, code sau
        BD->>BD: Cap nhat trang thai task,<br/>ghi audit trail
        CR->>CM: Luu ket qua vao memory
    end

    Note over U,CM: === GIAI DOAN 4: Kiem tra & Giao san pham ===

    SP->>CR: Verification - kiem tra toan bo output
    SP->>CR: Code Review - danh gia chat luong
    BD->>BD: Tao bao cao tong ket,<br/>ghi audit trail cuoi cung
    BD->>U: Bao cao ket qua
    CR->>U: Giao san pham hoan chinh

    Note over U,CM: === TRI NHO DAI HAN ===

    CM->>CM: LanceDB luu embeddings<br/>cho truy van sau nay
    BD->>BD: Dolt luu version history<br/>toan bo du an
    OS->>OS: Specs luu tren disk<br/>lam tai lieu tham khao
```
