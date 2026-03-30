# Design Spec: Start Project Skill (Phase 1a Entry Point)

**Date:** 2026-03-30
**Status:** Draft
**Scope:** Phase 1a entry skill — từ raw idea đến PAUSED_AT_GATE_1

---

## Overview

Implements the Phase 1a entry point theo workflow-v2 và data-flow-v2. User gõ ý tưởng thô, skill thu thập minimal context, sau đó orchestrate toàn bộ Phase A (normalize → question generation → debate) bằng cách gọi `run_debate_pipeline()` qua CLI script. Khi xong, skill hướng dẫn user chạy `/review-debate --run-id <id>` để vào Gate 1.

**Luồng tổng thể (từ data-flow-v2):**

```
U->>SK: Y tuong tho
SK->>SK: Normalize → initial_brief      (bên trong run_debate_pipeline)
SK->>SP: Generate + classify questions  (bên trong run_debate_pipeline — "Superpowers brainstorming")
SP-->>SK: N cau hoi (REQUIRED/STRATEGIC/OPTIONAL)
SK->>DC: run_debate (REQUIRED + STRATEGIC only)
DC-->>SK: Debate results
SK->>SK: Promote INITIAL_BRIEF artifact
         Promote DEBATE_REPORT artifact
         run.status = PAUSED_AT_GATE_1
```

Tất cả các bước trên được `run_debate_pipeline()` xử lý internally. CLI script là thin dispatcher: compute `project_id` từ project name, sau đó gọi `run_debate_pipeline()` một lần duy nhất.

---

## Architecture

**Hai thành phần:**

1. **Skill file** — `skills/start-project.md` — conversation flow, state machine, bash invocation
2. **CLI script** — `src/ai_dev_system/cli/start_project.py` — thin dispatcher gọi `run_debate_pipeline()`

**Approach:** Script riêng với JSON output. Progress lên stderr (real-time), result lên stdout (1 dòng JSON). Skill parse stdout sau khi script kết thúc.

**Phân chia trách nhiệm rõ ràng:**

| Bước | Thực hiện bởi |
|------|---------------|
| Compute `project_id` từ project name | CLI script |
| Normalize idea | `run_debate_pipeline()` — nội bộ |
| Promote `INITIAL_BRIEF` artifact | `run_debate_pipeline()` — nội bộ |
| Generate + classify questions (LLM) | `run_debate_pipeline()` — nội bộ |
| Run debate | `run_debate_pipeline()` — nội bộ |
| Promote `DEBATE_REPORT` artifact | `run_debate_pipeline()` — nội bộ |
| Set status `PAUSED_AT_GATE_1` | `run_debate_pipeline()` — nội bộ |
| In progress lên stderr | CLI script (nhận callback/event từ pipeline) |
| In JSON result lên stdout | CLI script (sau khi pipeline hoàn tất) |

---

## Component 1: Skill File

### Skill File

`skills/start-project.md` — invoked via `/start-project` trong Claude Code.

### State Machine

```
COLLECT_IDEA → COLLECT_CONSTRAINTS → COLLECT_PROJECT_NAME → RUNNING → DONE
                                                                  ↓
                                                               ERROR (exit code ≠ 0)
```

**COLLECT_IDEA:**
- Nếu user gõ `/start-project "xây forum..."` → idea có sẵn trong args, skip state này.
- Nếu chỉ `/start-project` → Skill hỏi: *"Bạn muốn xây dựng gì?"*

**COLLECT_CONSTRAINTS:**
Luôn hỏi sau khi có idea (kể cả khi idea inline):

> *"Có constraint nào cần biết trước không? (vd: tech stack bắt buộc, deadline, budget) — Enter để bỏ qua."*

User có thể để trống hoặc gõ "không" / "skip" → constraints = empty string, tiếp tục.

**COLLECT_PROJECT_NAME:**

> *"Tên project? (dùng để nhóm các run liên quan, vd: 'forum-kien-thuc')"*

`project_id` sẽ được tính bên trong CLI script và trả về trong stdout JSON. Skill **không** tự tính `project_id`.

**RUNNING:**
Skill shell-quote tất cả user-supplied values trước khi build command (xem phần Shell Safety bên dưới). Sau đó print:

> *"Đang chạy Phase A (normalize → debate)... Quá trình này mất 2-5 phút."*

Rồi chạy bash command (blocking). Progress từ stderr hiển thị real-time.

**DONE** (exit code = 0):
Skill parse stdout JSON, hiển thị:

