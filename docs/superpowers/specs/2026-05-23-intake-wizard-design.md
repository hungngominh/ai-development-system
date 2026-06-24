# Design Spec: Intake Wizard (Phase 1a-0 — Pre-Debate Brief Collection)

**Date:** 2026-05-23
**Status:** Draft
**Scope:** Replace `normalize_idea()` empty-skeleton with a 3-stage interactive wizard producing a fully populated structured brief before debate.

---

## Motivation

Phase 1 hiện tại nhận ý tưởng thô 1 dòng + constraints string, gọi `normalize_idea()` (vỏ rỗng), rồi đẩy thẳng vào `generate_questions()`. Hệ quả phân tích trong threads trước:

1. **Brief structured nhưng rỗng** — generator phải đoán mò problem/users/scope từ raw text
2. **Câu hỏi hời hợt** — không có context → câu binary, trùng, hoặc lệch idea
3. **Constraint cứng bị concat string** — không structured → có thể bị bỏ qua
4. **Spec hallucinate** — vì input rỗng, mọi LLM call downstream phải đoán

User xác nhận **không lười ở stage intake** — sẵn sàng đầu tư 15-30 phút để có brief chất lượng, đổi lại spec không phải làm lại.

---

## Goals

- Thu thập ≥30 trường brief có cấu trúc trước khi debate
- Cho phép user trả lời "không biết" → AI đề xuất → user confirm (không silent auto-fill)
- Detect gap & inconsistency động sau khi user xong form, follow-up có target
- Resumable: session đứt giữa chừng vẫn tiếp tục được
- Versioned schema: chuẩn bị typed templates (SaaS / internal tool / data pipeline) trong tương lai

## Non-goals

- **Không** build typed templates ngay — chỉ `generic_v1`
- **Không** thay đổi debate engine logic — chỉ đổi input brief
- **Không** UI web — wizard chạy trong terminal qua CLI tương tác, skill `/start-project` chỉ là launcher

---

## Architecture

```
User → Claude Code `/start-project [run_id?]`
       │
       └─→ Skill (launcher only):
             • detect run_id → resume
             • không có → new run
             • exec: ai-dev intake start|resume <project_id|run_id>
       │
       └─→ Python CLI `ai-dev intake`:
             • engine (pure state machine, testable)
             • suggest module (LLM-driven proposals)
             • followup module (4 gap detection logics)
             • DB checkpoint sau mỗi câu
             • output: INTAKE_BRIEF artifact (versioned schema)
       │
       └─→ Phase 1a tiếp tục: generate_questions(brief_v2) → debate → ...
```

**Phân tách trách nhiệm:**

| Module | Trách nhiệm |
|---|---|
| `skills/start-project.md` | Launcher: hỏi tên project (new) hoặc detect run_id (resume), exec CLI, hiển thị output |
| `cli/intake.py` | CLI tương tác: subcommands `start`, `resume`, `show` |
| `intake/engine.py` | Pure state machine: `next_step(state, input) → (new_state, render)` |
| `intake/templates/generic_v1.yaml` | Schema 30+ field, versioned |
| `intake/suggest.py` | LLM call sinh đề xuất khi user gõ "?" |
| `intake/followup.py` | 4 gap detection logic + dynamic question count |
| `intake/repo.py` | Lưu/load state từ DB (`runs.intake_state jsonb`) |

---

## Data Model

### Brief schema v2 (replaces current `normalize.py` skeleton)

