# Design Spec: Gate 1 Skill Redesign (`/review-debate`)

**Date:** 2026-05-23
**Status:** Draft
**Scope:** Redesign `/review-debate` skill to handle brief v2, new resolution statuses (`MODERATOR_PARSE_FAILED`), OPTIONAL visibility, brief-aware decision context, and brief edit at gate.

---

## Motivation

Gate 1 hiện tại ([review-debate.md](skills/review-debate.md)):

- Show debate report standalone — không thấy brief
- Không phân biệt `MODERATOR_PARSE_FAILED` vs `ESCALATE_TO_HUMAN`
- OPTIONAL questions show under generic "AI resolved" list, không expand được
- Không cho phép edit brief field tại gate (nếu user thấy brief sai phải abort, làm lại)
- Decision context (vì sao câu này cần debate) ẩn — user phải đọc full debate transcript để hiểu
- Skill state machine khá dài (~150 dòng markdown) — khó maintain

Sau Spec A/B/C, structure data đổi:
- Brief v2 đầy đủ
- Questions có `source_decision_id`
- RoundResult có `MODERATOR_PARSE_FAILED` + `auto_resolution_reason`
- Decisions có context `description` + `reason_required` + `blocks_what`

Skill phải tận dụng để Gate 1 review **nhanh và đúng**.

---

## Goals

- Hiển thị brief v2 + debate side-by-side, user thấy context khi quyết
- 4 section render (vs 2 hiện tại): Forced / Parse-failed / Consensus / Auto-resolved
- Brief edit tại gate (limited fields, with re-trigger logic)
- Decision context inline với mỗi câu hỏi
- State machine logic chuyển sang Python (skill chỉ render + parse user input)
- Backward compat với run cũ (brief v1)

## Non-goals

- **Không** thay đổi `finalize_gate1()` core logic (vẫn produce APPROVED_ANSWERS + DECISION_LOG)
- **Không** UI graphical (vẫn chat-based trong Claude Code)
- **Không** multi-user review (single approver)
- **Không** integrate Linear/Jira (defer)

---

## Architecture

```
User → /review-debate <run_id>
       │
       ▼
   Skill (markdown):
       • dispatch to Python tool calls
       • render output as chat messages
       • parse user input → action
       │
       ▼
   Python: gate1_review module
       • load brief v2 + debate + decisions + questions
       • build review sections
       • parse user input (NLU helpers)
       • call finalize_gate1() at end
       │
       ▼
   APPROVED_ANSWERS + DECISION_LOG artifacts
       → run.status = RUNNING_PHASE_1D
```

```
src/ai_dev_system/gate/gate1_review/
├── __init__.py
├── loader.py          # load all artifacts for run
├── sections.py        # build 4 sections from debate + brief
├── parser.py          # parse user input → ReviewAction
├── editor.py          # brief field edit logic
├── renderer.py        # produce markdown blocks for skill
└── finalize.py        # call existing finalize_gate1()
```

Old `skills/review-debate.md` (~150 lines markdown) → replaced with ~50-line launcher calling Python.

---

## Skill File Structure

`skills/review-debate.md` reduced to launcher:

```markdown
---
name: review-debate
description: Gate 1 review — duyệt debate report, brief, ghi decision log
---

# Gate 1 Review

Invoked với `/review-debate <run_id>`.

## Flow

1. Load run via `python -m ai_dev_system.gate.gate1_review load <run_id>`.
   Output JSON: { sections: {...}, brief: {...}, pending_count: int }

2. Render sections theo template dưới đây.

3. Wait for user input. Pass to:
   `python -m ai_dev_system.gate.gate1_review parse --run-id <id> --input "<text>"`

   Output JSON: { action, recorded, remaining, message }

4. Loop until `pending_count == 0` and `action == confirm`.

5. Call `python -m ai_dev_system.gate.gate1_review finalize --run-id <id>`.
   Print result.

## Render templates

### Section: Brief Summary (collapsed by default)
```
## Brief — {project_name}
Type `show brief` để xem chi tiết, `edit <field>` để sửa.

