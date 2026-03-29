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
- **Normalize idea** thành initial brief (problem, users, criteria, constraints, unknowns)
- Sinh câu hỏi brainstorming (invoke `superpowers:brainstorming`) + **phân loại câu hỏi**
- Present debate report (markdown rendering)
- Dẫn dắt conversation flow qua Gate 1 + Gate 2 (state machine)
- Collect approve/override decisions → translate sang structured JSON
- **Ghi decision log** (traceability cho mọi quyết định)
- Invoke Superpowers skills (TDD, code review, verification, skill rules từ Rule Registry)
- Quản lý session-id

### Engine (Python package) sở hữu:

- Chạy debate crew (CrewAI + agency-agents) — nhận câu hỏi đã phân loại từ Skill
- **Moderator structured output** (resolution status, evidence quality, caveats)
- Format debate report (markdown)
- Nhận approved answers → formalize spec (OpenSpec) theo **output contract cố định**
- Sinh task graph + dependencies + **execution metadata** (agent_type, inputs, outputs, done_definition)
- Match rules từ Rule Registry → inject vào agent backstory
- Execute tasks (CrewAI + agency-agents) với **failure/retry policy**
- Ghi state files vào session directory

### Ranh giới rõ ràng:

- Package **không bao giờ** gọi `input()` hoặc block chờ user
- Skill **không bao giờ** chạy CrewAI trực tiếp
- Mọi giao tiếp qua subprocess + JSON stdio

---

---

## Phase 1a: Idea Normalization + Question Generation

### Bước 1: Normalize idea (Skill)

Trước khi sinh câu hỏi, Skill normalize ý tưởng thô thành **initial brief**:

```json
{
  "problem_statement": "Nhân viên không có chỗ chia sẻ kiến thức nội bộ",
  "target_users": "Developers nội bộ công ty",
  "success_criteria": ["Có thể post/search bài viết", "Tagging theo chủ đề"],
  "constraints": ["Internal only", "Budget thấp"],
  "unknowns": ["Scale bao nhiêu user?", "Cần real-time không?"],
  "domain": "internal-tools",
  "risk_level": "low"
}
```

Nếu ý tưởng quá mơ hồ → Skill hỏi user clarify trước khi tiếp. Initial brief giúp brainstorming sinh câu hỏi sắc hơn.

### Bước 2: Sinh + phân loại câu hỏi (Skill)

Superpowers brainstorming sinh N câu hỏi, **mỗi câu được phân loại**:

| Loại | Ý nghĩa | Hành vi |
|------|---------|---------|
| **REQUIRED** | Bắt buộc trả lời, block flow nếu thiếu | Luôn đưa vào debate |
| **STRATEGIC** | Ảnh hưởng lớn đến design/product direction | Đưa vào debate |
| **OPTIONAL** | Nice-to-have, không chặn flow | Đưa vào debate nếu budget cho phép, hoặc dùng default |

Ví dụ:

| # | Câu hỏi | Loại |
|---|---------|------|
| Q1 | Feature chính MVP? | REQUIRED |
| Q2 | Primary users? | REQUIRED |
| Q3 | Backend tech stack? | STRATEGIC |
| Q6 | Authentication? | STRATEGIC |
| Q8 | Testing strategy? | OPTIONAL |

Skill truyền `questions[]` (kèm loại) vào Engine `run_debate`. Engine có thể skip OPTIONAL nếu quá nhiều câu.

---

## Debate: Moderator Output + Stop Conditions

### Moderator structured output

Sau mỗi câu hỏi, Moderator không chỉ output "CONSENSUS/FORCED" mà cần:

```json
{
  "question_id": "Q6",
  "resolution": "ESCALATE_TO_HUMAN",
  "decision_candidate": "OAuth2 + JWT + short expiry",
  "unresolved_assumptions": ["Session revocation speed requirement unclear"],
  "evidence_quality": "medium",
  "confidence": 0.55,
  "rounds_used": 5,
  "need_human_override": true
}
```

### Resolution status (thay thế binary CONSENSUS/FORCED)

| Status | Nghĩa | Gate 1 behavior |
|--------|--------|-----------------|
| **RESOLVED** | Cả 2 agent đồng ý, evidence rõ | 1-click confirm |
| **RESOLVED_WITH_CAVEAT** | Đồng ý nhưng có assumption chưa verify | Confirm + flag caveat |
| **ESCALATE_TO_HUMAN** | Bất đồng, cần user quyết định | Bắt buộc quyết định |
| **NEED_MORE_EVIDENCE** | Không đủ thông tin để kết luận | User cung cấp thêm context hoặc quyết định |

### Stop conditions