```python
{
    "brief_version": 2,
    "template_id": "generic_v1",
    "run_id": str,
    "project_id": str,
    "source_hash": str,             # sha256 of all user-source answers
    "created_at": iso8601,
    "completed_at": iso8601 | null,

    "fields": {
        "problem_statement":   {"value": str|null, "source": "user|ai_suggested_confirmed|skipped", "rationale": str|null},
        "who_feels_pain":      {...},
        "current_workaround":  {...},
        "cost_of_doing_nothing": {...},
        "scope_in":            {"value": list[str]|null, "source": ..., "rationale": ...},
        "scope_out":           {...},
        "success_metric":      {...},
        "done_definition":     {...},
        "deadline":            {...},
        "primary_user":        {...},
        "user_count_now":      {...},
        "user_count_year1":    {...},
        "user_languages":      {...},
        "accessibility":       {...},
        "must_use_stack":      {...},
        "must_not_use":        {...},
        "compliance":          {...},
        "data_residency":      {...},
        "budget_infra":        {...},
        "team_skills":         {...},
        "greenfield_or_brownfield": {...},
        "existing_auth":       {...},
        "data_sources":        {...},
        "must_integrate_with": {...},
        "deployment_target":   {...},
        "nfr_priority":        {"value": list[str]|null, ...},  # ordered list, no hard limit
        "expected_rps":        {...},
        "expected_data_volume": {...},
        "availability_target": {...},
        "latency_target":      {...},
        "known_unknowns":      {...},
        "failed_attempts":     {...},
        "inspiration_refs":    {...},
        "political_constraints": {...}
    },

    "assumptions": [str],           # gaps Stage 2 không kịp hỏi
    "audit": [                       # mọi sự kiện wizard
        {"ts": iso8601, "event": "answered|suggested|confirmed|edited|skipped", "field": str, "value_preview": str}
    ]
}
```

### DB

`runs` bảng thêm columns:

```sql
ALTER TABLE runs ADD COLUMN intake_state jsonb;       -- live wizard state, cleared after promote
ALTER TABLE runs ADD COLUMN intake_brief_id uuid;     -- pointer to promoted INTAKE_BRIEF artifact
```

`artifacts` thêm artifact_type: `INTAKE_BRIEF` (versioned via `brief_version` field).

### Template file format (`generic_v1.yaml`)

```yaml
version: 1
id: generic_v1
sections:
  - id: 1_context
    name: "Vấn đề & bối cảnh"
    fields:
      - id: problem_statement
        prompt: "Vấn đề cụ thể bạn đang giải quyết là gì? (pain point, không phải feature)"
        type: text_long
        critical: true
        ai_can_suggest: false
        examples_hint: "vd: 'Nhân viên không tìm được tài liệu cũ, mỗi lần hỏi lại Slack'"
      - id: who_feels_pain
        prompt: "Ai đang chịu pain này? Role gì, bao nhiêu người?"
        type: text_short
        critical: false
        ai_can_suggest: false
      ...
  - id: 2_scope
    ...
```

8 critical fields (theo confirm của user):
`problem_statement`, `scope_in`, `scope_out`, `success_metric`, `primary_user`, `deployment_target`, `compliance`, `current_workaround`.

---

## State Machine

```
START
  ├─ new run → create run record, status=COLLECTING_INTAKE
  └─ resume → load intake_state, fast-forward to last position

ASKING(field_idx)
  ├─ input = answer → save(source=user), idx++ → ASKING(idx+1)
  ├─ input = "?" or "không biết"
       └─ if field.ai_can_suggest=false → reprompt với hint, không suggest
       └─ if ai_can_suggest=true → SUGGESTING(field_idx)
  ├─ input = "skip" → save(value=null, source=skipped), idx++
  ├─ input = "back" → idx--, allow re-edit
  ├─ input = "save" → checkpoint, print "resume với: ai-dev intake resume <run_id>", exit 0
  ├─ input = "show" → render current state, stay at idx
  └─ idx >= last → FOLLOWUP

SUGGESTING(field_idx)
  ├─ call suggest.generate(field, brief_so_far)
  ├─ render: "Tôi đề xuất X vì Y. (a) ok / (b) sửa thành: ... / (c) thật sự để trống"
  ├─ "a" → save(value=suggestion, source=ai_suggested_confirmed, rationale=Y), idx++
  ├─ "b ..." → save(value=user_text, source=user), idx++
  └─ "c" → save(value=null, source=skipped), idx++

FOLLOWUP
  ├─ scan brief, generate gap list (dynamic count):
       • Critical blank: every critical field with value=null
       • Inconsistency: cross-field rule violations (latency+budget, scope+deadline, ...)
       • Ambiguity: LLM rates each text_long field, score < threshold
       • Scope mismatch: scope_in count vs deadline vs team_skills
  ├─ for each gap: ASKING-style sub-prompt with context why asking
  ├─ user can "?" trong followup → reuse SUGGESTING for that field
  ├─ after all gaps handled OR user gõ "đủ rồi" → record remaining as assumptions
  └─ → CONFIRM

CONFIRM
  ├─ render full brief với markup nguồn (👤 user, 🤖 ai_confirmed, ⚠️ assumption)
  ├─ "confirm" → promote INTAKE_BRIEF artifact, clear intake_state, status=READY_FOR_DEBATE
  └─ "sửa <field>" → jump to ASKING for that field, then re-CONFIRM
```