Problem: {brief.problem_statement | truncate 100}
Scope IN: {brief.scope_in | join ", "}
NFR: {brief.nfr_priority[0:3] | join " > "}
```

### Section: Cần quyết định (FORCED + PARSE_FAILED)
... (renderer module outputs full markdown block)

### Section: Đã resolve qua debate
...

### Section: Auto-resolved (OPTIONAL) — collapsed
Type `expand optional` to view all.

## Input parsing
Send user message to parser. Parser returns structured action with:
- action_type: answer | edit_brief | expand | show | confirm | abort
- target: question_id | field_id | section_id
- payload: text

Render parser.message back to user.
```

---

## Loader Module

### Input
- `run_id`

### Output
```python
@dataclass
class GateReviewContext:
    run_id: str
    project_name: str
    brief: dict                       # brief v2 or stub for legacy
    is_legacy_brief: bool
    debate_report: dict
    decisions: list[Decision] | None  # None if legacy
    questions: list[Question]         # final question list
    coverage_report: dict | None
```

### Logic
1. Load `run` from DB → `current_artifacts`
2. Load each referenced artifact:
   - `DEBATE_REPORT` (required)
   - `INTAKE_BRIEF` (new, optional for legacy)
   - `DECISION_INVENTORY` (new, optional)
   - `QUESTION_COVERAGE_REPORT` (new, optional)
3. Cross-link: each question → decision (via `source_decision_id`)
4. Cache in `runs.gate1_session_state` jsonb for incremental resumption

---

## Sections Builder

### 4 sections produced

```python
@dataclass
class ReviewSection:
    name: str                    # "forced" | "parse_failed" | "consensus" | "auto_resolved"
    items: list[ReviewItem]
    collapsed_by_default: bool

@dataclass
class ReviewItem:
    question_id: str
    question_text: str
    classification: str
    domain: str
    decision_context: str        # decision.description + reason_required
    blocks_what: list[str]       # from decision
    agent_a: str
    agent_b: str
    agent_a_position: str
    agent_b_position: str
    moderator_summary: str
    confidence: float
    resolution_status: str       # incl. MODERATOR_PARSE_FAILED
    caveat: str | None
    auto_resolution_reason: str | None
    raw_moderator_output: str | None   # only for PARSE_FAILED
```

### Section assignment

| Resolution Status | Section |
|---|---|
| `ESCALATE_TO_HUMAN` | forced |
| `MODERATOR_PARSE_FAILED` | parse_failed (different UI from forced) |
| `NEED_MORE_EVIDENCE` after max rounds | forced (treated same as ESCALATE) |
| `RESOLVED` / `RESOLVED_WITH_CAVEAT` | consensus |
| `OPTIONAL` (auto-resolved) | auto_resolved (collapsed) |

### Rendering rules

**Forced item:**
```
**Q3_search_engine_choice** [REQUIRED · backend]
  Context: {decision.description} — Sẽ block: {blocks_what}

  {agent_a.name}: {position_a}
  {agent_b.name}: {position_b}
  Moderator: {moderator_summary}
  (Confidence: 0.45 — agents fundamentally disagree)

  → Bạn quyết: chọn A / chọn B / approve moderator / override với text riêng
```

**Parse-failed item:**
```
⚠️ **Q5_moderation_policy** [STRATEGIC · product] — Moderator response không parse được

  Context: {decision.description}

  {agent_a.name}: {position_a}
  {agent_b.name}: {position_b}

  Raw moderator output (debug):
  ```
  {raw_moderator_output | truncate 300}
  ```

  → Bạn đọc 2 quan điểm trên và quyết: chọn A / chọn B / override với text riêng
```

**Consensus item (collapsed):**
```
✅ Q2_db_choice [REQUIRED · database] → PostgreSQL with read replica (confidence 0.92)
   Type `show Q2` to expand full debate.
```

**Auto-resolved item (collapsed group):**
```
🤖 4 câu OPTIONAL đã auto-resolve. Type `expand optional` to review.
```

---

## Input Parser

### Recognized actions

| User input pattern | Action |
|---|---|
| `Q3 chọn A` / `Q3 option A` / `Q3 agent A` | `answer(q_id=Q3, choice=agent_a)` |
| `Q3 chọn B` | `answer(q_id=Q3, choice=agent_b)` |
| `Q3 approve moderator` / `Q3 đồng ý moderator` | `answer(q_id=Q3, choice=moderator)` |
| `Q3: dùng X` / `Q3 → dùng X` / `Q3 override X` | `answer(q_id=Q3, choice=override, text=X)` |
| `show Q3` | `expand(item=Q3)` |
| `show brief` | `expand(section=brief)` |
| `expand optional` | `expand(section=auto_resolved)` |
| `edit problem_statement: new value` | `edit_brief(field=problem_statement, value=...)` |
| `approve all` | `bulk_approve_consensus()` (only if forced+parse_failed empty) |
| `approve all, Q4 dùng Vue` | `bulk_approve_with_override(overrides={Q4: "dùng Vue"})` |
| `confirm` | `confirm()` |
| `abort` | `abort()` |

