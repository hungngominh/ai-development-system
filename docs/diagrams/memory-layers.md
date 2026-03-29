# 4 tang tri nho AI

He thong AI Development System giai quyet van de tri nho cua AI qua 4 tang, tu context window ngan han den luu tru dai han. Moi tang duoc ho tro boi mot hoac nhieu repo cu the, dam bao AI khong "quen" thong tin quan trong giua cac phien lam viec.

```mermaid
graph TB
    subgraph L1["TANG 1: Context Window ⚠️ Gioi han vat ly"]
        L1D["Moi LLM call chi co gioi han tokens"]
        L1S1["Beads Compaction → Tom tat context,<br/>giu thong tin quan trong trong gioi han"]
    end

    subgraph L2["TANG 2: Cross-session ✅ Nho giua cac phien"]
        L2D["AI nho lai thong tin tu phien lam viec truoc"]
        L2S1["Beads → Persistent DB (Dolt)<br/>luu trang thai tasks giua cac phien"]
        L2S2["OpenSpec → Specs luu tren disk<br/>doc lai bat ky luc nao"]
        L2S3["CrewAI Memory → LanceDB<br/>luu embeddings cross-session"]
    end

    subgraph L3["TANG 3: Cross-agent ✅ Chia se giua cac agent"]
        L3D["Nhieu agent cung truy cap thong tin chung"]
        L3S1["CrewAI → Scoped Memory<br/>chia se context giua agents trong crew"]
        L3S2["Beads → Dependency Graph<br/>agent nay thay ket qua agent kia"]
    end

    subgraph L4["TANG 4: Dai han ✅ Luu tru vinh vien"]
        L4D["Toan bo lich su du an duoc bao toan"]
        L4S1["Dolt → Version history<br/>toan bo thay doi theo thoi gian"]
        L4S2["LanceDB → Semantic search<br/>tim kiem theo ngu nghia"]
        L4S3["File Specs → Tai lieu ky thuat<br/>luu tru vinh vien tren disk"]
    end

    L1 --> L2
    L2 --> L3
    L3 --> L4

    style L1 fill:#FFCDD2,stroke:#B71C1C,color:#000
    style L2 fill:#C8E6C9,stroke:#1B5E20,color:#000
    style L3 fill:#BBDEFB,stroke:#0D47A1,color:#000
    style L4 fill:#D1C4E9,stroke:#4A148C,color:#000
```

## Repo nao giai quyet tang nao

| Tang | Repo chinh | Co che |
|------|-----------|--------|
| Context Window | **Beads** | Compaction, tom tat tu dong |
| Cross-session | **Beads** + **OpenSpec** + **CrewAI Memory** | Persistent DB, file specs, LanceDB |
| Cross-agent | **CrewAI** + **Beads** | Scoped memory, dependency graph |
| Dai han | **Dolt** + **LanceDB** + **File Specs** | Version history, semantic search, disk storage |
