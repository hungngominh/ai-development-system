# Tong quan he thong

Bieu do nay the hien kien truc tong the cua AI Development System, tu luc nhan yeu cau dau vao cho den khi giao san pham hoan chinh. Moi thanh phan (repo) dam nhan mot vai tro cu the trong quy trinh phat trien phan mem tu dong hoa bang AI.

```mermaid
graph TD
    Input["📥 Yeu cau dau vao"]

    subgraph OpenSpec["OpenSpec: Quy trinh"]
        OS1["Proposal"]
        OS2["Specs"]
        OS3["Design"]
        OS4["Tasks"]
        OS1 --> OS2 --> OS3 --> OS4
    end

    subgraph Beads["Beads: Luu vet"]
        B1["Task Graph"]
        B2["Audit Trail"]
        B1 --- B2
    end

    subgraph CrewAI["CrewAI: Dieu phoi"]
        C1["Sequential Flow"]
        C2["Hierarchical Flow"]
        C1 --- C2
    end

    subgraph Agents["agency-agents: Chuyen mon"]
        A1["Agent Prompts"]
        A2["Role Definitions"]
        A1 --- A2
    end

    subgraph Superpowers["Superpowers: Phuong phap"]
        S1["TDD"]
        S2["Code Review"]
        S3["Verification"]
        S1 --- S2 --- S3
    end

    Output["📦 San pham hoan chinh"]

    Input --> OpenSpec
    OpenSpec --> Beads
    Beads --> CrewAI
    CrewAI <--> Agents
    CrewAI --> Superpowers
    Superpowers --> Output

    Superpowers -.->|"Kiem tra lien tuc"| OpenSpec
    Superpowers -.->|"Kiem tra lien tuc"| Beads
    Superpowers -.->|"Kiem tra lien tuc"| CrewAI
    Beads -.->|"Theo doi toan bo"| CrewAI
    Beads -.->|"Theo doi toan bo"| Superpowers

    style Input fill:#4CAF50,color:#fff
    style Output fill:#2196F3,color:#fff
    style OpenSpec fill:#FFF3E0
    style Beads fill:#E8F5E9
    style CrewAI fill:#E3F2FD
    style Agents fill:#F3E5F5
    style Superpowers fill:#FBE9E7
```
