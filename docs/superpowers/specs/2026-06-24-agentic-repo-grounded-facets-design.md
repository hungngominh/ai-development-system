# Spec 2 slice 2b — Agentic repo-grounded facets (Level B)

**Date:** 2026-06-24
**Status:** Design (approved in discussion)
**Depends on:** slice 1 (`task_graph/facets.py` — `FACET_KEYS`, `_coerce_facet` shape) + slice 2 (`task_graph/single_task.py`, webui single-task form).

---

## 1. Vấn đề / mục tiêu

Slice 2 sinh 8 facet **chỉ từ chữ trong ô mô tả task** (`spec_content={}`, `profile=None`) — không bám
code/schema thật. Mức A (đề xuất trước) grounding theo *spec hệ thống tự sinh*; nhưng phần lớn run
chưa đẻ ra code (execution layer còn kẹt), nên *"schema hiện có / code thật"* = **codebase thật của
người dùng**.

**Mục tiêu:** trỏ task vào **một repo thật** → một bước **agentic** (`claude -p` đọc file read-only) tự
lần code liên quan và điền 8 facet **bám bằng chứng thật**, chạy **nền** (không block HTTP).

## 2. Cơ chế lõi — agentic `claude -p` (đã xác minh)

Facet step (khi có repo) KHÔNG dùng `complete(system, user)` 1-shot (không đọc file được). Thay vào đó
shell `claude` ở chế độ read-only, không-tương-tác:

```
claude -p <task-prompt> --append-system-prompt <facet-instructions>
  --output-format json
  --permission-mode bypassPermissions          # không treo chờ xin quyền (no TTY)
  --disallowedTools Edit Write Bash PowerShell WebFetch WebSearch
  --max-turns 15                                # cho đọc nhiều file (KHÔNG =1)
  (subprocess cwd = repo_path, timeout ~300s)
```

- `Read/Grep/Glob` luôn dùng được, không cần quyền → agent tự lần models/schema/module task đụng tới.
- `--disallowedTools Edit/Write/Bash/PowerShell` → **không thể sửa repo / chạy lệnh** (read-only thật).
- **Tái dùng** `ClaudeCodeLLMClient._resolve_claude_cmd()` (đã xử lý tìm `claude.exe` trên Windows) +
  `_strip_outer_code_fence`.
- Auth: dùng phiên Claude Max sẵn có (provider claude_code đã chạy cả pipeline).
- **Lưu ý version:** flag phụ (`--output-format json` wrapper shape, `--append-system-prompt`,
  `--max-budget-usd`) phải verify đúng version `claude` lúc code; parse output **phòng thủ** (thử
  field `result`, rồi `messages[*].content`, rồi raw stdout) → fail thì `needs_human`.

### Prompt (điểm tạo khác biệt)
> "Bạn đặc tả 1 task **trên chính repo này**. Đọc code liên quan (model/schema, module bị đụng) bằng
> Read/Grep/Glob. Điền 8 facet, **bám bằng chứng thật + trích đường dẫn file** trong `content`. Facet
> nào KHÔNG tìm thấy bằng chứng trong code → `status:"needs_human"`, KHÔNG bịa. Bỏ qua `.env`, secrets,
> `node_modules`, build output. Trả về CHỈ JSON `{<facet>: {status, content, reason}}`."

Parse wrapper → text → `json.loads` → `_coerce_facet` mỗi facet (tái dùng resilience slice 1). Bất kỳ
lỗi/timeout/non-JSON/thiếu key → facet đó `needs_human`. **Không bao giờ raise.**

## 3. Thành phần

### 3.1 `task_graph/facets_agentic.py` (mới)
```python
def generate_task_facets_agentic(
    task: dict, repo_path: str, *, model: str | None = None,
    timeout: int = 300, run=subprocess.run,
) -> dict[str, dict]:
    """8 facet bám repo qua claude -p read-only. Never raises → needs_human on any failure."""
```
- Build command (flags trên) + cwd=repo_path; `run` injectable để test (mock subprocess).
- Validate `repo_path` tồn tại & là thư mục; không → toàn bộ `needs_human`.
- Reuse `FACET_KEYS`, `_coerce_facet`, `_all_needs_human` từ `facets.py`.

