# Data Contracts: Artifacts + APIs

> Ngày tạo: 2026-03-29
> Mục đích: Định nghĩa schema cho mọi artifact và API giữa các component
> Components: SK (Skill) | DC (Debate Crew) | OS (OpenSpec) | TG (Task Generator) | RR (Rule Registry) | CR (CrewAI) | BD (Beads)

---

## Artifact Registry

Mọi phase đọc artifact của phase trước. Không phụ thuộc vào chat history hay runtime state.

```
~/.claude/debate-sessions/<session-id>/
    initial_brief.json          Phase 1a → input cho brainstorming
    debate_report.json          Phase 1b → input cho Gate 1
    decision_log.json           Gate 1   → traceability
    approved_answers.json       Gate 1   → input cho build_spec_bundle
    specs/
        proposal.md
        design.md
        functional.md
        non-functional.md
        acceptance-criteria.md
    task_graph.generated.json   Phase 2a → input cho Gate 2
    task_graph.approved.json    Gate 2   → input cho Phase 3
    execution/
        <task-id>.result.json   Phase 3  → per-task result
    verification_report.json    Phase 4  → final
```

---

## Artifact Schemas

### `initial_brief.json`

Sinh bởi: Skill (normalize step)
Tiêu thụ bởi: Superpowers brainstorming

```json
{
  "session_id": "2026-03-29-forum-abc123",
  "raw_idea": "Xây forum chia sẻ kiến thức nội bộ công ty",
  "problem_statement": "Nhân viên không có nơi chia sẻ kiến thức kỹ thuật nội bộ",
  "target_users": "Developers nội bộ (~50 người)",
  "desired_output": "Web app cho phép post/search/tag bài viết",
  "scope_guess": "MVP trong 2 tuần",
  "constraints_known": ["Internal only", "Budget thấp", "Không cần real-time"],
  "unknowns": ["Scale long-term?", "Mobile cần không?", "SSO hay auth riêng?"],
  "domain": "internal-tools",
  "risk_level": "low"
}
```

---

### `debate_report.json`

Sinh bởi: DC (Engine `run_debate`)
Tiêu thụ bởi: SK → present tới user tại Gate 1

```json
{
  "session_id": "...",
  "total_questions": 8,
  "items": [
    {
      "question_id": "Q6",
      "question_text": "Authentication method?",
      "classification": "STRATEGIC",
      "agent_a": {
        "role": "Security Specialist",
        "position": "OAuth2 + JWT (stateless, scale tốt)",
        "key_arguments": ["Stateless token", "Revoke via blacklist"]
      },
      "agent_b": {
        "role": "Product Manager",
        "position": "Session + Redis (đơn giản, revoke ngay)",
        "key_arguments": ["Simpler implementation", "Instant revocation"]
      },
      "rounds_used": 5,
      "moderator": {
        "resolution": "ESCALATE_TO_HUMAN",
        "decision_candidate": "OAuth2 + JWT + short expiry (15 min)",
        "unresolved_assumptions": ["Session revocation speed requirement unclear"],
        "evidence_quality": "medium",
        "confidence": 0.55,
        "risk_if_wrong": "Security gap hoặc operational complexity cao"
      },
      "requires_user_decision": true
    },
    {
      "question_id": "Q3",
      "question_text": "Backend tech stack?",
      "classification": "STRATEGIC",
      "agent_a": { "role": "Backend Architect", "position": "FastAPI + PostgreSQL" },
      "agent_b": { "role": "DevOps Specialist", "position": "FastAPI + PostgreSQL" },
      "rounds_used": 3,
      "moderator": {
        "resolution": "RESOLVED",
        "decision_candidate": "FastAPI + PostgreSQL",
        "unresolved_assumptions": [],
        "evidence_quality": "high",
        "confidence": 0.92,
        "risk_if_wrong": null
      },
      "requires_user_decision": false
    }
  ]
}
```

---

### `decision_log.json`

Sinh bởi: SK sau Gate 1
Tiêu thụ bởi: audit, traceability, giải thích spec

