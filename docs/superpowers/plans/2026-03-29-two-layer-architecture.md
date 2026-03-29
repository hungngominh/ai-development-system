# Kiến trúc 2 lớp: Claude Code Skill + Python Package

> **Ngày tạo:** 2026-03-29
> **Spec:** `docs/superpowers/specs/2026-03-29-two-layer-architecture-design.md`
> **Liên quan:** `docs/workflow-v2.md`, `examples/04-debate-pipeline/`
> **Trạng thái:** Approved

## Context

User đề xuất tách hệ thống debate pipeline thành 2 lớp:
- **Lớp Interface**: Claude Code Skill — nơi user gõ ý tưởng, xem báo cáo, approve/override
- **Lớp Engine**: Python package — chạy debate, tạo spec, tạo task graph, execute

Mục tiêu: giữ triết lý Human-as-Approver (chỉ review + approve, AI làm hết việc nặng).

---

## Verdict: Khả thi — với 1 ràng buộc thiết kế quan trọng

Ràng buộc: **approval gate là blocking và interactive**. Mọi lựa chọn thiết kế bên dưới đều xuất phát từ đây.

---

## 1. Phân chia trách nhiệm

| Lớp | Làm gì |
|-----|--------|
| **Skill** | Nhận idea từ user, sinh câu hỏi (Superpowers brainstorming), present báo cáo (markdown), collect approve/override qua hội thoại, invoke skill rules, translate sang JSON cho package |
| **Python package** | Chạy debate crew, format report, nhận approved answers, tạo spec + task graph, match file rules, execute tasks |

**Thay đổi then chốt:** `approval_gate.py` hiện vừa format output vừa gọi `input()`. Trong kiến trúc mới:
- Formatting → giữ trong package
- Input collection → chuyển hoàn toàn sang Skill
- Package **không bao giờ** block trên `input()`

---

## 2. Giao tiếp Skill ↔ Package

**Cơ chế: subprocess + JSON stdio**

Skill gọi package qua subprocess. Package đọc JSON từ stdin, ghi JSON ra stdout. Không cần server, không cần port.

**6 lệnh CLI của Engine** (sinh câu hỏi do Skill/Superpowers brainstorming, không thuộc Engine):

| Command | Input | Output |
|---------|-------|--------|
| `run_debate` | `idea`, `questions[]`, `simulate` | `debate_results[]`, `debate_report_md` |
| `finalize_spec` | `idea`, `approved_answers[]` | `spec_text`, `task_graph[]`, `task_graph_report_md` |
| `regenerate_task_graph` | `spec_text`, `approved_answers[]` | `task_graph[]`, `task_graph_report_md` |
| `match-rules` | `task_json`, `registry_path` | `file_rules[]`, `skill_rules[]` |
| `execute-task` | `task_json`, `rules_content` | `execution_result` |
| `execute` | `approved_tasks[]`, `simulate` | `execution_summary` |

> **Phase 1a:** Skill invoke `superpowers:brainstorming` → sinh câu hỏi → truyền vào `run_debate`.
> **Luồng execution mỗi task:** Skill gọi `match-rules` → invoke `skill_rules` → gọi `execute-task` với `file_rules` đã inject. Xem chi tiết §7.

**Streaming progress cho debate (long-running):**
```
{"event": "progress", "question": 2, "total": 8, "status": "CONSENSUS", "rounds": 3}
{"event": "complete", "debate_results": [...], "debate_report_md": "..."}
```
Skill đọc từng dòng, forward progress cho user, chờ `complete` mới present approval gate.

---

## 3. Quản lý state

State lưu trong **file** (không in-memory), trong session directory:
```
~/.claude/debate-sessions/<session-id>/
    idea.txt
    initial_brief.json         ← sau normalize
    debate_results.json        ← sau run_debate
    decision_log.json          ← Skill ghi sau Gate 1
    approved_answers.json      ← Skill ghi sau Gate 1
    specs/                     ← sau finalize_spec (5 files cố định)
    task_graph.json            ← sau Task Generator
    approved_tasks.json        ← Skill ghi sau Gate 2
    execution_summary.json
```

Skill tạo `session-id` lúc đầu, truyền vào mọi lần gọi subprocess. Nếu bị gián đoạn → resume được vì state đã trong file.

---

## 4. Approval Gates trong Skill

Skill xử lý gate tốt hơn terminal `input()` vì:
- User có thể approve batch: *"approve hết consensus, question 3 chọn option B, question 7 dùng PostgreSQL"*
- User có thể hỏi thêm trước khi quyết định: *"tại sao agent không đồng ý ở câu 3?"*
- Skill confirm lại trước khi proceed: hiển thị summary các quyết định, user confirm xong mới gọi subprocess tiếp

**Gate 1:** Skill collect decisions → ghi `approved_answers.json` → gọi `finalize_spec`
**Gate 2:** Skill collect edits → apply vào task graph → ghi `approved_tasks.json` → gọi `execute`

---

## 5. Cấu trúc Python Package

```
ai-dev-engine/
    pyproject.toml
    src/ai_dev_engine/
        cli.py                  ← entry point, đọc stdin JSON, dispatch commands
        pipeline/
            debate.py           ← từ debate_pipeline.py phase 1a+1b
            spec.py             ← formalize_spec logic
            tasks.py            ← task_graph_generator.py
            execution.py        ← execute_tasks logic
        crew/
            debate_crew.py      ← giữ nguyên
            task_crew.py
            agent_pairing.py    ← giữ nguyên
        formatting/
            report_formatter.py ← giữ nguyên
        session/
            state.py            ← quản lý session directory
```