### Parser implementation
- Regex-first for structured patterns
- Fallback to LLM-based NLU for ambiguous phrasing (1 small call)
- Always confirm parse back to user: *"Hiểu là: Q3 = chọn quan điểm BackendArchitect. Đúng không?"*
- If ambiguous, ask clarifying — don't record.

### Guard: blocking actions

```python
if action == "approve_all" and pending_forced > 0:
    return ParserResult(
        accepted=False,
        message=f"Không thể approve all khi còn {pending_forced} câu cần quyết: {forced_ids}"
    )
```

---

## Brief Edit at Gate

### Why allow
User có thể nhận ra ở Gate 1 rằng brief sai (vd typo problem_statement, scope_in thiếu 1 item). Hiện tại phải abort run, làm lại intake. Wasteful.

### Editable fields (whitelist)
- `problem_statement`, `who_feels_pain`, `current_workaround`, `cost_of_doing_nothing`
- `scope_in`, `scope_out` (with re-trigger)
- `success_metric`, `done_definition`, `deadline`
- `nfr_priority`
- `known_unknowns`

### Non-editable at gate
- `compliance`, `data_residency`, `deployment_target` (changing these invalidates whole debate)
- `must_use_stack`, `must_not_use` (same)
- Any field with `source: ai_suggested_confirmed` (must re-confirm via wizard)

### Edit flow

```
User: edit scope_in: + "moderation"

Parser → editor.apply_edit(field="scope_in", op="append", value="moderation")
       → editor.check_impact():
           - scope_in changed → may invalidate questions about scope
           - flag affected questions: Q3_search, Q7_voting (none about moderation now)
       → editor.confirm_with_user():
           "Bạn thêm 'moderation' vào scope_in. Điều này có thể cần thêm câu hỏi
            mới về moderation policy. Options:
            (a) Chỉ update brief, không tạo câu hỏi mới (assumption: dùng default)
            (b) Re-trigger question gen cho domain product/qa (mất ~1 phút)
            (c) Hủy edit"

User: a
       → editor.commit(field=scope_in, value=[...new list...])
       → log to AUDIT
       → re-render brief section
```

### Re-trigger logic
If user chooses (b):
- Call `inventory_runner.generate_for_diff(brief_old, brief_new)` — only inventory new decisions caused by diff
- Materialize + critic + run debate for those new questions
- Append to existing debate_report
- Resume Gate 1 review with new pending items

This is a **mini Phase 1b** scoped to the diff. Implementation in Spec A's `pipeline.run_for_diff()` (add to open questions there).

---

## Brief Edit at Gate — Cost / Safety

- Edit is logged to `BRIEF_EDIT_LOG` artifact with old + new value + timestamp
- Re-trigger has hard cap: max 5 re-trigger per Gate 1 session (else abort, user redo intake)
- If user edits non-editable field → reject with explanation
- Re-trigger uses same model as original to avoid drift

---

## Confirm & Finalize

### Confirm screen

```
## Tóm tắt quyết định — 11 câu

Forced (3):
  👤 Q3 [REQUIRED] Search engine? → "PostgreSQL FTS for MVP, migrate to Meilisearch if NPS < 7"
  👤 Q5 [STRATEGIC] Moderation? → "Manual + report button, no auto-mod"
  👤 Q9 [REQUIRED] Voting anti-abuse? → "Rate limit 10/hour/user, 1 vote per post"

Parse-failed (1):
  👤 Q7 [STRATEGIC] Notification? → "Email only, no push"

Consensus (5):
  ✅ Q1, Q2, Q4, Q6, Q8 (auto-approved)

Overrides (2):
  ✏️ Q4 Frontend → "React + Tailwind" (user override)
  ✏️ Q6 Deploy → "Azure region SEA" (user override consensus)

Auto-resolved OPTIONAL (4):
  🤖 Q10, Q11, Q12, Q13 (all default-accepted)

Brief edits (1):
  ✏️ scope_in: + "moderation"

→ Xác nhận và ghi APPROVED_ANSWERS + DECISION_LOG + BRIEF_FINAL?
```

