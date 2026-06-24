# Spec 2 — Task facet taxonomy (SKETCH)

**Date:** 2026-06-24
**Status:** Sketch only — sẽ khai triển đầy đủ **sau** khi Spec 1 xong.
**Depends on:** `2026-06-24-vertical-personalized-questions-design.md` (tái dùng `ProjectProfile`).

> Đây là bản phác để **không rơi mất ý**. Chưa phải spec hoàn chỉnh; sẽ chạy lại brainstorming
> → writing-plans riêng cho nó sau Spec 1.

---

## 1. Vấn đề

Spec hiện chỉ có 5 mục **cấp dự án** (`proposal, design, functional, non_functional,
acceptance_criteria` — [`finalize_spec.py:13`](../../../src/ai_dev_system/finalize_spec.py),
[`spec/generators/`](../../../src/ai_dev_system/spec/generators/)). Khi đơn vị công việc là **một
task** (một endpoint/feature/việc cụ thể), không có gì đảm bảo task được đặc tả đủ các facet kỹ
thuật mà agent code cần. Thiếu hẳn ở mức task: **Input, Auth & permission, Database, Response
shape, Error cases**; có một phần: NFR, acceptance≈test, business rule≈functional.

## 2. Trục thứ 2 (vuông góc với vertical của Spec 1)

| Trục | Đầu vào | Sinh ra |
|---|---|---|
| Vertical lens (Spec 1) | ý tưởng/dự án | tâm lý, hành vi, retention, cảm xúc… theo ngành |
| **Facet taxonomy (Spec 2)** | **1 task/feature** | Input · Auth & permission · Business rule · Database · Response · Error cases · NFR · Test cases |

Hai trục **compose**: business_rule/error_cases của một task trong app cặp đôi vẫn nhuốm màu
vertical (qua `ProjectProfile` của Spec 1).

## 3. Ý tưởng cốt lõi

Tổng quát hóa khái niệm **"coverage taxonomy"** đã có (hiện ép phủ đủ *domain*) sang một
taxonomy thứ 2 ép phủ đủ **8 facet** khi đơn vị là task.

- **TaskFacet (8):** `input · auth_permission · business_rule · database · response ·
  error_cases · non_functional · test_cases`. Mỗi facet có thể đánh **N/A + lý do** (vd task
  thuần UI không có `database`).
- **Coverage rule** kiểu C1 hiện có: **FAIL** nếu một facet (không N/A) chưa có câu hỏi/spec.
- Output: một **`TaskSpec`** có cấu trúc 8 facet/ task → đầu vào sạch cho agent thực thi.

## 4. Hai điểm cắm dùng chung taxonomy

- **(a) project→task:** sau khi bung task graph
  ([`task_graph/`](../../../src/ai_dev_system/task_graph/)), mỗi task chạy "facet interrogation"
  → sinh câu hỏi/spec đủ 8 facet trước khi giao agent code.
- **(b) task-input mode:** entry point mới (CLI/webui) — dán 1 task lẻ → facet interrogation →
  mini `TaskSpec`, không cần cả dự án.

## 5. Câu hỏi mở (giải quyết khi khai triển)

- Facet interrogation sinh **câu hỏi để người duyệt** (kiểu debate) hay **điền spec trực tiếp**
  rồi chỉ hỏi khi thiếu? (nghiêng: hỏi khi thiếu/không có default an toàn, giống phân loại
  REQUIRED/STRATEGIC/OPTIONAL hiện tại.)
- `TaskSpec` lưu ở đâu: artifact mới hay mở rộng node task graph?
- Persona nào debate facet (Auth→SecuritySpecialist, DB→DatabaseSpecialist, Test→QAEngineer…)?
- Quan hệ với spec v2 5-mục hiện có: thay thế ở mức task hay bổ sung?
- Gate nào duyệt `TaskSpec` (Gate 2 hiện có?).

## 6. Phụ thuộc Spec 1

Prompt facet nhận `ProjectProfile` để `business_rule`/`error_cases` bám vertical → **phải xong
Spec 1 trước**.
