# Spec 2 slice 2 — Standalone "spec one task" (webui)

**Date:** 2026-06-24
**Status:** Design (approved in brainstorming)
**Depends on:** `2026-06-24-task-facet-taxonomy-design.md` (slice 1 — reuses `task_graph/facets.py`).

---

## 1. Vấn đề / mục tiêu

Slice 1 đặc tả 8 facet cho mỗi task **trong một dự án** (project→task). Đôi khi người dùng chỉ
muốn **đặc tả nhanh 1 task/feature lẻ** mà không cần cả pipeline (idea→debate→spec→graph). Slice 2
thêm một entry point: dán mô tả 1 task → nhận **TaskSpec 8 facet** ngay trên webui.

**Mục tiêu**
- Một hàm lõi (testable) biến free-text task → minimal coding task → 8 facet (dùng lại slice 1).
- Một form webui "Đặc tả 1 task" hiển thị kết quả + lưu TaskSpec JSON.

**Không-mục-tiêu** (đã chốt brainstorming)
- **Không execute** task (tránh execution layer đang kẹt: required_inputs/promoted_outputs).
- **Không** bước review tương tác riêng (chính phần hiển thị là review; chạy lại để làm lại).
- **Không** vertical-profile flavoring (task lẻ không có dự án → `profile=None`).
- Không đụng task-graph/`validate_graph` (1 task không phải graph 4-phase).

## 2. Kiến trúc & thành phần

### 2.1 Core — `src/ai_dev_system/task_graph/single_task.py` (mới)

```python
def build_single_task(idea: str, *, title: str | None = None) -> dict:
    """Minimal atomic coding task dict from free text (deterministic, no LLM)."""

def spec_single_task(idea: str, llm, *, title: str | None = None) -> dict:
    """-> {"task": <task dict>, "facets": <8-facet dict>}. One LLM call (facets)."""
```

- `build_single_task`: trả task dict tối thiểu — `id` (vd `"TASK-ADHOC"`), `title` (từ `title` hoặc
  rút gọn từ idea), `objective` = idea, `description` = idea, `type="coding"`,
  `execution_type="atomic"`, `required_inputs=[]`, `expected_outputs=[]`. Đủ để
  `is_implementation_task` = True và để facet prompt có ngữ cảnh.
- `spec_single_task`: gọi `generate_task_facets(task, {}, None, llm)` (slice 1) → gắn `task["facets"]`,
  trả `{task, facets}`. **Một LLM call.** `spec_content={}`, `profile=None`. Resilient/stub →
  toàn bộ `needs_human` (do facets.py đã resilient + tránh stub substrings).

### 2.2 Webui — form + route + render ([webui.py](../../../src/ai_dev_system/webui.py))

- **Home**: thêm card thứ 2 **"Đặc tả 1 task"** — textarea `idea` + select `mode` (stub/Max) →
  `POST /spec-task`.
- **`do_POST` nhánh `/spec-task`**: đọc `idea`, `mode`; chọn llm client
  (stub → `StubDebateLLMClient`; max → `make_real_llm_client()`); gọi `spec_single_task`; render
  card facet; lưu TaskSpec JSON xuống `storage/task_specs/<slug-or-ts>.json`.
- **`_render_task_spec(task, facets) -> str`** (pure, testable): card hiển thị 8 facet —
  `filled` hiện content, `na` hiện kèm lý do, `needs_human` gắn cờ "cần làm rõ". Kèm khối text
  copy được.
- Đồng bộ (1 LLM call). `ThreadingHTTPServer` nên không chặn request khác; Max mode ~vài chục giây.

### 2.3 Lưu trữ
TaskSpec JSON ghi xuống `storage/task_specs/` (tên theo slug idea + timestamp). Đủ để soi lại;
không cần DB/artifact registry.

## 3. Luồng dữ liệu
```
idea (free text)
   │
   ▼
build_single_task ──► minimal coding task dict
   │
   ▼
generate_task_facets(task, {}, None, llm)  [slice 1]  ──► 8 facets
   │
   ▼
{task, facets} ──► _render_task_spec (webui card) + storage/task_specs/<...>.json
```

## 4. Tương thích / resilience
- Dùng lại `facets.py` nguyên trạng — không sửa slice 1.
- Stub → facets `needs_human` (vẫn render được, không lỗi).
- `make_real_llm_client()` cần env LLM (LLM_PROVIDER...); nếu thiếu → bắt lỗi, render thông báo
  cấu hình thay vì 500.
- Không đụng execution/gate/task-graph → không rủi ro regression vùng đó.

## 5. Kiểm thử
**Unit**
- `build_single_task`: trả task tối thiểu đúng (type coding/atomic; objective=idea); `is_implementation_task` True.
- `spec_single_task`: fake llm trả JSON → `{task, facets}` đủ 8 facet, `task["facets"]` gắn đúng;
  `StubDebateLLMClient` → tất cả `needs_human`.
- `_render_task_spec`: filled hiện content; na hiện reason; needs_human gắn cờ; HTML escape an toàn.

(Phần HTTP `/spec-task` mỏng — logic nằm ở core + render, test ở 2 chỗ đó.)

## 6. Tệp dự kiến
**Mới**
- `src/ai_dev_system/task_graph/single_task.py`
- `tests/unit/task_graph/test_single_task.py`
- `tests/unit/test_webui_task_spec.py` (cho `_render_task_spec`)

**Sửa**
- `src/ai_dev_system/webui.py` (form home + `/spec-task` route + `_render_task_spec` + `_spec_task` + lưu file)