**5 lệnh đặc biệt mọi state nhận được:** `back`, `save`, `show`, `?`, `skip`.

---

## Suggest Module

### Trigger
User gõ `?` hoặc `không biết` ở field có `ai_can_suggest: true`.

### Prompt template

```
Bạn đang giúp user xác định "{field_id}" cho brief dự án.

Context user đã cung cấp:
{render top-5 fields most relevant to this field, picked via static dependency map}

Field hiện tại:
- Mô tả: {field.prompt}
- Type: {field.type}
- Options (nếu enum): {field.options}

Hãy đề xuất 1 giá trị duy nhất hợp lý nhất + lý do (≤2 câu).
Trả về JSON: {"suggestion": "...", "rationale": "..."}
```

### Dependency map (relevant fields per suggest target)

Hardcoded để tránh nhồi cả brief vào mọi suggest call:

| Target field | Relevant fields |
|---|---|
| `deployment_target` | `data_residency`, `existing_auth`, `budget_infra`, `compliance` |
| `nfr_priority` | `deadline`, `expected_rps`, `availability_target`, `team_skills` |
| `expected_rps` | `user_count_now`, `user_count_year1`, `primary_user` |
| `availability_target` | `cost_of_doing_nothing`, `primary_user`, `budget_infra` |
| ... | (full map trong `intake/suggest_deps.py`) |

### Refuse to suggest
Fields có `ai_can_suggest: false`:
`problem_statement`, `current_workaround`, `cost_of_doing_nothing`, `must_use_stack`, `must_not_use`, `budget_infra`, `team_skills`, `political_constraints`, `failed_attempts`, `inspiration_refs`.

Khi user gõ "?" ở những field này, reply: *"Field này tôi không thể đoán hộ — chỉ bạn biết. Bạn có thể skip (gõ `skip`) nếu thật sự chưa rõ."*

---

## Followup Module (Stage 2 — dynamic count)

### 4 logic detection

**1. Critical blank (rule-based)**
```python
gaps += [Gap("critical_blank", f) for f in CRITICAL_FIELDS if brief.fields[f].value is None]
```
Mỗi gap → 1 follow-up câu, force SUGGESTING nếu user lại gõ "?".

**2. Inconsistency (rule-based + 1 LLM call)**
Rule list trong `intake/consistency_rules.py`:
```python
RULES = [
    Rule("avail_vs_budget",
         when=lambda b: b.get("availability_target") in {"99.99%", "99.999%"}
                        and parse_budget(b.get("budget_infra")) < 200,
         message="Availability {availability_target} cần infra HA, nhưng budget {budget_infra}/mo khó đủ"),
    Rule("scope_vs_deadline",
         when=lambda b: len(b.get("scope_in", [])) > 8 and parse_deadline_weeks(b.get("deadline")) < 6,
         message="Scope {N} items vs deadline {weeks} tuần — cần cắt scope hay giãn deadline?"),
    Rule("residency_vs_deploy",
         when=lambda b: b.get("data_residency") == "VN"
                        and b.get("deployment_target") in {"AWS", "GCP"}
                        and "VN region" not in (b.get("deployment_target_note") or ""),
         message="Data phải ở VN nhưng deploy {deployment_target} — confirm có region VN?"),
    # ~10-15 rules total
]
```
1 LLM call cuối để bắt inconsistency rule-based bỏ sót (low confidence).

**3. Ambiguity (LLM-rated)**
```python
for f in TEXT_LONG_FIELDS:
    if brief.fields[f].source == "user" and brief.fields[f].value:
        score = llm_rate_specificity(brief.fields[f].value)  # 0-1
        if score < 0.5:
            gaps.append(Gap("ambiguity", f, hint=llm_ambiguity_hint(f)))
```
Stub-mode: tất cả ambiguity = 0.7, skip ambiguity stage.

