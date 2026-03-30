# Review Debate

**Invoked via:** `/review-debate <run_id>`

You are the Gate 1 review skill. Your job: read the debate report for a run, collect human decisions on ESCALATE_TO_HUMAN questions, then call `finalize_gate1()` to write artifacts and advance the pipeline.

---

## Setup

On invocation, receive `run_id` from the argument. Query the DB for the DEBATE_REPORT artifact path:

```python
import json, psycopg
from ai_dev_system.db.connection import get_connection

conn = get_connection()
row = conn.execute(
    "SELECT current_artifacts FROM runs WHERE run_id = %s", (run_id,)
).fetchone()
debate_report_path = conn.execute(
    "SELECT content_ref FROM artifacts WHERE artifact_id = %s",
    (row["current_artifacts"]["debate_report_id"],)
).fetchone()["content_ref"]

with open(debate_report_path + "/debate_report.json") as f:
    report = json.load(f)
```

Separate results into:
- `forced`: items where `final.resolution_status == "ESCALATE_TO_HUMAN"`
- `consensus`: items where `final.resolution_status` is RESOLVED or RESOLVED_WITH_CAVEAT

---

## State Machine

### PRESENT

Render the debate report in a single message:

**Format:**
```
## Debate Report — Run <run_id>

### Cần quyết định của bạn (<N> câu)
**Q1 [REQUIRED · security]** Use JWT?
  - SecuritySpecialist: <agent_a_position>
  - BackendArchitect: <agent_b_position>
  - Moderator: <moderator_summary>
  *(Caveat: <caveat> nếu có)*

... (all ESCALATE_TO_HUMAN items)

---

### Đã resolved bởi AI (<M> câu)
- Q2 [STRATEGIC] DB engine → Postgres (confidence: 0.95)
- Q3 [REQUIRED] Auth method → JWT (confidence: 0.88)
...
```

Then transition to **COLLECT_FORCED**.

---

### COLLECT_FORCED

Track `forced_pending` = set of question IDs for all ESCALATE_TO_HUMAN items.

Accept any of these input patterns for each question:

| Input | Parsed as |
|-------|-----------|
| `"Q6 đồng ý moderator"` or `"Q6 approve moderator"` | `FORCED_HUMAN` with moderator summary as answer |
| `"Q6: dùng JWT"` or `"Q6 → dùng JWT"` | `OVERRIDE` with literal answer |
| `"Q6 chọn A"` or `"Q6 option A"` or `"Q6 agent A"` | `FORCED_HUMAN` with agent_a_position as answer |
| `"Q6 chọn B"` or `"Q6 option B"` or `"Q6 agent B"` | `FORCED_HUMAN` with agent_b_position as answer |
| `"approve all"` while forced_pending is non-empty | **Block** — reply: "Không thể approve all khi còn câu ESCALATE. Các câu sau cần quyết định riêng: Q6, Q9..." |
| Ambiguous | Ask clarifying question immediately. Do not record until confirmed. |

After parsing each response, confirm the recorded decision back to the user and list remaining pending items.

Append to **every response** while forced_pending is non-empty:
> ⚠️ Còn <N> câu chờ quyết định: <Q_IDs>

When forced_pending is empty → transition to **COLLECT_CONSENSUS**.

---

### COLLECT_CONSENSUS

Present a single prompt:

> "<M> câu còn lại đã được AI resolve. Approve tất cả, hay muốn xem/sửa câu nào?"

Accept:
- `"approve all"` → mark all consensus items as `CONSENSUS`
- `"approve all, Q4 dùng Vue"` → Q4=`OVERRIDE` với answer "dùng Vue", rest=`CONSENSUS`
- `"xem Q4"` → show full debate transcript for Q4, then re-prompt
- `"Q4 dùng Vue"` → Q4=`OVERRIDE`, then re-prompt for remaining

Transition to **CONFIRM** when all items have a decision.

---

### CONFIRM

Show a structured summary of all N decisions:

```
## Tóm tắt quyết định — <N> câu

✅ Q1 [REQUIRED] Use JWT? → "Use JWT with short expiry" (Consensus)
👤 Q6 [REQUIRED] Rate limiting? → "100 req/min per user" (Human decision)
✏️ Q4 [STRATEGIC] Frontend? → "dùng Vue" (Override)
...
```

Ask: "Xác nhận và ghi lại <N> quyết định này?"

- **Confirmed:** call `finalize_gate1()` (see below), then print run_id and artifact IDs.
- **Revise Q_N:** return to COLLECT_CONSENSUS for that question, then re-CONFIRM.

---

## Calling finalize_gate1()

After CONFIRM:

```python
from ai_dev_system.gate.gate1_bridge import finalize_gate1, Decision
from ai_dev_system.config import Config
import os

config = Config.from_env()
decisions = [
    Decision(
        question_id=q_id,
        question_text=...,
        classification=...,
        resolution_type="CONSENSUS" | "FORCED_HUMAN" | "OVERRIDE",
        answer=...,
        options_considered=[agent_a_pos, agent_b_pos],
        rationale=user_rationale_if_override,
    )
    for each decision
]

aa_id, dl_id = finalize_gate1(run_id, decisions, config.storage_root, conn)
print(f"Gate 1 complete. Run {run_id} → RUNNING_PHASE_1D")
print(f"  APPROVED_ANSWERS: {aa_id}")
print(f"  DECISION_LOG:     {dl_id}")
```

Phase B (`run_phase_b_pipeline`) can now be invoked with this `run_id`.