```json
{
  "session_id": "...",
  "gate": "gate_1",
  "timestamp": "2026-03-29T14:30:00Z",
  "decisions": [
    {
      "question_id": "Q6",
      "question_text": "Authentication method?",
      "resolution_status": "ESCALATE_TO_HUMAN",
      "options_considered": [
        { "source": "agent_a", "value": "OAuth2 + JWT" },
        { "source": "agent_b", "value": "Session + Redis" },
        { "source": "moderator", "value": "OAuth2 + JWT + short expiry" }
      ],
      "final_answer": "OAuth2 + JWT + short expiry",
      "decision_type": "APPROVED_MODERATOR",
      "overridden_by_user": false,
      "user_rationale": null
    },
    {
      "question_id": "Q4",
      "question_text": "Frontend framework?",
      "resolution_status": "RESOLVED",
      "options_considered": [
        { "source": "consensus", "value": "React + TypeScript" }
      ],
      "final_answer": "Vue.js + TypeScript",
      "decision_type": "OVERRIDE",
      "overridden_by_user": true,
      "user_rationale": "Team đã quen Vue hơn"
    }
  ]
}
```

---

### `approved_answers.json`

Sinh bởi: SK sau Gate 1 (từ decision_log)
Tiêu thụ bởi: Engine `build_spec_bundle`

```json
{
  "session_id": "...",
  "answers": [
    {
      "question_id": "Q1",
      "question_text": "Feature chính MVP?",
      "answer": "Forum với post, search full-text, tagging theo chủ đề",
      "source": "APPROVED_CONSENSUS"
    },
    {
      "question_id": "Q4",
      "question_text": "Frontend framework?",
      "answer": "Vue.js + TypeScript",
      "source": "OVERRIDE"
    },
    {
      "question_id": "Q6",
      "question_text": "Authentication method?",
      "answer": "OAuth2 + JWT + short expiry (15 min)",
      "source": "APPROVED_MODERATOR"
    }
  ]
}
```

---

### `task_graph.generated.json`

Sinh bởi: Engine `build_spec_bundle` → Task Generator
Tiêu thụ bởi: SK → present tại Gate 2

```json
{
  "session_id": "...",
  "generated_at": "2026-03-29T15:00:00Z",
  "source": "generated",
  "tasks": [
    {
      "id": "TASK-1",
      "title": "Thiết kế PostgreSQL schema",
      "objective": "Tạo database schema cho forum MVP",
      "description": "Thiết kế tables: users, posts, tags, post_tags với proper indexes",
      "type": "design",
      "tags": ["database", "schema", "design"],
      "deps": [],
      "status": "ready",
      "agent_type": "Database Specialist",
      "required_inputs": ["specs/functional.md", "specs/non-functional.md"],
      "expected_outputs": ["schema.sql", "docs/erd.md"],
      "done_definition": "Schema cover đủ entities trong functional spec, migration script chạy được",
      "verification_steps": [
        "SQL syntax valid",
        "Có index cho các query phổ biến",
        "Không thiếu foreign key constraints"
      ],
      "priority": "high",
      "risk_level": "low"
    },
    {
      "id": "TASK-2",
      "title": "Setup OAuth2 + JWT authentication",
      "objective": "Implement auth layer theo quyết định Gate 1",
      "type": "coding",
      "tags": ["auth", "security", "implement"],
      "deps": ["TASK-1"],
      "status": "blocked",
      "agent_type": "Security Specialist",
      "required_inputs": ["schema.sql", "specs/non-functional.md"],
      "expected_outputs": ["auth/", "tests/test_auth.py"],
      "done_definition": "JWT issue/verify/refresh hoạt động, test pass",
      "verification_steps": ["Unit tests pass", "Token expiry đúng 15 min", "Refresh flow hoạt động"],
      "priority": "high",
      "risk_level": "medium"
    }
  ]
}
```

---

### `task_graph.approved.json`

Sinh bởi: SK sau Gate 2 (có thể có edits của user)
Tiêu thụ bởi: Phase 3 execution

```json
{
  "session_id": "...",
  "approved_at": "2026-03-29T15:30:00Z",
  "source": "approved",
  "user_edits": [
    {
      "task_id": "TASK-3",
      "field": "agent_type",
      "before": "Backend Architect",
      "after": "Full Stack Developer",
      "reason": "Cần người biết cả frontend"
    }
  ],
  "tasks": [ /* same schema as generated, với edits đã apply */ ]
}
```

> **Lý do tách generated/approved:** Sau này dễ diff để biết user đã chỉnh gì. Quan trọng cho audit trail.

---

### `execution/<task-id>.result.json`

Sinh bởi: SK sau mỗi task (Phase 3)
Tiêu thụ bởi: verification, audit

