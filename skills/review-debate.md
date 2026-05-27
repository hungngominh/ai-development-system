---
name: review-debate
description: Gate 1 review — duyệt debate report, brief, ghi decision log. Invoked with `/review-debate <run_id>`.
---

# Gate 1 Review

**Invoked via:** `/review-debate <run_id>`

Gate 1 is the human approval checkpoint after debate finishes. Your job is to:
1. Load and display the debate report (4 sections)
2. Collect human decisions on "forced" and "parse-failed" questions
3. Let user approve or override consensus items
4. Finalize → write APPROVED_ANSWERS + DECISION_LOG artifacts

---

## Flow

### Step 1 — Load

```bash
python -m ai_dev_system.gate.gate1_review load <run_id>
```

Output JSON: `{ run_id, project_name, is_legacy, pending_count, sections[], brief_header }`

### Step 2 — Render

```bash
python -m ai_dev_system.gate.gate1_review render --run-id <run_id>
```

Print the full rendered markdown to the user. This shows:
- **Brief summary** (collapsed — `show brief` to expand)
- **🔴 Cần quyết định** (ESCALATE_TO_HUMAN + NEED_MORE_EVIDENCE + MODERATOR_PARSE_FAILED)
- **✅ Đã resolve** (RESOLVED / RESOLVED_WITH_CAVEAT — collapsed)
- **🤖 Auto-resolved OPTIONAL** (collapsed — `expand optional` to view)

### Step 3 — Collect decisions (loop)

For each user message, call:

```bash
python -m ai_dev_system.gate.gate1_review parse \
  --run-id <run_id> \
  --input "<user message>" \
  --pending-forced <N> \
  --pending-pf <M>
```

Output JSON: `{ action, target, choice, payload, message, accepted }`

**Actions:**
- `answer` → record decision for `target` question with `choice` (agent_a/agent_b/moderator/override)
- `expand` → show detail for `target` (QID or "brief" or "auto_resolved")
- `approve_all` → bulk-approve all consensus items (only if pending_forced==0 && pending_pf==0)
- `confirm` → proceed to finalize
- `abort` → stop without writing artifacts

**After each answer:**
- Echo back `parser.message` to the user as confirmation
- Track which questions have been answered in your session state
- Show remaining pending count: "Còn <N> câu cần quyết định"

Loop until `pending_forced == 0 && pending_pf == 0` AND user types `confirm`.

### Step 4 — Confirm screen

Before finalizing, show a summary:

```
## Tóm tắt quyết định — <N> câu

Forced (<X>):
  👤 Q3 [REQUIRED] Search engine? → "<answer>"
  👤 Q5 [STRATEGIC] Moderation? → "<answer>"

Parse-failed (<Y>):
  👤 Q7 [STRATEGIC] Notification? → "<answer>"

Consensus (<Z> — auto-approved):
  ✅ Q1, Q2, Q4, Q6, Q8

Auto-resolved OPTIONAL (<W>):
  🤖 Q10, Q11, Q12

→ Xác nhận và ghi APPROVED_ANSWERS + DECISION_LOG?
```

Ask user for `confirm` or `abort`.

### Step 5 — Finalize

Build decisions JSON array (one entry per answered question):

```json
[
  {
    "question_id": "Q3",
    "question_text": "...",
    "classification": "REQUIRED",
    "resolution_type": "FORCED_HUMAN",
    "answer": "...",
    "options_considered": ["<agent_a_pos>", "<agent_b_pos>"],
    "rationale": ""
  }
]
```

Then call:

```bash
python -m ai_dev_system.gate.gate1_review finalize \
  --run-id <run_id> \
  --decisions-json '<JSON array>'
```

Output: `{ status: "ok", aa_id, dl_id }` → print run_id + artifact IDs.
Run is now at status `RUNNING_PHASE_1D`. Phase B can be invoked.

---

## Decision resolution_type mapping

| Section | Source | resolution_type |
|---------|--------|----------------|
| forced / parse_failed | user picks agent_a | `FORCED_HUMAN` (answer = agent_a_position) |
| forced / parse_failed | user picks agent_b | `FORCED_HUMAN` (answer = agent_b_position) |
| forced / parse_failed | user approves moderator | `FORCED_HUMAN` (answer = moderator_summary) |
| forced / parse_failed | user overrides with text | `OVERRIDE` (answer = override text) |
| consensus | auto-approved | `CONSENSUS` (answer = moderator_summary) |
| consensus | user overrides | `OVERRIDE` (answer = override text) |
| auto_resolved | accepted | `CONSENSUS` (answer = "auto-resolved OPTIONAL") |

---

## Backward compatibility

Legacy runs (no brief v2): the `is_legacy` flag is true in the load output.
Brief edit is not available for legacy runs. 4 sections still shown but without
decision context (no decision inventory exists).

---

## Error handling

- If `load` fails → tell user the run_id was not found or has no DEBATE_REPORT
- If `finalize` fails → show the error, do NOT retry automatically
- If user input is `unknown` → echo `parser.message` asking to rephrase

See design spec: `docs/superpowers/specs/2026-05-23-gate1-skill-redesign.md`
