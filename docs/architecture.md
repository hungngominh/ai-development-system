# Kiến trúc hệ thống

## Tổng quan

AI Development System là một **Python monorepo** duy nhất (`src/ai_dev_system/`).
Không có external repo hay external service nào — toàn bộ logic nằm trong các Python module bên dưới.

Xem sơ đồ tổng quan tại [docs/diagrams/system-overview.md](diagrams/system-overview.md).

## Các module chính

### 1. `ai_dev_system.intake` — Intake wizard

Module tiếp nhận ý tưởng thô từ người dùng và chuẩn hóa thành `initial_brief`.
`brief.py` normalize đầu vào thành cấu trúc cố định (problem, target users, success criteria, constraints, unknowns).
`suggest.py` sinh câu hỏi phân loại (REQUIRED / STRATEGIC / OPTIONAL) cho debate.
`engine.py` điều phối toàn bộ wizard flow.

### 2. `ai_dev_system.debate` — Debate engine

Module điều hành tranh luận AI. Mỗi câu hỏi REQUIRED/STRATEGIC được debate bởi 2 agent đối lập (tối đa 5 vòng).
`agents/` quản lý agent pairing theo domain câu hỏi.
`questions/` phân loại câu hỏi theo loại.
Moderator tổng hợp → resolution status: RESOLVED / RESOLVED_WITH_CAVEAT / ESCALATE_TO_HUMAN / NEED_MORE_EVIDENCE.

### 3. `ai_dev_system.gate.gate1_review` — Gate 1

Người dùng duyệt debate report tại Gate 1.
`core.py` điều phối flow: hiển thị ESCALATE_TO_HUMAN trước, RESOLVED sau.
Ghi `decision_log.json` và `approved_answers.json` vào SQLite.

### 4. `ai_dev_system.spec` — Spec bundle

Module sinh spec bundle từ `approved_answers`.
`pipeline.py` điều phối toàn bộ quá trình.
`planner.py` dùng LLM sinh 5 artifact cố định: proposal / design / functional / non-functional / acceptance-criteria.
`grounding.py` kiểm tra grounding với codebase thực tế.
`repair.py` tự động repair conflict.
`tracer.py` tạo trace map liên kết spec → task.

### 5. `ai_dev_system.task_graph` — Task graph

Module sinh task graph từ spec bundle.
`generator.py` sinh tasks với metadata đầy đủ (agent_type, required_inputs, expected_outputs, done_definition, verification_steps).
`enricher.py` enrich metadata (facets, personalization).
`validator.py` validate graph.
`single_task.py` / `single_task_executor.py` hỗ trợ chế độ single-task (đã hoạt động trên Windows).

### 6. `ai_dev_system.engine` — Execution runner (⚠️ Partially implemented)

Module thực thi task graph.
`runner.py` + `worker.py` — tối đa 4 parallel workers.
`failure.py` — retry policy (max 2 lần / task).
`escalation.py` — escalate to human khi retry hết.
**⚠️ Lưu ý:** single-task execution đã hoạt động (TDD-first: test phase → impl phase). Multi-task graph execution với required_inputs/promoted_outputs resolution chưa hoàn chỉnh.

### 7. `ai_dev_system.db` — SQLite persistence

Persistent storage duy nhất của hệ thống.
`connection.py` — stdlib `sqlite3`, zero external dependency.
`migrator.py` — schema migrations.
`repos/` — repository layer cho từng entity.
**Không có** LanceDB, Dolt, hay PostgreSQL.

### 8. `ai_dev_system.eval` — Eval harness

`metrics/` — 18 evaluation metrics.
Golden dataset cho baseline runs.
CLI: `ai-dev eval run / compare / list / show`.

### 9. `ai_dev_system.agents` — LLM providers

`ClaudeMaxAgent` — dùng `claude` CLI (Claude Max subscription, không cần API key).

- **TDD-first single-task split** (`EXEC_TDD_GATE`, default on): the executor emits
  two tasks — `TASK-TEST` (`TestAuthorAgent`) then `TASK-IMPL` (`RepoBranchAgent`),
  routed by `PhaseRoutingAgent`. `TestAuthorAgent` writes FAILING tests from the
  acceptance source (the `test_cases` facet / acceptance criteria) in its own
  context, gated by `TestReviewAgent` (red check + tests↔AC; turn budget:
  `EXEC_TEST_REVIEW_MAX_TURNS`, default 40) before implementation.
  The post-impl `ReviewAgent` additionally flags any test the implementer weakened
  relative to the acceptance source. See
  [specs/2026-06-27-tdd-first-test-split-design.md](superpowers/specs/2026-06-27-tdd-first-test-split-design.md).

### 10. `ai_dev_system.rules` — Rule registry

Match rules theo task type/tags, inject `file_rules` và `skill_rules` vào execution context.

### 11. `ai_dev_system.cli` — CLI

Entry point `ai-dev` cho tất cả commands:
- `ai-dev intake start / resume / abort / show`
- `ai-dev gate review`
- `ai-dev phase-b run / resume / abort`
- `ai-dev eval run / compare / list / show`

## Bảng mapping vấn đề → giải pháp

| Vấn đề | Module | Cách giải quyết |
|---|---|---|
| Không rõ yêu cầu | `intake` | Normalize brief, sinh câu hỏi phân loại |
| Quyết định thiếu căn cứ | `debate` | AI debate đa vòng, moderator resolution |
| Con người can thiệp quá nhiều | `gate.gate1_review` | Chỉ 2 approval gates (Gate 1 + Gate 2) |
| Spec tự mâu thuẫn | `spec` | grounding + repair tự động |
| Task không có metadata | `task_graph` | generator sinh metadata đầy đủ |
| Mất dữ liệu giữa sessions | `db` | SQLite persistent storage |
| Không đo chất lượng | `eval` | Golden dataset + 18 metrics |

## Giới hạn đã biết

1. **Engine chưa hoàn chỉnh**: Multi-task graph execution với required_inputs/promoted_outputs resolution đang trong quá trình phát triển. Single-task execution đã hoạt động.
2. **Intra-session context**: LLM context window là giới hạn vật lý. Conversation dài có thể mất thông tin đầu. Workaround: plan files trên disk.
3. **Memory accuracy**: Không có cơ chế tự động validate memory cũ còn đúng không.