```
✅ Phase A hoàn tất.
   Run ID    : <run_id>
   Questions : <questions_count> tổng
               (<escalated_count> ESCALATE_TO_HUMAN, <resolved_count> RESOLVED, <optional_count> OPTIONAL tự giải)

→ Chạy /review-debate --run-id <run_id> để bắt đầu Gate 1.
```

**ERROR** (exit code ≠ 0):
Skill hiển thị error từ stderr:

```
❌ Phase A thất bại: <error message từ stderr>

Không có DB record nào được tạo. Bạn có thể chạy lại /start-project với cùng tên project.
```

### Shell Safety

Skill phải properly quote tất cả user-supplied values khi build bash command. Dùng single-quote wrapping với escape cho ký tự `'`:

```bash
python -m ai_dev_system.cli.start_project \
  --project-name 'forum-kien-thuc' \
  --idea 'Xây forum chia sẻ kiến thức nội bộ' \
  --constraints 'Python only, no cloud'
```

Với idea hoặc constraints chứa ký tự đặc biệt, skill escape trước khi nhúng vào command.

---

## Component 2: CLI Script

### File

`src/ai_dev_system/cli/start_project.py`

### Interface

```bash
python -m ai_dev_system.cli.start_project \
  --project-name "forum-kien-thuc" \
  --idea "Xây forum chia sẻ kiến thức nội bộ" \
  --constraints "Python only, deploy on-prem"   # optional, default ""
```

### project_id Generation

Script tính `project_id` từ `--project-name`:

```python
import re, uuid

def name_to_slug(name: str) -> str:
    # 1. Lowercase
    s = name.lower()
    # 2. Strip diacritics (dùng unidecode nếu available, fallback ascii-ignore)
    try:
        from unidecode import unidecode
        s = unidecode(s)
    except ImportError:
        s = s.encode("ascii", "ignore").decode()
    # 3. Replace non-alphanumeric bằng "-", strip leading/trailing "-"
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    # 4. Truncate 40 chars
    return s[:40]

slug = name_to_slug(project_name)
project_id = uuid.uuid5(uuid.NAMESPACE_DNS, slug)
```

Cùng project name → cùng slug → cùng `project_id` → các run được nhóm đúng.

### Constraints Injection

Nếu `--constraints` non-empty, append vào raw_idea trước khi gọi pipeline:

```python
full_idea = raw_idea
if constraints.strip():
    full_idea = raw_idea + "\n\nConstraints: " + constraints
```

### Gọi run_debate_pipeline()

```python
result = run_debate_pipeline(
    raw_idea=full_idea,
    config=config,
    conn=conn,
    project_id=str(project_id),
    llm_client=llm_client,
)
```

Script là thin dispatcher — không tự gọi normalize_idea, generate_questions, hay run_debate. Tất cả do `run_debate_pipeline()` xử lý.

### Stderr Progress

Script in progress lên stderr khi nhận event/callback từ pipeline, mirror các bước trong data-flow-v2:

```
[Phase 1a] Normalizing idea...
[Phase 1a] Generating questions (LLM)...
           → 8 questions: 3 REQUIRED, 4 STRATEGIC, 1 OPTIONAL
[Phase 1b] Running debate (REQUIRED + STRATEGIC only)...
           Q1 backend-stack       RESOLVED          (2 rounds)
           Q2 frontend-framework  RESOLVED          (3 rounds)
           Q3 authentication      ESCALATE_TO_HUMAN (5 rounds)
           Q4 database-schema     RESOLVED          (1 round)
           ...
           1 OPTIONAL question(s) auto-resolved (không debate)
[Done]     INITIAL_BRIEF + DEBATE_REPORT promoted. Status: PAUSED_AT_GATE_1
```

### Stdout JSON

Một dòng JSON khi script hoàn tất thành công (exit code 0):

```json
{
  "run_id": "abc-123",
  "project_id": "550e8400-e29b-41d4-a716-446655440000",
  "project_name": "forum-kien-thuc",
  "status": "PAUSED_AT_GATE_1",
  "questions_count": 8,
  "escalated_count": 1,
  "resolved_count": 6,
  "optional_count": 1
}
```

**Định nghĩa các count:**
- `questions_count` = tổng tất cả questions (REQUIRED + STRATEGIC + OPTIONAL)
- `escalated_count` = số câu kết thúc với `ESCALATE_TO_HUMAN` **hoặc** `NEED_MORE_EVIDENCE` ở round cuối (cả hai đều yêu cầu human quyết định tại Gate 1)
- `resolved_count` = số câu kết thúc với `RESOLVED` hoặc `RESOLVED_WITH_CAVEAT` (chỉ các câu đã debate)
- `optional_count` = số OPTIONAL (auto-resolved, không debate)
- **Invariant:** `questions_count = escalated_count + resolved_count + optional_count`

