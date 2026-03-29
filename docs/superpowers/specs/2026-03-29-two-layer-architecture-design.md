# Kiến trúc 2 lớp — Thiết kế Skill + Engine

> Ngày tạo: 2026-03-29
> Phương án: 2 lớp — Claude Code Skill (Interface) + Python Package (Engine)
> Ngôn ngữ: Tiếng Việt (code và thuật ngữ kỹ thuật giữ tiếng Anh)
> Dựa trên: `docs/workflow-v2.md`, `examples/04-debate-pipeline/`

---

## Mục đích

Tách debate pipeline hiện có (`examples/04-debate-pipeline/`) thành 2 lớp rõ ràng:

- **Người dùng thường** tương tác qua Claude Code Skill — gõ ý tưởng, duyệt kết quả, approve/override
- **Logic nặng** chạy trong Python package ở background — debate, spec, task graph, execution

Giữ triết lý Human-as-Approver: con người chỉ review và approve, AI làm hết việc nặng.

---

## 2 lớp hệ thống

| Lớp | Công cụ | Vai trò | Dùng khi |
|-----|---------|---------|----------|
| Interface | Claude Code Skill | Nhận idea, present report, collect decisions, invoke skills | Tương tác hàng ngày |
| Engine | Python package (`ai-dev-engine`) | Debate, spec, task graph, execution, rule matching | Chạy background bởi Skill |

Mối quan hệ:

```
User ←→ Skill (Claude Code)
            |
            | subprocess + JSON stdio
            |
         Engine (Python CLI)
            |
            ├── CrewAI (debate, execution)
            ├── agency-agents (backstory)
            ├── OpenSpec (spec formalization)
            └── Beads (audit trail)
```

---

## Phân chia trách nhiệm

### Skill (Interface) sở hữu:

- Nhận ý tưởng thô từ user (natural language)
- Sinh câu hỏi brainstorming (invoke `superpowers:brainstorming`)
- Present debate report (markdown rendering)
- Dẫn dắt conversation flow qua Gate 1 + Gate 2 (state machine 4 bước)
- Collect approve/override decisions → translate sang structured JSON
- Invoke Superpowers skills (TDD, code review, verification, skill rules từ Rule Registry)
- Quản lý session-id, ghi `approved_answers.json` và `approved_tasks.json`

### Engine (Python package) sở hữu:

- Chạy debate crew (CrewAI + agency-agents) — nhận câu hỏi từ Skill
- Format debate report (markdown)
- Nhận approved answers → formalize spec (OpenSpec)
- Sinh task graph + dependencies
- Match rules từ Rule Registry → inject vào agent backstory
- Execute tasks (CrewAI + agency-agents)
- Ghi state files vào session directory

### Ranh giới rõ ràng:

- Package **không bao giờ** gọi `input()` hoặc block chờ user
- Skill **không bao giờ** chạy CrewAI trực tiếp
- Mọi giao tiếp qua subprocess + JSON stdio

---

## Giao tiếp: 6 lệnh CLI của Engine

> Sinh câu hỏi do Skill/Superpowers brainstorming, không thuộc Engine.

| Command | Input | Output |
|---------|-------|--------|
| `run_debate` | `idea`, `questions[]`, `simulate` | `debate_results[]`, `debate_report_md` |
| `finalize_spec` | `idea`, `approved_answers[]` | `spec_text`, `task_graph[]`, `task_graph_report_md` |
| `regenerate_task_graph` | `spec_text`, `approved_answers[]` | `task_graph[]`, `task_graph_report_md` |
| `match-rules` | `task_json`, `registry_path` | `file_rules[]`, `skill_rules[]` |
| `execute-task` | `task_json`, `rules_content` | `execution_result` |
| `execute` | `approved_tasks[]`, `simulate` | `execution_summary` |

### Streaming protocol (cho long-running commands):

```jsonl
{"event": "progress", "question": 2, "total": 8, "status": "CONSENSUS", "rounds": 3}
{"event": "progress", "question": 3, "total": 8, "status": "running", "round": 2}
{"event": "complete", "debate_results": [...], "debate_report_md": "..."}
```

- stdout: JSON protocol (progress + result)
- stderr: debug logs, CrewAI verbose output

---

## Quản lý State

State lưu trong file, trong session directory:

```
~/.claude/debate-sessions/<session-id>/
    idea.txt
    debate_results.json
    approved_answers.json       ← Skill ghi
    spec.txt
    task_graph.json
    approved_tasks.json         ← Skill ghi
    execution_summary.json
```

