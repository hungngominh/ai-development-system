# Hướng dẫn sử dụng hệ thống

Tài liệu này mô tả cách chạy AI Development System end-to-end bằng CLI `ai-dev`.
Hệ thống là một Python monorepo (`src/ai_dev_system/`) — không cần cài external repo.

## Prerequisites

- Python >= 3.10
- `claude` CLI (Claude Max subscription) đã cài và đăng nhập
- Cài package:

```bash
pip install -e .
```

Xác nhận CLI hoạt động:

```bash
ai-dev --help
```

## Bước 1: Intake + Normalize (`ai_dev_system.intake`)

**Mục đích:** Nhận ý tưởng thô và chuẩn hóa thành brief + câu hỏi phân loại.

Bắt đầu intake mới:

```bash
ai-dev intake start "Ý tưởng của bạn"
```

Resume một run đang dở:

```bash
ai-dev intake resume <run-id>
```

Xem trạng thái:

```bash
ai-dev intake show <run-id>
```

Hủy run:

```bash
ai-dev intake abort <run-id>
```

**Output:** `initial_brief.json` + câu hỏi phân loại (REQUIRED/STRATEGIC/OPTIONAL) lưu vào SQLite.

## Bước 2: AI Debate + Gate 1 (`ai_dev_system.debate` + `ai_dev_system.gate.gate1_review`)

**Mục đích:** Debate AI cho từng câu hỏi REQUIRED/STRATEGIC → người dùng duyệt tại Gate 1.

Sau khi intake xong, debate chạy tự động. Người dùng được hiển thị debate report và cần quyết định các ESCALATE_TO_HUMAN items trước, rồi confirm RESOLVED items.

Chạy Gate 1 review:

```bash
ai-dev gate review <run-id>
```

**Output:** `decision_log.json` + `approved_answers.json` lưu vào SQLite.

## Bước 3: Build Spec Bundle (`ai_dev_system.spec`)

**Mục đích:** Từ approved answers, sinh 5 spec artifact cố định.

Chạy tự động sau Gate 1 approve, hoặc:

```bash
ai-dev phase-b run <run-id>
```

`spec.pipeline` → `spec.planner` (LLM) → `spec.grounding` → `spec.repair` → `spec.tracer`.

**Output:** 5 artifact trong SQLite:
- proposal
- design
- functional
- non-functional
- acceptance-criteria

## Bước 4: Task Graph Generator (`ai_dev_system.task_graph`)

**Mục đích:** Từ spec bundle, sinh task graph với metadata đầy đủ.

Chạy tự động sau spec bundle, hoặc resume:

```bash
ai-dev phase-b resume <run-id>
```

`task_graph.generator` → `task_graph.enricher` → `task_graph.validator`.

**Output:** `task_graph.generated.json` trong SQLite.
Người dùng review và approve/reject tại Gate 2.

## Bước 5: Execution (`ai_dev_system.engine`)

> ⚠️ **Lưu ý:** Single-task execution đã hoạt động. Multi-task graph với required_inputs/promoted_outputs đang phát triển.

```bash
ai-dev phase-b run <run-id>
```

`engine.runner` khởi động với tối đa 4 parallel workers (`engine.worker`).
Retry tối đa 2 lần / task (`engine.failure`).
Escalate to human khi hết retry (`engine.escalation`).

Audit trail được lưu vào SQLite sau mỗi task.

## Bước 6: Eval Harness (`ai_dev_system.eval`)

**Mục đích:** Đánh giá chất lượng hệ thống so với golden baseline.

Chạy eval:

```bash
ai-dev eval run
```

So sánh với baseline:

```bash
ai-dev eval compare <run-id-1> <run-id-2>
```

Xem danh sách runs:

```bash
ai-dev eval list
```

Xem chi tiết một run:

```bash
ai-dev eval show <run-id>
```

## Tổng kết pipeline

```
ai-dev intake start → debate (auto) → ai-dev gate review
    → spec bundle (auto) → task graph (auto) → Gate 2 (manual approve)
    → ai-dev phase-b run → execution
```

Tất cả state lưu trong SQLite (`ai_dev_system.db`).
Không có external service hay external CLI nào khác ngoài `claude` CLI.

Xem [docs/diagrams/data-flow-v2.md](diagrams/data-flow-v2.md) cho sequence diagram đầy đủ.
Xem [docs/architecture.md](architecture.md) cho mô tả từng module.