**4. Scope mismatch (rule)**
```python
scope_count = len(brief.fields["scope_in"].value or [])
weeks = parse_deadline_weeks(brief.fields["deadline"].value)
team_skill_count = len(brief.fields["team_skills"].value or [])
if scope_count / max(weeks, 1) > 1.5:  # >1.5 items/week
    gaps.append(Gap("scope_mismatch", ...))
```

### Dynamic count
Không hard-limit. Nhưng có 2 escape hatch:
- User gõ `"đủ rồi"` bất cứ lúc nào → còn lại ghi vào `assumptions`
- Nếu followup phát hiện >15 gap → cảnh báo user: *"Brief có 17 gap. Bạn muốn (a) đi qua hết để chính xác, (b) chỉ giải quyết critical (8 gap), (c) skip hết, ghi assumption?"*

---

## CLI Surface

```
ai-dev intake start --project-id <id>
    → create run, status=COLLECTING_INTAKE, launch wizard
    → exit 0 with JSON: {run_id, status: "intake_complete", brief_id}
    → exit 0 with JSON: {run_id, status: "intake_paused"} nếu user "save"

ai-dev intake resume --run-id <id>
    → load intake_state, jump to last position, continue wizard

ai-dev intake show --run-id <id>
    → print brief (markup nguồn), exit 0

ai-dev intake abort --run-id <id>
    → clear intake_state, set run status=ABORTED
```

---

## Skill Changes

`skills/start-project.md` giảm trách nhiệm còn:

```
INVOKED:
  ├─ args contains run_id → check DB:
       • run exists & status=COLLECTING_INTAKE → exec `ai-dev intake resume`
       • run exists & status=READY_FOR_DEBATE → say "intake xong rồi, chạy /review-debate"
       • run không tồn tại → báo lỗi
  └─ no args → COLLECT_PROJECT_NAME

COLLECT_PROJECT_NAME:
  → ask "Tên project?"
  → exec `ai-dev intake start --project-id <id>`

(while CLI running, stream stderr; on exit parse stdout JSON)

DONE if intake_complete:
  → display: "✅ Intake xong. Chạy /debate --run-id <run_id> để bắt đầu Phase 1b"

DONE if intake_paused:
  → display: "💾 Đã lưu. Resume: /start-project <run_id>"
```

**Bỏ:** `COLLECT_IDEA`, `COLLECT_CONSTRAINTS` (giờ là field trong wizard).

**Note:** Phase 1b (`generate_questions` + debate) tách thành skill mới `/debate` thay vì chạy auto sau intake. Lý do: intake xong là 1 milestone, user nên có cơ hội xem brief trước khi đốt token chạy debate.

---

## Phase B Integration (breaking change)

### `finalize_spec.py` update

```python
def finalize_spec(brief_v2: dict, approved_answers: dict, run_id: str, llm_client, output_dir):
    """
    Generate 5-file spec from BOTH structured brief AND debate decisions.
    Brief = ground truth context. Approved_answers = decisions chốt từ debate.
    """
    system_prompt = """
    You are a technical writer. You will receive:
    1. brief_v2: structured project context (problem, users, scope, constraints, NFR priority)
    2. approved_answers: decisions made through AI debate + human approval

    Write a 5-section spec where:
    - 'proposal' references brief's problem_statement and success_metric verbatim
    - 'functional' covers everything in scope_in, explicitly excludes scope_out
    - 'non_functional' is anchored on brief's nfr_priority order
    - 'design' uses approved_answers + brief constraints as hard requirements
    - 'acceptance_criteria' must be measurable against brief.success_metric

    Treat brief fields with source=ai_suggested_confirmed same as source=user.
    Skipped fields with assumptions in brief.assumptions: surface them in spec under
    "Open Questions" section per file.
    """
    user_payload = json.dumps({"brief": brief_v2, "approved_answers": approved_answers})
    ...
```

### `generate_questions` update (debate input)

```python
def generate_questions(brief_v2: dict, llm_client) -> list[Question]:
    """
    Questions now grounded on full brief instead of empty skeleton.
    System prompt updated to: 'Look at scope_in/scope_out/constraints/known_unknowns
    to identify decisions that AI cannot make alone. Do not ask things already
    decided in brief.'
    """
```