### 3.2 `single_task.py` (sửa)
`spec_single_task(idea, llm, *, title=None, repo_path=None)`:
- `repo_path` có → `generate_task_facets_agentic(task, repo_path, ...)` (Mức B).
- không → `generate_task_facets(task, {}, None, llm)` (Mức A, như hiện tại).

### 3.3 Webui chạy NỀN (giống pattern `_start`)
Agentic = vài phút → không block HTTP.
- Form "Đặc tả 1 task" thêm ô **"Đường dẫn repo (tuỳ chọn)"**.
- `POST /spec-task`: sinh `spec_id` (uuid) → ghi `storage/task_specs/<spec_id>.json` với
  `{"status":"running", "idea":..., "repo":...}` → **spawn worker tách rời** (detach flags như bản fix
  orphan: `DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP`) → redirect `/task-spec?id=<spec_id>`.
- **Worker** `python -m ai_dev_system.task_graph.single_task_worker --id <id> --idea <..> [--repo <..>] [--stub]`:
  gọi `spec_single_task(..., repo_path=...)`, ghi đè file với `{"status":"done", "task":..., "facets":...}`
  (lỗi → `{"status":"error", "error":...}`).
- `GET /task-spec?id=`: đọc file; `running` → trang "đang chạy, tự refresh 5s"; `done` → render card 8
  facet (`_render_task_spec`); `error` → thông báo.
- Không repo → vẫn nền nhưng nhanh (Mức A 1 LLM call). (Có thể chạy đồng bộ khi không repo — nhưng giữ
  một luồng nền cho đồng nhất.)

## 4. An toàn & độ tin
- **Read-only** ép bằng `--disallowedTools` + `bypassPermissions` (không prompt, không sửa repo).
- Cap `--max-turns` + `timeout` (+ `--max-budget-usd` nếu version hỗ trợ).
- Prompt yêu cầu bỏ qua `.env`/secrets/node_modules; (nếu version hỗ trợ glob-deny: `--disallowedTools
  'Read(**/.env)'`).
- **Trung thực khi mù:** `needs_human` thay vì bịa — khác biệt sống còn.
- Worker tách rời → server tắt không giết job (bài học orphan); file status để soi.

## 5. Phạm vi / không-mục-tiêu
- **Trong:** 1 repo, agentic read-only, chạy nền, ô repo + fallback Mức A.
- **Ngoài:** multi-repo; retrieval/embedding tự xây (agent tự lo); execute task; sửa Mức A ngoài tham
  số `repo_path`; reconcile job nền treo (chỉ hiện status file).

## 6. Kiểm thử
**Unit**
- `generate_task_facets_agentic`: inject `run` trả wrapper JSON hợp lệ → 8 facet filled; command CHỨA
  `--disallowedTools`/`bypassPermissions` + `cwd=repo`; non-zero exit / timeout / non-JSON / thiếu key
  → tất cả `needs_human`; repo_path không tồn tại → `needs_human` (không gọi `run`).
- `spec_single_task`: có `repo_path` → gọi nhánh agentic (inject/patch); không repo → nhánh Mức A.
- Worker: ghi file `status:done` với task+facets (chạy `--stub`/mock); lỗi → `status:error`.
- Webui: `_render_task_spec` trạng thái running/done/error (helper render trạng thái).

(Không gọi `claude` thật trong test — luôn mock `run`/llm.)

## 7. Tệp dự kiến
**Mới**
- `src/ai_dev_system/task_graph/facets_agentic.py`
- `src/ai_dev_system/task_graph/single_task_worker.py`
- tests: `tests/unit/task_graph/test_facets_agentic.py`, `test_single_task_worker.py`, webui test mở rộng.

**Sửa**
- `src/ai_dev_system/task_graph/single_task.py` (tham số `repo_path`)
- `src/ai_dev_system/webui.py` (ô repo, `/spec-task` async spawn, `/task-spec?id=` polling page)