CLI entry point: `ai-dev-engine` → `ai_dev_engine.cli:main`

---

## 6. Top 3 Rủi ro

| # | Rủi ro | Mitigation |
|---|--------|-----------|
| 1 | **Timeout subprocess** — debate 8 câu x 5 rounds có thể 10-30 phút | Streaming JSON progress + resume từ file nếu bị kill |
| 2 | **Parse Gate 1 từ natural language** — Skill có thể hiểu sai override của user | Skill confirm lại summary decisions trước khi gọi `finalize_spec` |
| 3 | **Context explosion** — Skill accumulate quá nhiều (debate report + task graph + conversation) | Skill không carry raw JSON inline, chỉ dùng markdown report; design resume pattern dùng session-id |

---

## 7. Rule Registry: Tự động inject quy tắc theo task

### Vấn đề

User có sẵn nhiều bộ quy tắc (dạng Superpowers skill + file markdown). Agent cần biết đúng quy tắc **trước khi** làm việc — code task cần coding rules, test task cần testing rules.

### Giải pháp: Rule Registry + Auto-matching

**Config file:** `rule-registry.toml` ở project root

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

Mỗi task có `type`, `tags[]`, `title`, `description`. Matching dùng scoring:

| Signal | Weight | Ví dụ |
|--------|--------|-------|
| Exact type match | +10 | task.type == "coding" |
| Tag overlap | +3/tag | task.tags ∩ config.tags |
| Keyword in title/description | +1/keyword | "implement" found in title |

Score > 0 → matched. Một task có thể match nhiều type ("fix bug in API" → coding + debug).
`defaults` luôn apply.

### Data flow: Skill ↔ Engine phối hợp

```
Skill layer                              Engine layer
─────────                                ────────────

1. Gọi `match-rules --task <json>`  ──→  Load registry, chạy scoring
                                    ←──  Return: { file_rules[], skill_rules[] }

2. Invoke skill_rules[]                  (Skill layer chạy skills trong Claude Code)
   e.g. superpowers:TDD

3. Gọi `execute-task --task <json>
   --rules <file contents>`         ──→  Inject file content vào agent backstory
                                         Tạo CrewAI Agent, execute
                                    ←──  Return: result JSON

4. Post-execution quality gates          (Skill layer chạy verification skill)
```

**Phân chia rõ:**
- **File rules** → Engine đọc + inject vào backstory (vì Engine tạo CrewAI Agent)
- **Skill rules** → Skill layer invoke (vì skills chạy trong Claude Code runtime)

### Injection vào agent backstory

Engine đọc file rules, append vào backstory của agent:

```
[agency-agents base backstory]

---
## Rule: coding-standards
[nội dung coding-standards.md]

---
## Rule: error-handling
[nội dung error-handling.md]
```

Theo đúng pattern `load_agent_prompt` hiện có trong `agent_pairing.py`.

### Task schema mở rộng

Task graph generator cần sinh thêm `type` + `tags`:

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
| Không match rule nào | Chỉ apply defaults, agent chạy bình thường |
| File rule không tồn tại | Skip + log warning stderr, không crash |
| Skill không available | Skill layer báo user, task vẫn chạy |
| File rule quá lớn | Truncate (default max 4000 chars/file) |
| Task không có type/tags | Chỉ keyword matching, fallback defaults |

### File layout

```
project-root/
  rule-registry.toml
  rules/
    general-principles.md
    coding-standards.md
    test-rules.md
    design-principles.md
    ...
```

### Files cần thay đổi

| File | Thay đổi |
|------|----------|
| `rule-registry.toml` | Tạo mới — config registry |
| `rules/*.md` | Tạo mới — nội dung quy tắc |
| `rule_registry.py` | Tạo mới (trong package) — loader, matcher |
| `task_graph_generator.py` | Thêm `type` + `tags` vào task schema |
| `debate_pipeline.py` execute_tasks() | Hook rule matching + injection |
| `agent_pairing.py` | Thêm `build_enriched_backstory()` |
| `docs/workflow-v2.md` | Cập nhật diagram execution phase |

---

## Files quan trọng

- `examples/04-debate-pipeline/approval_gate.py` — nơi refactor chính: bỏ `input()`, chỉ giữ formatting
- `examples/04-debate-pipeline/debate_pipeline.py` — sẽ thành `cli.py`, phase boundaries = 5 CLI commands
- `examples/04-debate-pipeline/report_formatter.py` — migrate nguyên vào package
- `examples/04-debate-pipeline/debate_crew.py` — `crew.kickoff()` blocking, cần thiết kế streaming quanh nó

---

## Kết luận

**Kiến trúc 2 lớp + Rule Registry này được. Nên làm.**

Code trong `examples/04-debate-pipeline/` đã gần sẵn sàng:
1. **Refactor chính:** tách `approval_gate.py` — bỏ `input()`, giữ formatting
2. **Rule Registry:** thêm module mới cho auto-matching + injection, hook vào `execute_tasks()`
3. **Task schema:** mở rộng thêm `type` + `tags` để matching có dữ liệu structured
