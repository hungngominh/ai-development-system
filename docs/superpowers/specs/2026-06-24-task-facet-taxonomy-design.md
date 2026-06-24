# Spec 2 (slice 1) — Task facet taxonomy: project→task enrichment

**Date:** 2026-06-24
**Status:** Design (approved in brainstorming, pending written-spec review)
**Depends on:** `2026-06-24-vertical-personalized-questions-design.md` (Spec 1 — reuses the persisted `ProjectProfile`).
**Follow-up slice (out of scope here):** standalone "paste one task" entry point; per-facet debate.

---

## 1. Vấn đề

Khi công việc được bung thành **task graph**, mỗi task hôm nay chỉ mang vài trường tường thuật
(`objective`, `description`, `done_definition`, `verification_steps`, `required_inputs`,
`expected_outputs` — [`task_graph/skeleton.py`](../../../src/ai_dev_system/task_graph/skeleton.py),
enrich bởi [`task_graph/enricher.py`](../../../src/ai_dev_system/task_graph/enricher.py) với 5
trường). Agent code **chỉ nhận đúng các trường này** — không có spec dự án, không có chi tiết kỹ
thuật ([`agents/claude_max_agent.py` `_build_user`](../../../src/ai_dev_system/agents/claude_max_agent.py),
context lấy từ [`engine/materializer.py` `_build_context`](../../../src/ai_dev_system/engine/materializer.py)).
Spec dự án chỉ có 5 mục **cấp dự án** (`proposal/design/functional/non_functional/
acceptance_criteria` — [`finalize_spec.py`](../../../src/ai_dev_system/finalize_spec.py)), không
phân rã theo task.

Hệ quả: agent phải **đoán** Input, Auth & permission, Business rule, Database, Response shape,
Error cases cho từng task. Thiếu hẳn một đặc tả kỹ thuật **theo từng task**.

## 2. Mục tiêu / Không-mục-tiêu

**Mục tiêu**
- Mỗi atomic **implementation task** được đặc tả qua **8 facet**: `input · auth_permission ·
  business_rule · database · response · error_cases · non_functional · test_cases`.
- AI **tự điền** facet (mở rộng khâu enrichment) từ spec dự án + ngữ cảnh task + lăng kính
  `ProjectProfile` (Spec 1); facet không chắc → `needs_human`, không liên quan → `na` + lý do.
- **Gate 2** review/sửa facet (đúng nơi đang duyệt task graph).
- Facet chảy vào execution: agent code nhận một mục **"## Task Specification"** thay vì đoán.

**Không-mục-tiêu (slice/spec sau)**
- Entry point nhập **1 task lẻ** (execution shortcut, độc lập — slice tiếp theo dùng chung taxonomy).
- Debate từng facet (đã loại: thừa khi đã debate ở mức dự án).
- Đặc tả facet cho task phân tích/thiết kế (parse_spec, design_solution) — chỉ implementation task.
- Đổi `generate_task_graph` hiện có (facet là bước hậu kỳ tách biệt → test graph cũ giữ nguyên).

## 3. Quyết định brainstorming (đã chốt)

1. **Cách sinh facet:** AI tự điền + duyệt ở Gate 2 (không debate, không khung rỗng thủ công).
2. **Phạm vi:** project→task enrichment trước; task lẻ là slice sau.
3. (Từ khảo sát code) **Lưu trữ** = task-graph JSON → `context_snapshot` (không migrate DB);
   **review** = Gate 2; facet **bổ sung** chứ không thay 5 mục spec cấp dự án.

## 4. Kiến trúc & thành phần

### 4.1 Mô hình facet (mới)

Mỗi atomic implementation task nhận một dict `facets` (8 khóa cố định). Mỗi facet:

```python
FACET_KEYS = (
    "input", "auth_permission", "business_rule", "database",
    "response", "error_cases", "non_functional", "test_cases",
)

@dataclass
class Facet:
    status: Literal["filled", "needs_human", "na"]
    content: str = ""   # text đặc tả khi "filled"; "" với needs_human/na
    reason: str = ""    # lý do khi status == "na"

# task["facets"]: dict[facet_key -> Facet-as-dict]
```

Serialize sang JSON (dict thuần) để nhét vào task-graph JSON + `context_snapshot`.

### 4.2 Sinh facet — module mới `task_graph/facets.py`