Debate loop dừng khi 1 trong các điều kiện:
1. Cả 2 agent converge → RESOLVED (confidence >= 0.8)
2. Đạt max rounds (5) → Moderator chọn best candidate → ESCALATE_TO_HUMAN
3. Agent lặp lại argument → early stop → Moderator tổng hợp
4. Evidence quality quá thấp → NEED_MORE_EVIDENCE

---

## Decision Log

Sau Gate 1, Skill ghi **decision log** — artifact trung gian quan trọng cho traceability:

```json
{
  "session_id": "2026-03-29-forum-abc123",
  "decisions": [
    {
      "question_id": "Q6",
      "question": "Authentication method?",
      "classification": "STRATEGIC",
      "options_considered": [
        {"agent": "Security Specialist", "position": "OAuth2 + JWT"},
        {"agent": "Product Manager", "position": "Session + Redis"}
      ],
      "moderator_recommendation": "OAuth2 + JWT + short expiry",
      "resolution_status": "ESCALATE_TO_HUMAN",
      "final_answer": "OAuth2 + JWT + short expiry",
      "overridden_by_user": false,
      "user_rationale": null,
      "timestamp": "2026-03-29T14:30:00Z"
    }
  ]
}
```

Dùng để:
- Trace vì sao spec như hiện tại
- Giải thích vì sao task graph sinh ra như vậy
- Audit / giảm tranh cãi về sau

---

## OpenSpec Output Contract

Khi `finalize_spec` chạy, output **phải** theo structure cố định:

```
specs/
    proposal.md           ← bài toán, scope, goals, non-goals
    design.md             ← kiến trúc, tradeoffs, risks
    functional.md         ← functional requirements
    non-functional.md     ← performance, security, scalability
    acceptance-criteria.md ← definition of done cho toàn project
```

Nếu không define rõ, mỗi lần generate sẽ lệch format → task graph generator nhận input không nhất quán.

---

## Task Schema mở rộng (Execution Metadata)

Task graph generator sinh task **với đủ metadata** để CrewAI không phải đoán:

```json
{
  "id": "TASK-1",
  "title": "Thiết kế PostgreSQL schema",
  "objective": "Tạo database schema cho forum MVP",
  "description": "Thiết kế tables: users, posts, tags, post_tags. Dùng PostgreSQL.",
  "type": "design",
  "tags": ["database", "schema", "design"],
  "deps": [],
  "status": "ready",
  "agent_type": "Database Specialist",
  "required_inputs": ["specs/functional.md", "specs/non-functional.md"],
  "expected_outputs": ["schema.sql", "erd.md"],
  "done_definition": "Schema có đủ tables, relationships, indexes cho MVP features",
  "verification_steps": ["SQL syntax valid", "Covers all entities in functional spec"],
  "priority": "high",
  "risk_level": "low"
}
```

---

## Failure / Retry Policy

### Failure paths cho mỗi phase

| Phase | Failure | Hành vi |
|-------|---------|---------|
| Debate | Confidence < 0.3 sau max rounds | NEED_MORE_EVIDENCE → Skill hỏi user cung cấp context |
| Debate | Engine crash / timeout | Resume từ last completed question (state in file) |
| Spec | OpenSpec output thiếu file bắt buộc | Engine retry 1 lần, nếu vẫn thiếu → Skill báo user |
| Spec | Spec mâu thuẫn nội bộ | Engine validate → báo conflict → Skill hỏi user resolve |
| Task graph | Invalid dependencies (circular) | Engine validate → auto-fix hoặc báo Skill |
| Execution | Task fail | Retry theo policy (max 2 retries) |
| Execution | Retry quá ngưỡng | Escalate → Skill báo user, hỏi skip/manual fix/abort |
| Verification | Quality gate fail | Feedback loop → agent sửa → re-verify (max 3 lần) |
| Verification | Re-verify vẫn fail | Escalate → Skill báo user |

### Retry policy config

```toml
[retry]
max_task_retries = 2
max_verification_retries = 3
escalate_after_retries = true
```

---

## Cải tiến tương lai (ghi nhận, chưa implement)

### Gate 2 Impact Preview (#7)

Thêm report thân thiện cho user business: tổng số task, critical path, blocking tasks, parallel opportunities, estimated complexity.

### Phase 3 Role Separation (#8)

Tách CrewAI thành 4 vai rõ: Orchestrator (điều phối), Worker (thực thi), Verifier (kiểm tra), State Manager (memory/audit/status).

### Shared Memory Layering (#9)

Chia memory thành 3 lớp: Project memory (mục tiêu, constraints), Task memory (I/O từng task), Execution memory (logs, retries). Chỉ inject đúng lớp cần thiết.

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