### Error Handling

| Lỗi | Exit code | Stderr message |
|-----|-----------|----------------|
| `--idea` empty | 1 | `Error: --idea must be non-empty` |
| `--project-name` empty | 1 | `Error: --project-name must be non-empty` |
| DB connection fail | 1 | `DB connection failed: {detail}` |
| LLM API error | 1 | `LLM API error: {detail}` |

Khi exit code ≠ 0: stdout **trống**, không có JSON. Skill kiểm tra exit code trước khi parse.

Với LLM API error: nếu lỗi xảy ra trước khi `run_debate_pipeline()` tạo run record → không có DB record, user có thể chạy lại `/start-project` hoàn toàn. Nếu lỗi xảy ra sau khi run record đã tạo (mid-debate) → run record tồn tại với status không hoàn chỉnh; resume là **out of scope** cho v1, user nên tạo run mới.

---

## Data Flow & Integration

```
/start-project skill
       │
       ├─ COLLECT_IDEA          → raw_idea (string)
       ├─ COLLECT_CONSTRAINTS   → constraints (string, có thể rỗng)
       ├─ COLLECT_PROJECT_NAME  → project_name
       │
       └─ Bash (blocking, properly quoted):
            python -m ai_dev_system.cli.start_project \
              --project-name '<name>' --idea '<idea>' --constraints '<constraints>'
                    │
                    ├─ name_to_slug(project_name) → slug
                    ├─ uuid5(NAMESPACE_DNS, slug)  → project_id
                    │
                    └─ run_debate_pipeline(full_idea, config, conn, project_id, llm_client)
                            │
                            ├─ normalize_idea()           → initial_brief  [Phase 1a]
                            ├─ promote INITIAL_BRIEF
                            ├─ generate_questions(brief)  → N questions    [Phase 1a]
                            ├─ run_debate(R+S questions)  → debate_report  [Phase 1b]
                            ├─ promote DEBATE_REPORT
                            └─ update_status → PAUSED_AT_GATE_1

Skill đọc stdout JSON → hiển thị DONE message → hướng dẫn:
  /review-debate --run-id <run_id>
```

### Artifacts Tạo Ra

| Artifact | Phase | Promoted bởi | Schema |
|----------|-------|--------------|--------|
| `INITIAL_BRIEF` | 1a | `run_debate_pipeline()` | ✅ có trong enum |
| `DEBATE_REPORT` | 1b | `run_debate_pipeline()` | ✅ có trong enum |

Không cần DB migration.

### Run Status Flow

```
CREATED → RUNNING_PHASE_1A → RUNNING_PHASE_1B → PAUSED_AT_GATE_1
```

Tất cả đã có trong `run_status` enum. Không cần migration.

### Handoff sang Gate 1

Sau khi skill hiển thị DONE message với `run_id` cụ thể, user chạy:

```
/review-debate --run-id abc-123
```

`review-debate` skill đọc `DEBATE_REPORT` artifact từ DB theo `run_id` và bắt đầu Gate 1 state machine.

---

## Testing Strategy

**Unit tests (no DB, no LLM):**
- `tests/unit/cli/test_start_project.py`
  - Slug generation: spaces, special chars, truncation
  - Slug generation: Vietnamese diacritics (`"Kiến Thức"` → `"kien-thuc"` via unidecode)
  - Slug generation: fallback khi unidecode không có (ascii-ignore)
  - `project_id` determinism: cùng slug → cùng UUID
  - Constraints injection: non-empty appends đúng format, empty = no change
  - Argument validation: empty `--idea` → exit 1, empty `--project-name` → exit 1

**Integration tests (DB, stub LLM):**
- `tests/integration/test_start_project_cli.py`
  - Happy path: idea + constraints → exit 0, stdout JSON valid, `questions_count = escalated + resolved + optional`
  - stdout/stderr separation: stderr có progress lines, stdout chỉ có JSON duy nhất
  - Empty idea → exit 1, stdout trống
  - LLM API error → exit 1, stdout trống, stderr có error message
  - Idempotent project_id: gọi 2 lần cùng `--project-name` → cùng `project_id` trong cả 2 stdout JSON

**Skill logic** được test qua manual walkthrough (skill là markdown, không có unit test framework).

---

## Out of Scope

- Resume từ mid-debate failure (Phase B lo việc resume từ PAUSED_AT_GATE_1)
- List/status các run hiện có
- Cancellation trong khi debate đang chạy
- Streaming progress realtime (stderr buffering đủ cho v1)
- Tạo bảng `projects` riêng trong DB
