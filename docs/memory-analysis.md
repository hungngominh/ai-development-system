# Phân tích bộ nhớ AI: Vấn đề và giải pháp hiện tại

## Vấn đề

AI (LLM) có giới hạn bộ nhớ nghiêm trọng. Mỗi model chỉ có thể "nhớ" trong phạm vi context window, và khi hết phiên làm việc thì mất sạch. Có 4 tầng vấn đề cần giải quyết:

1. **Context window đầy** — Cuộc hội thoại dài, AI quên phần đầu. Khi số lượng token vượt giới hạn, thông tin đầu tiên bị đẩy ra ngoài.
2. **Hết session** — Phiên mới = não trắng. AI không nhớ bất kỳ điều gì từ phiên trước.
3. **Giữa các agent** — Agent A không biết Agent B đã làm gì.
4. **Dài hạn** — AI quên dự án sau vài ngày/tuần.

## Bảng đánh giá (trạng thái hiện tại)

| Tầng | Vấn đề | Giải pháp | Module | Mức độ |
|------|--------|-----------|--------|--------|
| 1. Context window | Hội thoại dài quên đầu | Plan files trên disk, đọc lại khi cần | `cli` / disk | ⚠️ Một phần |
| 2. Cross-session | Phiên mới = trắng | SQLite persistent storage | `ai_dev_system.db` | ✅ Tốt |
| 3. Cross-agent | Agent A ≠ biết Agent B | Task state trong SQLite + structured output | `db` + `engine` | ✅ Tốt |
| 4. Dài hạn | Quên sau tuần/tháng | SQLite + spec files trên disk | `db` + `spec` | ✅ Tốt |

## Chi tiết từng tầng

### Tầng 1: Context Window (⚠️ Một phần)

- **Giới hạn vật lý**: mỗi LLM chỉ xử lý ~128K–1M tokens. Đây là hard limit của kiến trúc transformer.
- **Plan files**: `cli` ghi plan file trên disk, LLM đọc lại khi cần thay vì giữ trong context.
- **Spec files**: `spec.pipeline` ghi 5 artifact ra disk/SQLite — LLM không cần giữ toàn bộ spec trong context, chỉ đọc khi liên quan.
- **Chưa triệt để**: task quá phức tạp trong 1 session vẫn có thể quên. Context window vẫn là hard limit.

### Tầng 2: Cross-session (✅ Tốt)

- **SQLite persistence**: `ai_dev_system.db` (`connection.py` + `migrator.py`) lưu toàn bộ state: intake runs, debate results, decision logs, spec bundles, task graphs, execution status.
- **Không có** LanceDB hay Dolt. Storage duy nhất là SQLite stdlib (`sqlite3`).
- Khi bắt đầu phiên mới: `ai-dev intake resume <run-id>` đọc lại đúng trạng thái từ database.

### Tầng 3: Cross-agent (✅ Tốt)

- **SQLite task state**: `engine.worker` đọc task state từ SQLite trước khi thực thi, ghi kết quả sau khi xong. Agent tiếp theo đọc output từ SQLite thay vì nhận trực tiếp từ agent trước.
- **Structured output**: `task_graph.generator` định nghĩa rõ `required_inputs` và `expected_outputs` cho mỗi task, tránh mất thông tin ngầm.

### Tầng 4: Dài hạn (✅ Tốt)

- **SQLite**: toàn bộ history của dự án (intake, debate, spec, task graph, execution) lưu trong SQLite file. Có thể query lại bất kỳ lúc nào.
- **Spec files**: `spec.pipeline` ghi artifact ra disk, có thể xem lại bằng editor.
- **Không có** Dolt (Git cho database) hay LanceDB (vector store) trong codebase hiện tại.

## 3 lỗ hổng còn lại

### 1. Intra-session

Context window đầy giữa cuộc hội thoại. Đây là vấn đề khó nhất vì không thể thêm memory từ bên ngoài khi đang trong một phiên.

**Workaround hiện tại**: ghi plan file ra disk, LLM đọc lại phần cần thiết.

### 2. Handoff quality

Output giữa agents có thể mất implicit knowledge. Chỉ có explicit output được truyền qua SQLite.

**Workaround hiện tại**: `task_graph.generator` định nghĩa rõ `required_inputs` / `expected_outputs` / `done_definition` để giảm thiểu mất thông tin.

### 3. Memory accuracy

AI không tự validate memory cũ còn đúng không. Spec file có thể outdated khi codebase thay đổi.

**Workaround hiện tại**: `spec.grounding` kiểm tra grounding với codebase thực tế tại thời điểm build spec.

## Đánh giá tổng

**Score: 7/10**

Tốt hơn 95% setup AI hiện tại (hầu hết không có persistent memory nào). Hệ thống giải quyết được 3/4 tầng vấn đề tốt, và tầng còn lại (context window) có workaround chấp nhận được.

**Điểm mạnh:**
- SQLite persistent storage đơn giản, zero external dependency
- Cross-agent communication có cấu trúc qua `task_graph` metadata
- Spec artifact có thể đọc lại bất kỳ lúc nào

**Điểm yếu:**
- Intra-session memory vẫn phụ thuộc vào context window size
- Chưa có semantic search (không có vector store)
- Chưa có tự động validation cho memory cũ