→ Câu hỏi sẽ sâu hơn vì có context. Nhưng vẫn cần Decision Inventory + Critic loop (phân tích trước) để chống F1/F4.

### Migration cho run cũ
Brief v1 (skeleton rỗng) không tương thích. Run cũ ở status `PAUSED_AT_GATE_1` không migrate được — đánh dấu `legacy=true`, để chạy đường cũ tới hết, không inject brief v2.

---

## Resumability Details

### Checkpoint chiến lược
Save `intake_state` vào DB **sau mỗi**: answer, suggest confirm, skip, gap resolution. Không sau "show" hoặc "back" navigation.

### Resume detection trong skill
```
Skill nhận `/start-project <token>`:
  • token là UUID → lookup runs.run_id
  • token là project name → lookup runs by project_id, status=COLLECTING_INTAKE, latest
  • không tìm thấy → assume new project
```

### State fingerprint
Mỗi checkpoint có `schema_hash` của template version. Resume nếu hash khớp; nếu template upgrade, prompt user: *"Schema đã đổi từ v1 → v2. Migrate (giữ trả lời tương thích, hỏi field mới) hay start over?"*

---

## Testing Strategy

### Unit
- `engine.next_step()`: feed scripted input sequences, assert state transitions
- `suggest.generate()`: stub LLM, assert prompt structure + JSON parsing
- `followup.detect_gaps()`: golden brief inputs, assert gap list match
- `consistency_rules`: each rule has positive + negative case

### Integration
- Full wizard run end-to-end với stub LLM
- Resume after kill at every state
- Schema migration v1→v2 cho run cũ

### Golden set (eval harness — tách sang spec riêng)
5 idea mẫu (forum, SaaS, ETL, ML service, CLI tool) × full intake → so brief v2 với expected brief. Đo: critical fill rate, ai_suggested confirm rate, assumptions count.

---

## Build Order (vertical slices)

| Slice | Đầu ra closable | Tests |
|---|---|---|
| **S1** | `generic_v1.yaml` + `engine.next_step()` pure function | unit test toàn state machine với stub input |
| **S2** | `ai-dev intake start` chạy được headless (stdin/stdout) | integration test e2e với stub LLM |
| **S3** | Suggest module + dependency map | unit + integration với "?" inputs |
| **S4** | Followup module (4 logic) | rule unit tests + integration test có gap |
| **S5** | DB checkpoint + `intake resume` | kill-and-resume integration test |
| **S6** | Skill `/start-project` đổi sang launcher mode | manual test trong Claude Code |
| **S7** | `finalize_spec` đọc brief v2 + `generate_questions` đọc brief v2 | regression test Phase B end-to-end |
| **S8** | Migration script + legacy flag | test với run cũ thực |

Mỗi slice là 1 PR, mergeable independently. **Đừng build S7 trước S1.**

---

## Open Questions

1. **Field count drift:** 30+ field có thể quá nhiều cho ý tưởng siêu nhỏ ("fix login bug"). Cần `template_id: micro_v1` (5-7 field) hay user tự skip aggressive đủ rồi? **Defer** đến sau khi `generic_v1` chạy thật.

2. **Suggest cost:** mỗi `?` = 1 LLM call. Worst case 30 field × suggest = 30 calls trước khi vào debate. Có nên cache suggestions trong session để user back-and-forth không tốn lại?

3. **Multi-language:** prompts hiện viết tiếng Việt hardcode. Khi support typed templates, template có nên kèm `lang: vi|en` không?

4. **Brief v2 size:** ~5-10KB JSON. Truyền vào mọi LLM call downstream = ăn token. Cần "brief digest" (~500 token) cho các call không cần full context?

---

## Out of Scope (deferred)

- Typed templates (SaaS / internal / data pipeline / ML)
- Web UI cho intake (chỉ terminal)
- Multi-user collaborative intake (1 user / run)
- Voice/audio intake
- Auto-extract brief từ existing doc (PRD, Jira ticket) — đề xuất build sau khi `generic_v1` chứng minh giá trị
