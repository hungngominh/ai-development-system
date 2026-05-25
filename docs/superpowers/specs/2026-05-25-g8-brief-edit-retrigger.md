# G8 — Brief Edit Re-trigger Logic (Mini-Spec)

**Date:** 2026-05-25
**Status:** Locked. Companion to `2026-05-23-gate1-skill-redesign.md` (M6) and `2026-05-23-question-generation-redesign.md` (M4).
**Authoritative decision:** Locked Decisions #36 (2026-05-23 doc).

This mini-spec closes the ambiguity flagged in the M3–M7 audit: when the user edits the brief at Gate 1, how is Question Gen re-triggered?

---

## Scope

In scope:
- Re-run the Materializer (Stage 2 of Question Pipeline) for decisions whose source brief fields changed.
- Append newly materialized questions to the existing `QUESTION_COVERAGE_REPORT` artifact.
- Emit audit events.

Out of scope (defer to a later milestone):
- Re-running Inventory (Stage 1) to discover *new* decisions introduced by the edit.
- Removing or rewriting questions that were already answered by the user.
- Cross-decision dependency resolution (a brief edit cascading to multiple decisions).

The trade-off: a brief edit that fundamentally changes scope can introduce decisions that Inventory would have caught. We accept this risk for Phase 1 v2 vertical slice. If a golden idea exposes the gap, revisit.

---

## Trigger

`finalize_gate1()` detects `brief_edits` on the approved payload. If `feature.use_question_pipeline_v2` is enabled AND at least one whitelisted field changed AND `gate1_session.retrigger_count < 5`, dispatch `g8_retrigger.run(run_id, brief_edits)`.

`gate1_session.retrigger_count >= 5` → emit `BRIEF_EDIT_THRESHOLD_EXCEEDED`, show CLI warning, still proceed (Decision #41 soft warn).

---

## Flow

```
1. Load DECISION_INVENTORY artifact for this run.
2. Compute affected_decisions = {
       d for d in inventory.decisions
       if any edit.field_id in d.brief_field_refs
   }
3. If affected_decisions is empty:
       emit G8_NOOP event; return.
4. Re-issue BRIEF_DIGEST (Decision #39): invalidate prior digest artifact,
   compute fresh from edited brief, persist new digest artifact.
5. Call materializer.run(
       decisions=affected_decisions,
       brief_digest=new_digest,
       mode="retrigger",
   )
   → returns new_questions[].
6. Critic loop (Stage 3) runs only on new_questions[],
   with same MAX_CRITIC_ITER=2 + sha256 guard (Decision #10).
7. Append surviving new_questions to existing QUESTION_COVERAGE_REPORT artifact:
       - generate question.id with suffix "-r{retrigger_count}" to keep IDs unique.
       - append, never overwrite existing answered questions.
8. Update gate1_session.retrigger_count += 1.
9. Emit G8_RETRIGGER_COMPLETED event with affected_decisions, new_question_ids[].
10. Re-enter Gate 1 review UI; new questions surface in the "Questions remaining" section.
```

---

## Decision → brief field reference

The Inventory Stage 1 prompt (M4 spec) must instruct the model to record, per decision, the list of `brief_field_refs` it consulted. Stored on the `Decision` dataclass:

```python
@dataclass
class Decision:
    id: str
    question_summary: str
    classification: Literal["REQUIRED", "STRATEGIC", "OPTIONAL"]
    domain_hints: list[str]
    blocks_what: list[str]
    has_safe_default: bool
    brief_field_refs: list[str]  # NEW: source brief field ids
```

If a legacy `DECISION_INVENTORY` artifact lacks `brief_field_refs`, G8 treats `affected_decisions = all` (worst-case, full re-materialize). Backward-compat-safe.

---

## ID collision handling

Existing questions: `q1`, `q2`, ..., `q12`.

Edit triggers re-materialize for decisions covered by `q3` and `q7`. New questions emitted: `q3-r1`, `q7-r1` (the suffix encodes retrigger generation).

If a second edit later affects the same decision: `q3-r2`. Numeric base IDs are never reused or shifted, preserving any user answers already bound to original IDs.

---

## Artifact schema delta

`QUESTION_COVERAGE_REPORT` gains:

```yaml
retriggers:
  - retrigger_id: 1
    triggered_at: "2026-05-25T08:14:32Z"
    edited_fields: ["scope_in", "success_metric"]
    affected_decision_ids: ["voting_abuse", "metric_threshold"]
    new_question_ids: ["q3-r1", "q7-r1"]
```

The base `decisions` and `questions` arrays remain append-only.

---

## Events emitted

| event_type | When | Payload |
|---|---|---|
| `G8_RETRIGGER_STARTED` | Step 1 begins | edited_fields, retrigger_count |
| `G8_NOOP` | Step 3: no affected decisions | edited_fields |
| `G8_RETRIGGER_COMPLETED` | Step 9 | affected_decision_ids, new_question_ids |
| `BRIEF_EDIT_THRESHOLD_EXCEEDED` | retrigger_count > 5 | retrigger_count, edited_fields |

All event types must be added to `control-layer-schema.sql` CHECK constraint at the M6 G8 implementation slice.

---

## Test plan

- Unit: `affected_decisions` computation given various edit/inventory combos including empty intersection.
- Unit: ID suffix generation across multiple retriggers on the same decision.
- Integration: full Gate 1 → edit → retrigger → re-review loop with stub LLM, asserting coverage report append shape.
- Integration: legacy `DECISION_INVENTORY` without `brief_field_refs` triggers worst-case full re-materialize.
- Integration: 6th edit emits threshold event but still applies.

---

## Build order

G8 slice depends on:
- M4 complete (Materializer + Critic stable, `Decision.brief_field_refs` populated).
- M6 G7 shipped (brief edit-only UX, `gate1_session_state` schema live).
- Decision #39 wired (BRIEF_DIGEST artifact lifecycle).

Implemented as a single slice after M6 MVP, before M9 rollout staging.