`generate_task_facets(task, spec_sections, profile, llm_client) -> dict[str, dict]`:

- Chạy **per atomic implementation task** (lọc theo `execution_type == "atomic"` và
  `type`/`group` thuộc implementation/coding/testing — task phân tích/thiết kế bỏ qua).
- Input cho LLM: các mục spec dự án (`functional`, `design`, `non_functional`,
  `acceptance_criteria`), trường task (`objective`, `description`, `required_inputs`,
  `expected_outputs`), và **`ProjectProfile`** (lăng kính Spec 1, đọc từ `brief._project_profile`).
- Output: cho mỗi facet → `filled` (kèm content), `na` (kèm reason), hoặc `needs_human` khi AI
  không suy được.
- **Resilient:** mọi lỗi LLM/parse → toàn bộ 8 facet = `needs_human` (content rỗng). Không bao
  giờ làm hỏng pipeline. Dưới stub LLM ⇒ `needs_human` ⇒ execution vẫn chạy (prompt bỏ qua facet rỗng).
- Prompt file `task_graph/prompts/facets.txt` (SYSTEM/USER, str.replace) — tránh các substring
  router của stub nếu cần resilient (giống Spec 1).

Bước cao hơn `generate_task_facets_for_graph(graph, spec_sections, profile, llm_client)` lặp qua
các task hợp lệ và gắn `task["facets"]`.

### 4.3 Wiring vào Phase B

Trong [`debate_pipeline.py`](../../../src/ai_dev_system/debate_pipeline.py) (Phase 2, ngay **sau**
`generate_task_graph` và **trước** khi promote `TASK_GRAPH_GENERATED` + Gate 2): gọi
`generate_task_facets_for_graph(...)` để gắn facet vào graph. `spec_sections` lấy từ spec đã
finalize; `profile` từ `brief._project_profile` (đọc lại từ DebateReport/brief). Facet đi cùng
graph qua `TASK_GRAPH_GENERATED`.

### 4.4 Review tại Gate 2

Mở rộng [`gate/terminal_gate2.py`](../../../src/ai_dev_system/gate/terminal_gate2.py):
- Hiển thị tóm tắt facet mỗi task: `facets: 6 filled / 2 needs-human / 0 N/A`.
- Lệnh mới: `facet show <ID>` (xem 8 facet), `facet set <ID> <key> <text>` (điền/sửa →
  status "filled"), `facet na <ID> <key> <reason>` (đánh N/A).
- Khi `approve`: nếu còn facet `needs_human` → **cảnh báo** liệt kê chúng, nhưng vẫn cho approve
  (human toàn quyền — Gate 2 là interactive, không hard-FAIL).
- Facet đã duyệt lưu vào `TASK_GRAPH_APPROVED` (cùng cơ chế lưu graph hiện tại).

### 4.5 Chảy vào execution (không migrate DB)

- [`engine/materializer.py` `_build_context`](../../../src/ai_dev_system/engine/materializer.py):
  thêm `"facets": task.get("facets", {})` vào context_snapshot (JSON TEXT sẵn có — không cần cột mới).
- [`agents/claude_max_agent.py` `_build_user`](../../../src/ai_dev_system/agents/claude_max_agent.py):
  render mục **"## Task Specification"** liệt kê các facet `filled` (label người-đọc-được), bỏ qua
  `na`, và nếu còn `needs_human` thì ghi rõ "(cần làm rõ)" để agent thận trọng.

### 4.6 Quan hệ với trường/spec hiện có (tránh trùng)

- `test_cases` facet **mở rộng** (không thay) `verification_steps` của enricher: verification_steps =
  *cách verify*, test_cases facet = *kịch bản test cụ thể*. Design ghi rõ mapping.
- `non_functional` facet = cụ-thể-hóa mục NFR cấp dự án xuống mức task.
- `input` facet bổ trợ `required_inputs`; `response` bổ trợ `expected_outputs`.

## 5. Luồng dữ liệu

```
spec sections (functional/design/nfr/acceptance) + ProjectProfile (Spec 1) + task fields
        │  (mỗi atomic implementation task)
        ▼
generate_task_facets ──► task["facets"]  (filled | needs_human | na)
        ▼
TASK_GRAPH_GENERATED → Gate 2 (xem/điền facet, cảnh báo needs_human) → TASK_GRAPH_APPROVED
        ▼
materializer → context_snapshot["facets"] → agent "## Task Specification"
```