- Skill tạo `session-id` (timestamp + idea hash) lúc đầu
- Truyền session-id vào mọi subprocess call
- Resumable: check file nào đã tồn tại → skip phase đã hoàn thành

---

## Gate 1 Conversation Flow (Skill)

State machine 4 bước:

```
[PRESENT] → [COLLECT_FORCED] → [COLLECT_CONSENSUS] → [CONFIRM] → [DONE]
                                                           ↑           |
                                                           └───────────┘
```

Chi tiết xem `docs/workflow-v2.md` section "Thiết kế Conversation Flow".

Nguyên tắc:
- Không hỏi từng câu một — present toàn bộ picture
- Batch input được phép
- CONFIRM bắt buộc trước `finalize_spec`
- FORCED trước CONSENSUS

---

## Rule Registry

### Vấn đề

User có sẵn bộ quy tắc (Superpowers skills + file markdown). Agent cần biết đúng quy tắc **trước khi** làm việc.

### Giải pháp

**Config:** `rule-registry.toml` ở project root

```toml
[defaults]
files = ["rules/general-principles.md"]
skills = []

[task_types.coding]
files = ["rules/coding-standards.md"]
skills = ["superpowers:test-driven-development"]
tags = ["code", "implement", "fix", "refactor"]
keywords = ["implement", "build", "create", "code", "fix"]

[task_types.testing]
files = ["rules/test-rules.md"]
skills = ["superpowers:verification-before-completion"]
tags = ["test", "qa", "quality"]
keywords = ["test", "coverage", "verify", "validate"]

[task_types.design]
files = ["rules/design-principles.md"]
skills = ["superpowers:brainstorming"]
tags = ["design", "architect", "schema", "api"]
keywords = ["design", "architect", "schema", "plan"]
```

### Auto-matching: Scoring

| Signal | Weight |
|--------|--------|
| Exact type match | +10 |
| Tag overlap | +3/tag |
| Keyword in title/description | +1/keyword |

Score > 0 → matched. Một task match nhiều type được. `defaults` luôn apply.

### Phân chia 2 lớp

- **File rules** → Engine đọc + inject vào agent backstory
- **Skill rules** → Skill layer invoke trước execution

### Task schema mở rộng

```json
{
  "id": "TASK-1",
  "title": "Thiết kế database schema",
  "type": "design",
  "tags": ["database", "schema", "design"],
  "deps": [],
  "status": "ready"
}
```

### Edge cases

| Case | Xử lý |
|------|--------|
| Không match rule nào | Chỉ apply defaults |
| File rule không tồn tại | Skip + warning stderr |
| Skill không available | Báo user, task vẫn chạy |
| File rule quá lớn | Truncate (max 4000 chars/file) |

---

## Cấu trúc Python Package

```
ai-dev-engine/
    pyproject.toml
    src/ai_dev_engine/
        cli.py                  ← entry point
        pipeline/
            debate.py           ← debate logic
            spec.py             ← formalize spec
            tasks.py            ← task graph generator
            execution.py        ← execute tasks
        crew/
            debate_crew.py
            task_crew.py
            agent_pairing.py
        formatting/
            report_formatter.py
        session/
            state.py            ← session directory management
        rules/
            registry.py         ← rule loader, matcher
```

---

## File layout toàn bộ

```
project-root/
    rule-registry.toml
    rules/
        general-principles.md
        coding-standards.md
        test-rules.md
        design-principles.md
        ...
    ai-dev-engine/              ← Python package (Engine)
        pyproject.toml
        src/ai_dev_engine/
            ...
    docs/
        workflow-v2.md          ← cập nhật thêm conversation flow + rule registry
        diagrams/
            data-flow-v2.md     ← cập nhật thêm 2-layer interaction
```

---

## Rủi ro

| # | Rủi ro | Mitigation |
|---|--------|-----------|
| 1 | Timeout subprocess (debate 10-30 phút) | Streaming progress + resume từ file |
| 2 | Parse Gate 1 sai (natural language → JSON) | Confirm summary trước khi proceed |
| 3 | Context explosion trong Skill | Chỉ dùng markdown report, không carry raw JSON |

---

## Ngoài phạm vi

- Nội dung cụ thể của từng rule file (user tự viết)
- UI/UX ngoài Claude Code (web dashboard, v.v.)
- Authentication/authorization cho multi-user
- Deploy/packaging lên PyPI
