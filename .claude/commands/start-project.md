---
name: start-project
description: >
  Phase 1a entry point. Thu thập ý tưởng từ user, chạy debate pipeline
  (normalize → question gen → AI debate), và hướng dẫn user sang /review-debate.
---

# Start Project Skill

Invoke this skill with `/start-project` (optionally followed by the idea inline).

## State Machine

```
COLLECT_IDEA → COLLECT_CONSTRAINTS → COLLECT_PROJECT_NAME → RUNNING → DONE / ERROR
```

## Instructions

### COLLECT_IDEA

Check if the user provided an idea after the slash command (e.g., `/start-project "xây forum..."`).

- **Idea provided inline:** skip to COLLECT_CONSTRAINTS.
- **No idea:** Ask: *"Bạn muốn xây dựng gì?"* Wait for response.

### COLLECT_CONSTRAINTS

Always ask this, even if idea was inline:

> *"Có constraint nào cần biết trước không? (vd: tech stack bắt buộc, deadline, budget) — Enter để bỏ qua."*

Accept empty reply, "không", "skip", "bỏ qua" → treat as empty string, continue.

### COLLECT_PROJECT_NAME

Ask:

> *"Tên project? (dùng để nhóm các run liên quan, vd: 'forum-kien-thuc')"*

Wait for name. Do not generate the slug yourself — the CLI script handles it.

### RUNNING

Before running the command, escape all user-supplied values: replace any single-quote `'`
in the value with `'\''` (the standard POSIX shell single-quote escape).

Print:

> *"Đang chạy Phase A (normalize → debate)... Quá trình này mất 2-5 phút."*

Then run the following bash command (blocking — wait for it to finish):

```bash
ai-dev start \
  --project-name '<project_name_escaped>' \
  --idea '<idea_escaped>' \
  --constraints '<constraints_escaped>'
```

> **Fallback:** If `ai-dev` is not in PATH, use `python -m ai_dev_system.cli.start_project` with the same arguments.

Stderr from the script will appear in the terminal in real-time. Do not suppress it.

### DONE (exit code = 0)

Parse the single JSON line from stdout. Display:

```
✅ Phase A hoàn tất.
   Run ID    : <run_id>
   Questions : <questions_count> tổng
               (<escalated_count> ESCALATE_TO_HUMAN/NEED_MORE_EVIDENCE,
                <resolved_count> RESOLVED,
                <optional_count> OPTIONAL tự giải)

→ Chạy /review-debate --run-id <run_id> để bắt đầu Gate 1.
```

Substitute actual values from the JSON. Show the exact `/review-debate --run-id <run_id>` command with the real run_id so the user can copy-paste it.

### ERROR (exit code ≠ 0)

Display:

```
❌ Phase A thất bại: <last line of stderr>

Không có DB record nào được tạo. Bạn có thể chạy lại /start-project với cùng tên project.
```

## Shell Safety

Single-quote escape rule: if value contains `'`, replace each `'` with `'\''` before embedding in the command.

Example — idea containing a single quote `"it's a forum"`:
```bash
--idea 'it'\''s a forum'
```