## 6. Tương thích ngược & resilience

- Facet **cộng thêm**: `generate_task_graph` không đổi → test graph cũ giữ nguyên. Facet sinh ở
  bước hậu kỳ riêng.
- Resilient: LLM lỗi/stub/parse fail → tất cả facet `needs_human`, content rỗng → agent prompt bỏ
  qua → execution không vỡ.
- Gate 2 mặc định cảnh-báo-không-chặn nên run cũ (graph không có `facets`) vẫn approve được; lệnh
  facet chỉ áp dụng khi task có dict `facets`.
- Cân nhắc env kill-switch `AI_DEV_DISABLE_TASK_FACETS=1` (bỏ qua bước sinh facet) — chốt ở plan;
  KHÔNG thêm vào chuỗi `feature_flags.FLAG_ORDER` tuyến tính (quá xâm lấn — bài học Spec 1).

## 7. Kiểm thử

**Unit**
- `facets.py`: filled/na/needs_human parse đúng; lỗi LLM → toàn bộ needs_human (resilient); lọc
  đúng atomic implementation task (bỏ qua parse/design task).
- `generate_task_facets_for_graph`: chỉ gắn facet cho task hợp lệ; giữ nguyên task khác.
- Gate 2: `facet show/set/na` cập nhật đúng status/content/reason; approve cảnh báo khi còn
  needs_human; facet vào TASK_GRAPH_APPROVED.
- materializer: `context_snapshot` mang `facets`.
- agent prompt: `_build_user` render mục "## Task Specification" (filled hiện, na ẩn, needs_human
  gắn cờ).

**Integration**
- Phase B (stub): graph có `facets` (needs_human dưới stub), execution vẫn chạy tới cùng.

## 8. Rủi ro & giảm thiểu

| Rủi ro | Giảm thiểu |
|---|---|
| LLM điền facet sai/hời hợt | Gate 2 cho human sửa; needs_human buộc chú ý; ProjectProfile + spec sections làm grounding |
| Trùng lặp với verification_steps/NFR | Định nghĩa rõ ranh giới (facet = task-level cụ thể); mapping ghi trong design |
| Lạm phát token (8 facet × nhiều task) | 1 LLM call/task (không debate); chỉ implementation task; resilient fallback |
| Phá test graph/Phase B cũ | Facet là bước hậu kỳ tách biệt + additive; stub → needs_human; Gate 2 cảnh-báo-không-chặn |

## 9. Tệp dự kiến đụng tới

**Mới**
- `src/ai_dev_system/task_graph/facets.py`
- `src/ai_dev_system/task_graph/prompts/facets.txt`
- tests dưới `tests/unit/task_graph/` + `tests/unit/...gate/`

**Sửa**
- `src/ai_dev_system/debate_pipeline.py` (gọi facet step sau generate_task_graph, Phase 2)
- `src/ai_dev_system/gate/terminal_gate2.py` (lệnh facet + cảnh báo approve)
- `src/ai_dev_system/engine/materializer.py` (`_build_context` mang facets)
- `src/ai_dev_system/agents/claude_max_agent.py` (`_build_user` render facets)

## 10. Định nghĩa Facet (tham chiếu cho generator + agent)

| Facet | Hỏi gì cho task này |
|---|---|
| `input` | Dữ liệu/tham số/artifact đầu vào: hình dạng, nguồn, ràng buộc hợp lệ |
| `auth_permission` | Ai được phép; xác thực/uỷ quyền cần gì; ranh giới quyền |
| `business_rule` | Logic nghiệp vụ/ràng buộc miền (nhuốm màu vertical qua ProjectProfile) |
| `database` | Thay đổi schema/bảng/migration; mẫu truy vấn; tính toàn vẹn |
| `response` | Hình dạng đầu ra/trả về: cấu trúc, mã trạng thái, định dạng |
| `error_cases` | Chế độ lỗi đã biết + cách xử lý (nhuốm màu vertical) |
| `non_functional` | Perf/bảo mật/logging/độ tin cậy ở mức task (từ NFR dự án) |
| `test_cases` | Kịch bản test cụ thể (unit/integration) — mở rộng verification_steps |