### Finalize calls

```python
finalize_gate1(
    run_id=run_id,
    decisions=[...],
    brief_edits=[...],          # NEW: include in DECISION_LOG
    config=Config.from_env(),
    conn=conn,
)
```

Existing `finalize_gate1()` extended to accept `brief_edits` parameter. New artifact `BRIEF_FINAL` (post-edit brief) created if edits exist.

---

## Backward Compatibility

### Legacy run (no brief v2)
- `loader.is_legacy_brief = True`
- Brief section shows: *"This run started before brief v2 (intake wizard). Edit not available."*
- 4 sections still rendered but without decision context (since no decisions artifact)
- `parse_failed` section empty (legacy never produces this status)

### State machine state in DB

`runs.gate1_session_state jsonb` stores:
- `pending_forced: list[q_id]`
- `pending_parse_failed: list[q_id]`
- `decisions_recorded: dict[q_id, Decision]`
- `brief_edits: list[BriefEdit]`
- `last_action_at: timestamp`

Allows resume of Gate 1 if user disconnects mid-review (e.g. quits Claude Code).

---

## Testing Strategy

### Unit
- `loader.py`: artifact load with all-present and partial cases (legacy)
- `sections.py`: 4-way classification of items, all 5 resolution statuses
- `parser.py`: 30+ input patterns, ambiguity fallback to LLM
- `editor.py`: editable field whitelist, impact detection
- `renderer.py`: markdown output snapshot tests

### Integration
- Full Gate 1 review with stub LLM, all 4 sections produced
- Brief edit flow: edit scope_in + re-trigger questions
- Parse-failed handling: present + collect + finalize
- Bulk approve_all with overrides

### Skill manual test
- Real Claude Code session, `/review-debate` on golden run
- Edit brief, observe re-trigger
- Confirm, verify artifacts created

---

## Build Order

| Slice | Đầu ra | Test |
|---|---|---|
| **G1** | `loader.py` + `GateReviewContext` | unit with fixtures |
| **G2** | `sections.py` builds 4 sections | unit per status |
| **G3** | `renderer.py` produces markdown blocks | snapshot tests |
| **G4** | `parser.py` regex patterns (no NLU fallback) | unit 20+ patterns |
| **G5** | CLI: `python -m gate1_review load|parse|finalize` | integration |
| **G6** | New `review-debate.md` skill calling Python | manual Claude Code |
| **G7** | `editor.py` brief edit (no re-trigger) | unit + integration |
| **G8** | Re-trigger logic (call back into Spec A pipeline) | integration |
| **G9** | LLM-based NLU fallback for ambiguous input | unit + integration |
| **G10** | Session state in DB + resume | integration kill-and-resume |

G1-G6 = MVP. G7-G10 = quality + advanced features.

---

## Open Questions

1. **Skill markdown size:** giảm xuống 50 lines bằng cách push logic vào Python tốt, nhưng skill file mất "self-contained". User đọc skill không hiểu hết flow. Mitigation: add link to design spec trong skill description.

2. **NLU fallback cost:** mỗi câu ambiguous = 1 small LLM call (~$0.001). Acceptable. Cache (input, parsed) per session.

3. **Brief edit re-trigger:** UX complex. Có nên defer re-trigger tới sau và chỉ allow edit thuần (assumption-only)?
Recommend: ship G7 (edit-only) first, G8 (re-trigger) later.

4. **Session state TTL:** gate1_session_state có nên expire không? Đề xuất: clear khi run.status thay đổi khỏi `PAUSED_AT_GATE_1`.

5. **Multi-language UI:** rendering currently mixed Vi/En. Standardize on Vi for user-facing, En for field IDs / artifact names?

---

## Out of Scope (deferred)

- Diff view giữa debate output và user override (visualize divergence)
- Suggestion: AI propose "có thể bạn muốn override Q3 vì scope_in có X" — pre-emptive nudges
- Multi-approver workflow (current = single user)
- Approval delegation
- Mobile-friendly rendering (chat is fine)
- Voice review (audio summary read-back)
