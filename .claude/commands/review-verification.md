# Review Verification

**Invoked via:** `/review-verification <run_id>`

You are the Gate 3 review skill. Your job: read the VERIFICATION_REPORT for a run, present the full report to the human, collect skip/abort/fix decisions for any FAIL criteria, then call `finalize_gate3()` to advance or close the pipeline.

---

## Setup

On invocation, receive `run_id` from the argument. Query the DB for the active VERIFICATION_REPORT:

```python
import json
import psycopg
import psycopg.rows
from ai_dev_system.db.connection import get_connection

conn = get_connection()
art = conn.execute(
    """
    SELECT content_ref FROM artifacts
    WHERE run_id = %s AND artifact_type = 'VERIFICATION_REPORT' AND status = 'ACTIVE'
    ORDER BY version DESC LIMIT 1
    """,
    (run_id,),
).fetchone()

with open(art["content_ref"] + "/verification_report.json", encoding="utf-8") as f:
    report = json.load(f)

fail_criteria = [c for c in report["criteria"] if c["verdict"] == "FAIL"]
pass_criteria = [c for c in report["criteria"] if c["verdict"] == "PASS"]
attempt = report["attempt"]
```

---

## State Machine

### PRESENT

Render the full report in a single message. Show FAIL criteria with full detail, PASS criteria summarized.

**Format:**
```
✅ Verification Report — Attempt {attempt}/3

✅ PASS ({N}): AC-1, AC-3, AC-5

❌ FAIL ({M}):

❌ AC-4: "User can search posts by tag"
   Reasoning: Search API returns 200 but does not filter by tag
   Evidence: task-6 output — GET /posts?tag=python returns all posts
   Confidence: 0.92

❌ AC-6: "Coverage ≥ 80%"
   Reasoning: pytest-cov report = 71%
   Evidence: task-6 verification_steps output
   Confidence: 0.99

→ Confirm to create remediation tasks, or "skip AC-4" / "abort".
```

**All-pass fast path:** If no FAIL criteria → skip COLLECT_FAILS entirely:
```
✅ All {N} criteria PASS. Confirm completion? (yes/abort)
```

Transition to **COLLECT_FAILS** (or CONFIRM_PASS if no fails).

---

### COLLECT_FAILS

Collect one decision per FAIL criterion. Accept batched input in a single message.

| User says | Parsed as |
|-----------|-----------|
| `"ok create remediation"` / `"fix all"` | All FAILs → remediation (no SKIP) |
| `"skip AC-4, fix AC-6"` | AC-4 → SKIP, AC-6 → remediation |
| `"abort"` | run → ABORTED |
| `"show evidence AC-4"` | Display full evidence for AC-4, stay in COLLECT_FAILS |
| Ambiguous | Ask clarifying question, do not record until confirmed |

After parsing, confirm what was recorded and list remaining undecided criteria.

Transition to **CONFIRM** when all FAIL criteria have a decision.

---

### CONFIRM

Show summary before calling finalize_gate3():

```
📝 Confirm:
  AC-4 → ⏭️  Skip
  AC-6 → 🔧 Create remediation task

Continue? (remediation will re-run the execution loop)
```

- `"ok"` / `"confirm"` / `"yes"` → call `finalize_gate3()` (see below)
- `"edit"` / `"change"` → return to COLLECT_FAILS

### CONFIRM_PASS (all-pass fast path)

- `"yes"` / `"confirm"` → call `finalize_gate3(decisions=[])` → run transitions to COMPLETED
- `"abort"` → prompt: "All criteria passed — are you sure you want to abort? (yes to abort, no to complete)"

---

### PAUSED_AT_GATE_3B (attempt ≥ 3, still failing)

When `finalize_gate3()` transitions to PAUSED_AT_GATE_3B, or if the run is already in this state when the skill is invoked:

```
⚠️ Already attempted 3 times. Still failing: AC-6

Options:
  A. Continue — add 1 more attempt
  B. Skip failing criteria — mark as skip, complete the run
  C. Abort — stop this run entirely
```

- A → call `finalize_gate3(decisions=[])` after resetting attempt counter (or human manually sets run.status = RUNNING_PHASE_V and invokes `/review-verification` again)
- B → call `finalize_gate3(decisions=[Gate3Decision(cid, "SKIP") for each fail])`
- C → call `finalize_gate3(decisions=[Gate3Decision(fail_criteria[0].criterion_id, "ABORT")])`

Max 3 is a **soft limit** — option A lets the human extend.

---

## Calling finalize_gate3()

After CONFIRM:

```python
from ai_dev_system.gate.gate3_bridge import finalize_gate3, Gate3Decision
from ai_dev_system.config import Config

config = Config.from_env()
decisions = [
    Gate3Decision(criterion_id=cid, action=action)
    for cid, action in user_decisions.items()
    # only include FAIL criteria that were SKIP or ABORT
    # pass criteria are implicit — do not include them
]

result = finalize_gate3(run_id, decisions, config.storage_root, conn)

if result.aborted:
    print(f"Run {run_id} → ABORTED")
elif result.has_remediation:
    print(f"Run {run_id} → RUNNING_PHASE_V (remediation queued, attempt {attempt+1})")
    print("Re-run execution with the remediation graph, then invoke /review-verification again.")
else:
    print(f"Run {run_id} → COMPLETED ✅")
```

---

## Principles

- Present full picture immediately — no criterion-by-criterion interruption (consistent with Gate 1)
- CONFIRM is mandatory before remediation — remediation cannot be undone
- `Gate3Decision` only contains FAIL decisions — PASS is implicit
- Attempt ≥ 3 is a soft limit — human can extend by choosing option A
