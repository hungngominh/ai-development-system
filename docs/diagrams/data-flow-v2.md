# Luồng dữ liệu v2: Human-as-Approver

Biểu đồ này thể hiện luồng dữ liệu thực tế qua các Python module trong `src/ai_dev_system/`.
Người dùng chỉ xuất hiện tại 2 approval gate thay vì can thiệp từng bước.

```mermaid
sequenceDiagram
    participant U as Con người
    participant CLI as ai_dev_system.cli
    participant IN as ai_dev_system.intake
    participant DB_mod as ai_dev_system.debate
    participant G1 as ai_dev_system.gate.gate1_review
    participant SP as ai_dev_system.spec
    participant TG as ai_dev_system.task_graph
    participant RU as ai_dev_system.rules
    participant ENG as ai_dev_system.engine
    participant DB as ai_dev_system.db (SQLite)

    Note over U,DB: === PHASE 1a: INTAKE + NORMALIZE ===
    U->>CLI: ai-dev intake start "Ý tưởng thô"
    CLI->>IN: intake.engine — chạy wizard
    IN->>IN: brief.py — Normalize → initial_brief.json
    IN->>IN: suggest.py — Sinh câu hỏi phân loại
    IN->>DB: Lưu run (SQLite)
    IN-->>CLI: N câu hỏi (REQUIRED/STRATEGIC/OPTIONAL)

    Note over U,DB: === PHASE 1b: AI DEBATE ===
    CLI->>DB_mod: run_debate (câu hỏi REQUIRED + STRATEGIC)
    loop Mỗi câu hỏi
        DB_mod->>DB_mod: agents/ — Ghép cặp agent theo domain
        DB_mod->>DB_mod: Debate (tối đa 5 vòng)
        DB_mod->>DB_mod: Moderator — resolution status + confidence
        Note right of DB_mod: RESOLVED / RESOLVED_WITH_CAVEAT<br/>ESCALATE_TO_HUMAN / NEED_MORE_EVIDENCE
    end
    DB_mod->>DB: Lưu debate results (SQLite)
    DB_mod-->>CLI: debate_report.json

    Note over U,DB: === GATE 1: DUYỆT + DECISION LOG ===
    CLI-->>U: Debate Report<br/>(ESCALATE_TO_HUMAN trước, RESOLVED cuối)
    U->>U: Quyết định ESCALATE items (bắt buộc)<br/>Confirm RESOLVED items (1-click)
    U-->>CLI: Đáp án đã duyệt
    CLI->>G1: gate1_review.core — ghi decision log
    G1->>DB: decision_log.json + approved_answers.json (SQLite)

    Note over U,DB: === PHASE 1d: BUILD SPEC BUNDLE ===
    CLI->>SP: spec.pipeline — build_spec_bundle(approved_answers)
    SP->>SP: planner.py — LLM sinh spec artifacts
    SP->>SP: grounding.py — Grounding check
    SP->>SP: repair.py — Conflict repair
    SP->>SP: tracer.py — Trace map
    alt Spec mâu thuẫn
        SP-->>CLI: Conflict warning
        CLI-->>U: Hỏi resolve
        U-->>CLI: Resolve
        CLI->>SP: build_spec_bundle lại
    end
    SP->>DB: spec bundle (SQLite)

    Note over U,DB: === PHASE 2a: SINH TASK GRAPH ===
    SP->>TG: task_graph.generator — spec bundle làm đầu vào
    TG->>TG: generator.py — Sinh tasks (agent_type, inputs, outputs, done_definition)
    TG->>TG: enricher.py — Enrich metadata (facets, personalization)
    TG->>TG: validator.py — Validate graph
    TG->>DB: task_graph.generated (SQLite)
    TG-->>CLI: task_graph.generated.json

    Note over U,DB: === GATE 2: DUYỆT TASK GRAPH ===
    CLI-->>U: Task Graph Report
    U->>U: Review, approve/edit/reject
    alt Reject
        U->>CLI: Reject
        CLI->>TG: regenerate_task_graph
        TG-->>CLI: task_graph.generated.json mới
        CLI-->>U: Present lại
    end
    U-->>CLI: Approve (với edits nếu có)
    CLI->>DB: task_graph.approved (SQLite)

    Note over U,DB: === PHASE 3: RULE MATCHING + EXECUTION ===
    Note over ENG: ⚠️ Single-task execution hoạt động.<br/>Multi-task graph (required_inputs/promoted_outputs) chưa hoàn chỉnh.
    loop Mỗi task (theo dependency order)
        CLI->>RU: rules — match-rules (task type/tags)
        RU-->>CLI: file_rules + skill_rules
        CLI->>ENG: engine.runner — execute-task
        ENG->>ENG: worker.py — chạy task (max 4 parallel workers)
        alt Task fail
            ENG-->>CLI: Fail → retry (max 2)
            alt Vẫn fail
                CLI-->>U: Escalate: skip/fix/abort?
            end
        end
        ENG->>DB: Cập nhật status (SQLite)
    end

    Note over U,DB: === PHASE 4-5: TỔNG KẾT ===
    ENG-->>U: Kết quả thực thi
    CLI->>DB: Audit trail (SQLite)
    DB-->>U: Báo cáo + thống kê
```

## So sánh Data Flow v1 vs v2

### v1: Con người ở giữa mỗi bước

```
U -> intake -> U -> spec -> U -> task_graph -> engine -> U
     hỏi         trả lời      tạo task
```

### v2: Con người chỉ ở 2 approval gates

```
U -> intake -> debate -> [GATE 1 + decision_log] -> spec -> task_graph -> [GATE 2] -> engine -> U
```

## Điểm khác biệt chính

| Bước | v1 Data | v2 Data |
|---|---|---|
| Input | Ý tưởng thô | Ý tưởng thô → normalized brief (`intake.brief`) |
| Brainstorming | User trả lời từng câu | AI debate (`debate` module) → User approve report |
| Quyết định | User tự suy nghĩ | Resolution status → User approve/override + decision log |
| Spec | Free-form | 5 artifact cố định (`spec.pipeline`) |
| Tạo task | User tự tạo thủ công | `task_graph.generator` sinh JSON → User approve |
| Dependency | User tự thiết lập | `task_graph.generator` phân tích → User approve |
| Execution | Không retry | `engine.failure` — retry policy + escalate |
| Persistence | Không có | SQLite (`ai_dev_system.db`) — toàn bộ state |