```json
{
  "task_id": "TASK-1",
  "session_id": "...",
  "status": "completed",
  "started_at": "2026-03-29T16:00:00Z",
  "completed_at": "2026-03-29T16:20:00Z",
  "agent_used": "Database Specialist",
  "rules_applied": {
    "file_rules": ["rules/design-principles.md"],
    "skill_rules": ["superpowers:brainstorming"]
  },
  "outputs_produced": ["schema.sql", "docs/erd.md"],
  "verification": {
    "status": "passed",
    "checks": [
      { "name": "SQL syntax valid", "result": "pass" },
      { "name": "Index coverage", "result": "pass" }
    ],
    "attempts": 1
  },
  "retry_count": 0,
  "escalated": false
}
```

---

## Engine CLI APIs

### `run_debate`

```
Input (stdin JSON):
{
  "command": "run_debate",
  "session_id": "...",
  "idea": "...",
  "questions": [
    {
      "id": "Q6",
      "text": "Authentication method?",
      "classification": "STRATEGIC"
    }
  ],
  "simulate": false,
  "max_rounds": 5
}

Output (stdout JSONL — streaming):
{"event": "progress", "question_id": "Q3", "total": 8, "status": "RESOLVED", "rounds": 3}
{"event": "progress", "question_id": "Q6", "status": "running", "round": 4}
{"event": "complete", "debate_report": { /* debate_report.json schema */ }}

Errors (stderr):
Verbose CrewAI logs, debug output
```

---

### `build_spec_bundle`

```
Input:
{
  "command": "build_spec_bundle",
  "session_id": "...",
  "approved_answers": [ /* approved_answers.json items */ ]
}

Output:
{"event": "complete", "spec_dir": "~/.claude/debate-sessions/<id>/specs/", "files": ["proposal.md", "design.md", "functional.md", "non-functional.md", "acceptance-criteria.md"], "conflicts": []}

Nếu conflict:
{"event": "conflict", "conflicts": [{"field": "auth_method", "description": "..."}]}
```

---

### `generate_task_graph` / `regenerate_task_graph`

```
Input:
{
  "command": "generate_task_graph",   // hoặc "regenerate_task_graph"
  "session_id": "...",
  "spec_dir": "~/.claude/debate-sessions/<id>/specs/"
}

Output:
{"event": "complete", "task_graph": { /* task_graph.generated.json schema */ }}
```

---

### `match-rules`

```
Input:
{
  "command": "match-rules",
  "session_id": "...",
  "task": { /* task schema */ },
  "registry_path": "./rule-registry.toml"
}

Output:
{
  "event": "complete",
  "file_rules": [
    { "path": "rules/design-principles.md", "content": "..." }
  ],
  "skill_rules": ["superpowers:brainstorming"],
  "match_score": 13
}
```

---

### `execute-task`

```
Input:
{
  "command": "execute-task",
  "session_id": "...",
  "task": { /* task schema */ },
  "file_rules_content": "...",   // đã inject vào backstory
  "retry_count": 0
}

Output (streaming):
{"event": "progress", "step": "agent_thinking"}
{"event": "progress", "step": "producing_output", "output_file": "schema.sql"}
{"event": "complete", "outputs": ["schema.sql", "docs/erd.md"], "summary": "..."}

Nếu fail:
{"event": "error", "code": "execution_failed", "message": "...", "retryable": true}
```

---

## Superpowers Role Separation

Hiện SK invoke SP cho 3 mục đích khác nhau — nên phân biệt rõ trong code:

| Vai trò | Khi nào | Input |
|---------|---------|-------|
| **Ideation** | Phase 1a | `initial_brief.json` |
| **Skill runner** | Trước execute-task | `skill_rules[]` từ match-rules |
| **Verifier** | Sau execute-task | task result, expected outputs |

Trong SK, gọi qua 3 method riêng:
- `sp.brainstorm(brief)` → questions
- `sp.invoke_skill(skill_name, task)` → void (side effect)
- `sp.verify(task, result)` → verification_result

---

## Session State Transitions

```
initial_brief.json
    │ (brainstorm)
    ▼
debate_report.json
    │ (Gate 1)
    ▼
decision_log.json
approved_answers.json
    │ (build_spec_bundle)
    ▼
specs/
    │ (generate_task_graph)
    ▼
task_graph.generated.json
    │ (Gate 2)
    ▼
task_graph.approved.json
    │ (execute-task × N)
    ▼
execution/<task-id>.result.json × N
    │ (verify)
    ▼
verification_report.json
```

Mỗi mũi tên là một phase. Phase sau **không được** đọc artifact của phase trước 2 bước (ví dụ execution không đọc `debate_report.json` trực tiếp, phải qua spec bundle).
