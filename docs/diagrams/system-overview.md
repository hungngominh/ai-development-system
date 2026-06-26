# Tổng quan hệ thống

Hệ thống AI Development System là một Python monorepo duy nhất (`src/ai_dev_system/`).
Không có external repo nào — toàn bộ logic nằm trong các module Python bên dưới.

```mermaid
graph TD
    Input["📥 Ý tưởng thô từ người dùng"]

    subgraph CLI["ai_dev_system.cli"]
        CMD["CLI: ai-dev\n(intake / gate / phase-b / eval)"]
    end

    subgraph Intake["ai_dev_system.intake"]
        IN1["engine.py — Intake wizard"]
        IN2["brief.py — Normalize → initial_brief"]
        IN3["suggest.py — Sinh câu hỏi phân loại"]
        IN1 --> IN2 --> IN3
    end

    subgraph Debate["ai_dev_system.debate"]
        DB1["agents/ — Agent pairing theo domain"]
        DB2["questions/ — Phân loại REQUIRED/STRATEGIC/OPTIONAL"]
        DB3["Moderator — Resolution status + confidence"]
        DB1 --> DB3
        DB2 --> DB3
    end

    subgraph Gate1["ai_dev_system.gate.gate1_review"]
        G1["core.py — Gate 1: duyệt debate report"]
        G1D["decision_log.json + approved_answers.json"]
        G1 --> G1D
    end

    subgraph Spec["ai_dev_system.spec"]
        SP1["pipeline.py — Build spec bundle"]
        SP2["planner.py — LLM spec generation"]
        SP3["grounding.py — Grounding check"]
        SP4["repair.py — Conflict repair"]
        SP5["tracer.py — Trace map"]
        SP1 --> SP2 --> SP3 --> SP4 --> SP5
    end

    subgraph TaskGraph["ai_dev_system.task_graph"]
        TG1["generator.py — Sinh task graph"]
        TG2["enricher.py — Enrich metadata"]
        TG3["validator.py — Validate graph"]
        TG4["single_task.py — Single-task mode"]
        TG1 --> TG2 --> TG3
    end

    subgraph Engine["ai_dev_system.engine (⚠️ Partially implemented)"]
        ENG1["runner.py — Execution runner"]
        ENG2["worker.py — Worker threads (max 4 parallel)"]
        ENG3["loop.py — Worker loop"]
        ENG4["escalation.py — Escalate to human"]
        ENG5["failure.py — Retry policy"]
        ENG1 --> ENG2 --> ENG3
        ENG3 --> ENG4
        ENG3 --> ENG5
        Note1["⚠️ Planned: required_inputs/promoted_outputs\nresolution across tasks not yet complete"]
    end

    subgraph DB["ai_dev_system.db"]
        DB_C["connection.py — SQLite (stdlib sqlite3)"]
        DB_M["migrator.py — Schema migrations"]
        DB_R["repos/ — Repository layer"]
    end

    subgraph Eval["ai_dev_system.eval"]
        EV1["metrics/ — 18 evaluation metrics"]
        EV2["golden dataset — Baseline runs"]
    end

    subgraph Agents["ai_dev_system.agents"]
        AG1["ClaudeMaxAgent — claude CLI provider"]
    end

    subgraph Rules["ai_dev_system.rules"]
        RU1["Rule Registry — file_rules + skill_rules"]
    end

    Output["📦 Sản phẩm / Task graph đã duyệt"]

    Input --> CLI
    CLI --> Intake
    Intake --> Debate
    Debate --> Gate1
    Gate1 --> Spec
    Spec --> TaskGraph
    TaskGraph -->|"Gate 2: người dùng duyệt"| Engine
    Engine --> Output

    DB -.->|"Persistent storage\n(SQLite)"| Intake
    DB -.->|"Persistent storage"| Debate
    DB -.->|"Persistent storage"| Gate1
    DB -.->|"Persistent storage"| Engine

    Agents -.->|"LLM calls"| Debate
    Agents -.->|"LLM calls"| Spec
    Agents -.->|"LLM calls"| TaskGraph
    Agents -.->|"LLM calls"| Engine

    Rules -.->|"Inject rules"| Engine

    Eval -.->|"ai-dev eval run/compare"| CLI

    style Input fill:#4CAF50,color:#fff
    style Output fill:#2196F3,color:#fff
    style CLI fill:#FFF3E0
    style Intake fill:#E8F5E9
    style Debate fill:#E3F2FD
    style Gate1 fill:#F3E5F5
    style Spec fill:#FBE9E7
    style TaskGraph fill:#E0F2F1
    style Engine fill:#FFF9C4
    style DB fill:#ECEFF1
    style Eval fill:#FCE4EC
    style Agents fill:#E8EAF6
    style Rules fill:#F1F8E9
```

## Các module chính

| Module | Package | Vai trò |
|--------|---------|---------|
| CLI | `ai_dev_system.cli` | Giao diện dòng lệnh `ai-dev` |
| Intake | `ai_dev_system.intake` | Intake wizard: normalize brief → câu hỏi phân loại |
| Debate | `ai_dev_system.debate` | Debate engine: agent tranh luận tối đa 5 vòng |
| Gate 1 | `ai_dev_system.gate.gate1_review` | Người dùng duyệt debate report + ghi decision log |
| Spec | `ai_dev_system.spec` | Build spec bundle (pipeline, grounding, repair) |
| Task Graph | `ai_dev_system.task_graph` | Sinh + validate task graph với metadata đầy đủ |
| Engine | `ai_dev_system.engine` | Execution runner + worker threads (⚠️ partial) |
| DB | `ai_dev_system.db` | SQLite persistence (stdlib `sqlite3`) |
| Eval | `ai_dev_system.eval` | Eval harness: golden dataset + 18 metrics |
| Agents | `ai_dev_system.agents` | LLM provider: ClaudeMaxAgent (`claude` CLI) |
| Rules | `ai_dev_system.rules` | Rule Registry: inject rules theo task type |

## Lưu trữ

Hệ thống dùng **SQLite** (stdlib `sqlite3`) làm persistent storage duy nhất.
Không có LanceDB, Dolt, hay PostgreSQL trong codebase hiện tại.
Schema được quản lý qua `ai_dev_system.db.migrator`.
