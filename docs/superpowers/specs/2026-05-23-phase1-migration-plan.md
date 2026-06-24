# Design Spec: Phase 1 Migration Plan

**Date:** 2026-05-23
**Status:** Draft
**Scope:** Migration strategy from current Phase 1 (brief v1 skeleton, single-call generators) to Phase 1 v2 (intake wizard, multi-stage pipelines). Covers DB schema, artifact versioning, run state handling, code deprecation, rollout sequencing.

---

## Motivation

5 spec đi kèm (Intake Wizard, Eval Harness, Question Gen, Debate Upgrade, Spec Gen v2, Gate 1 Redesign) introduce **breaking changes** ở:

- Brief schema (v1 skeleton → v2 30+ field structured)
- Question pipeline (1 call → 4-stage)
- Debate output (new status `MODERATOR_PARSE_FAILED`, new fields)
- Spec generation (1 call → multi-section + trace map)
- Gate 1 skill (markdown state machine → Python-backed)
- New artifact types (`INTAKE_BRIEF`, `DECISION_INVENTORY`, `QUESTION_COVERAGE_REPORT`, `BRIEF_EDIT_LOG`, `SPEC_TRACE_MAP`, `SPEC_GROUNDING_VIOLATIONS`)

Migration phải:
1. Không phá run đang chạy ở production
2. Cho phép incremental rollout (mỗi spec merge độc lập)
3. Có flag để rollback nếu spec mới có bug
4. Migration data 1 chiều (v1 → v2 trên existing run) là **out of scope** — chỉ migrate code path, không backfill brief content

---

## Goals

- Schema migration plan: SQL migrations sequenced, idempotent
- Feature flags per spec để rollout staged
- Legacy code path preserved cho 1 release cycle
- Run state classifier: every existing run can be classified `migrate | legacy_continue | abort`
- Test plan: golden runs covering pre/post migration
- Rollback plan: every change reversible within 24h

## Non-goals

- **Không** backfill brief v2 cho run cũ (legacy continue đường cũ)
- **Không** auto-upgrade Phase 1 mid-run (run đang ở Gate 1 vẫn chạy v1 đến hết)
- **Không** dual-write (write to both v1 + v2 schemas) — không scale
- **Không** zero-downtime migration (Phase 1 is offline pipeline, downtime ok)

---

## Architecture: Three-Plane Migration

```
┌─────────────────────────────────────────────────────────────┐
│ Plane 1: Schema                                              │
│   - DB migrations (additive only, no DROP)                   │
│   - New tables / columns / artifact types                    │
│   - Idempotent, reversible                                   │
├─────────────────────────────────────────────────────────────┤
│ Plane 2: Code                                                │
│   - Feature flags per spec                                   │
│   - Legacy + new code coexist                                │
│   - Dispatcher chooses path based on run.pipeline_version    │
├─────────────────────────────────────────────────────────────┤
│ Plane 3: Data                                                │
│   - Existing runs flagged legacy=true on first touch         │
│   - New runs born with pipeline_version=2                    │
│   - No data backfill                                         │
└─────────────────────────────────────────────────────────────┘
```

---

## Plane 1: Schema Migrations

### Migration files

`docs/schema/migrations/v5-phase1-v2.sql` — single atomic migration grouping all Phase 1 v2 additions:

```sql
-- v5-phase1-v2.sql
-- Phase 1 v2 — add brief v2, intake, decisions, coverage, trace map

BEGIN;

-- runs table: add pipeline version + intake state
ALTER TABLE runs ADD COLUMN IF NOT EXISTS pipeline_version int NOT NULL DEFAULT 1;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS intake_state jsonb;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS intake_brief_id uuid REFERENCES artifacts(artifact_id);
ALTER TABLE runs ADD COLUMN IF NOT EXISTS gate1_session_state jsonb;
ALTER TABLE runs ADD COLUMN IF NOT EXISTS legacy boolean NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS idx_runs_pipeline_version ON runs(pipeline_version);
CREATE INDEX IF NOT EXISTS idx_runs_legacy ON runs(legacy) WHERE legacy = true;

-- artifact_types enum extension (depends on existing type system)
-- If using TEXT column with CHECK: ALTER constraint
-- If using PostgreSQL ENUM: ALTER TYPE artifact_type ADD VALUE ...
ALTER TYPE artifact_type ADD VALUE IF NOT EXISTS 'INTAKE_BRIEF';
ALTER TYPE artifact_type ADD VALUE IF NOT EXISTS 'DECISION_INVENTORY';
ALTER TYPE artifact_type ADD VALUE IF NOT EXISTS 'QUESTION_COVERAGE_REPORT';
ALTER TYPE artifact_type ADD VALUE IF NOT EXISTS 'BRIEF_EDIT_LOG';
ALTER TYPE artifact_type ADD VALUE IF NOT EXISTS 'BRIEF_FINAL';
ALTER TYPE artifact_type ADD VALUE IF NOT EXISTS 'SPEC_TRACE_MAP';
ALTER TYPE artifact_type ADD VALUE IF NOT EXISTS 'SPEC_GROUNDING_VIOLATIONS';

-- new run statuses (if using ENUM)
ALTER TYPE run_status ADD VALUE IF NOT EXISTS 'COLLECTING_INTAKE';
ALTER TYPE run_status ADD VALUE IF NOT EXISTS 'READY_FOR_DEBATE';
ALTER TYPE run_status ADD VALUE IF NOT EXISTS 'RUNNING_PHASE_1B_INVENTORY';
ALTER TYPE run_status ADD VALUE IF NOT EXISTS 'RUNNING_PHASE_1B_MATERIALIZE';
ALTER TYPE run_status ADD VALUE IF NOT EXISTS 'RUNNING_PHASE_1B_CRITIC';
ALTER TYPE run_status ADD VALUE IF NOT EXISTS 'RUNNING_PHASE_1B_COVERAGE';
ALTER TYPE run_status ADD VALUE IF NOT EXISTS 'FAILED_AT_QUESTION_INVENTORY';
ALTER TYPE run_status ADD VALUE IF NOT EXISTS 'FAILED_AT_QUESTION_COVERAGE';

-- new resolution_status value (for debate)
ALTER TYPE resolution_status ADD VALUE IF NOT EXISTS 'MODERATOR_PARSE_FAILED';

-- backfill: mark all existing runs as legacy=true
-- Safe because new code reads `legacy` flag and dispatches to old code path
UPDATE runs SET legacy = true WHERE pipeline_version = 1;

COMMIT;
```

### Migration safety rules

- **Additive only:** no DROP COLUMN, no DROP TYPE, no DROP TABLE in v5. Cleanup migration (v6) ships ≥1 month later if all v1 runs archived.
- **Default values:** all new NOT NULL columns have DEFAULT to handle existing rows.
- **Idempotent:** all `IF NOT EXISTS` / `IF NOT EXISTS` guards. Re-running migration is no-op.
- **Reversible:** ship `v5-phase1-v2-rollback.sql` with `ALTER COLUMN DROP`, `ALTER TYPE` (note: PG ENUM removal is complex — see Open Questions).

### Migration ordering

Per spec build order:
- v5 ships **once**, gates all 5 specs
- Each spec can deploy code independently after v5 applied
- Spec-level feature flags (Plane 2) control which code path activates

### Rollback testing
- Run v5 → snapshot DB
- Run rollback → snapshot DB
- Diff snapshots — must equal pre-migration state minus enum extensions (PG limitation)

---

## Plane 2: Feature Flags

### Flag matrix

```python
# src/ai_dev_system/config.py
@dataclass
class FeatureFlags:
    use_intake_wizard: bool = False           # Spec Intake Wizard
    use_question_pipeline_v2: bool = False    # Spec A
    use_debate_v2: bool = False               # Spec B
    use_spec_gen_v2: bool = False             # Spec C
    use_gate1_v2: bool = False                # Spec D
    eval_harness_enabled: bool = False        # Spec Eval Harness

    @classmethod
    def from_env(cls) -> "FeatureFlags":
        return cls(
            use_intake_wizard=os.getenv("FF_INTAKE_WIZARD", "false").lower() == "true",
            ...
        )
```

### Dispatcher pattern

Every changed module wraps new logic in flag check:

```python
# debate_pipeline.py
def run_debate_pipeline(...):
    if flags.use_intake_wizard and not run.legacy:
        brief = run_intake_wizard(...)
    else:
        brief = normalize_idea(raw_idea)   # legacy path

    if flags.use_question_pipeline_v2 and not run.legacy:
        questions = question_pipeline_v2.run(brief, ...)
    else:
        questions = generate_questions_legacy(brief, ...)

    # ... etc
```

### Rollout sequence

```
T+0:  Deploy code with all flags FALSE. v5 migration applied.
      → No behavior change. Existing tests pass.

T+3d: Enable eval_harness_enabled=true. Establish baseline measurement.

T+1w: Enable use_intake_wizard=true for new runs (legacy=false). Old runs unaffected.
      Monitor: intake completion rate, time to complete, eval Brief Layer metrics.

T+2w: Enable use_question_pipeline_v2=true. Run eval compare master vs new.
      Decision gate: if eval metrics regress >10%, disable flag, investigate.

T+3w: Enable use_debate_v2=true. Same eval check.

T+4w: Enable use_gate1_v2=true.

T+5w: Enable use_spec_gen_v2=true.

T+8w: All flags default ON. Legacy code path deprecation begins.

T+12w: Remove legacy code (v6 migration cleanup).
```

Each enable is **reversible by flipping flag** — no code rollback needed (unless bug in code itself).

### Per-run override
```python
# CLI can force flag for testing single run
ai-dev start --feature use_question_pipeline_v2=false --idea "..."
```

Stored in `runs.feature_overrides jsonb`. Takes precedence over env flags.

---

## Plane 3: Run State Classification

### Classifier rules

Every existing run at migration time is classified:

```python
def classify_run(run: dict) -> Literal["v1_continue", "v2_new", "v2_resume", "abort"]:
    if run.created_at < V5_MIGRATION_TIMESTAMP:
        return "v1_continue"  # legacy=True, run on old code path until terminal state

    if run.pipeline_version == 1 and run.status in TERMINAL_STATUSES:
        return "v1_continue"  # already done, archive only

    if run.pipeline_version == 2:
        if run.status == "COLLECTING_INTAKE":
            return "v2_resume"  # resume intake wizard
        return "v2_new"

    return "abort"  # unexpected combination
```

### Action per classification

| Classification | Action |
|---|---|
| `v1_continue` | Run finishes on legacy code path. No migration. Read-only after terminal. |
| `v2_new` | Born after migration, full v2 pipeline. |
| `v2_resume` | Resume from `intake_state` jsonb. Skill `/start-project` detects. |
| `abort` | Mark `status=ABORTED`, log to migration_audit. Manual review. |

### Migration audit log

`migration_audit` table:
```sql
CREATE TABLE IF NOT EXISTS migration_audit (
    id serial PRIMARY KEY,
    run_id uuid NOT NULL,
    migration_version int NOT NULL,
    classification text NOT NULL,
    classified_at timestamptz NOT NULL DEFAULT now(),
    notes text
);
```

Populated by classifier on first read of each run post-migration. Allows post-migration analysis: how many legacy vs new vs aborted.

---

## Data Migration: What We Don't Do

### Backfill rejected
Backfilling brief v1 → v2 would require:
- LLM call per legacy run to expand skeleton into 30+ fields
- Risk of hallucinated values polluting historical record
- No clear benefit (legacy runs already completed or are at terminal gate)

Decision: legacy runs stay legacy. New work uses v2.

### Schema upgrade for in-flight runs
Run that's mid-execution when migration deploys: **continues on v1 code path** (legacy=true was UPDATEd to true for it). It will not adopt v2 mid-pipeline.

### Archived runs
Runs in terminal status (COMPLETED/FAILED/ABORTED) are read-only. Migration just marks `legacy=true` for audit clarity.

---

## Code Deprecation Schedule

### Phase 1 deprecation (T+8w)
Mark deprecated:
- `normalize_idea()` — emit `DeprecationWarning`
- `generate_questions()` (legacy) — emit warning
- Old `agents.py` exports — already wrappers, log usage
- Old `finalize_spec()` direct path — wrapper continues but warns
- Old `review-debate.md` state machine markdown — skill loader detects, warns

### Phase 2 deprecation (T+12w)
Remove:
- Code files marked deprecated
- Feature flags (default behavior is v2, no flag needed)
- v1 legacy code paths in dispatchers

`runs.legacy=true` records preserved (read-only). Code can still parse them via separate `legacy_loader.py` module (frozen).

### Migration v6 cleanup
T+12w SQL:
```sql
-- v6-cleanup.sql (T+12w)
-- Remove unused columns IF all production runs are v2 or terminal

-- NOT removing legacy column — preserved for audit
-- NOT removing pipeline_version=1 paths — preserved for legacy_loader

-- Only removing temporary columns added during transition
ALTER TABLE runs DROP COLUMN IF EXISTS feature_overrides;
-- ... if all runs converged
```

Conservative. **When in doubt, don't drop.**

---

## Test Plan

### Pre-migration baseline
- Capture eval harness output on master: `ai-dev eval run --tag pre-v5 --mode real`
- Snapshot: all golden ideas pass current thresholds
- Document: pre-migration metrics in `migration_baseline.md`

### Per-spec rollout testing

Each spec PR includes:
1. Unit tests for new code (per spec build order)
2. Integration test: golden idea passes through new pipeline
3. Regression test: eval harness compare with previous tag, no metric regresses >10%
4. Flag-flip test: enable + disable in same test, both produce valid output
5. Legacy compat test: run with `legacy=true` exercises old path

### End-to-end migration test

`tests/integration/test_phase1_v2_e2e.py`:
- Apply v5 migration to fresh test DB
- Run 1 golden idea end-to-end with all flags ON
- Assert: artifacts created in expected order, status transitions correct, trace map valid, eval metrics pass
- Run 1 legacy idea (pre-built v1 fixture) → assert legacy path still works

### Rollback test

`tests/integration/test_migration_rollback.py`:
- Apply v5
- Run partial v2 pipeline (status=COLLECTING_INTAKE)
- Apply v5 rollback
- Assert: run is recoverable manually (data preserved for inspection)
- Note: PG enum values cannot be removed cleanly — accept this limitation, document.

---

## Rollback Plan

### Triggers
- Critical bug in v2 path causes data corruption / pipeline failure
- Eval metrics regress >20% on real-mode
- Multiple user reports of broken Gate 1

### Procedure

**Step 1: Disable flag** (immediate, <1 min)
```bash
export FF_QUESTION_PIPELINE_V2=false
# or via config file, then restart workers
```
New runs revert to legacy path. In-flight runs continue current path.

**Step 2: If flag disable insufficient** (within 1h)
- Revert code deployment to previous git SHA
- v5 schema stays applied (additive only, doesn't break old code)
- Affected runs: status checked, retried if needed

**Step 3: Schema rollback** (last resort, within 24h)
- Apply `v5-phase1-v2-rollback.sql`
- Caveat: PG ENUM values persist (PG limitation)
- New runs would fail if code expects new columns — must deploy old code first

**Step 4: Post-mortem**
- Document failure mode in `docs/migration_postmortems/v5-{date}.md`
- Update spec or code, retry rollout

### Communication
- Migration plan announces 1 week before T+0 in CHANGELOG.md
- Each rollout milestone (T+1w, T+2w...) updated in CHANGELOG
- Rollback events: immediate update + post-mortem within 48h

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| PG enum extension fails on old version | Low | High | Test on PG 13/14/15/16, document required version |
| Feature flag combo creates inconsistent state | Med | High | Combo test matrix: 2^6 = 64 combos, test 8 critical |
| v2 LLM cost spike (more calls) | High | Med | Cost monitoring per run, alert >$2/run |
| Intake wizard abandonment (user quits mid) | High | Low | Resume mechanism (spec Intake Wizard) |
| Eval harness false negatives block rollout | Med | Med | Manual override flag for known-good drift |
| Brief v2 schema needs change after release | Med | High | Versioned (brief_version field), v3 plan ready |
| Gate 1 Python dispatch slow vs markdown | Low | Low | Profile; markdown was already calling DB |
| User confused by 30+ field intake | Med | Med | UX research after first 5 real users |

---

## Build Order

Migration spec doesn't have implementation slices — it's a **plan**. Implementation interleaves with the other 5 specs:

| When | Migration work |
|---|---|
| Before Spec A/B/C/D/Eval/Intake start | Write v5 migration SQL + apply to test DB |
| Each spec MVP merge | Add feature flag, wire dispatcher, add per-spec rollback test |
| After all 5 specs MVP done | E2E migration test, rollback test |
| Before production rollout | Capture baseline, write rollout runbook |
| Rollout T+0 to T+8w | Execute schedule, monitor metrics |
| T+8w | Begin deprecation phase 1 |
| T+12w | Begin deprecation phase 2, deploy v6 cleanup migration |

---

## Open Questions

1. **PG enum values cannot be removed:** if we add `MODERATOR_PARSE_FAILED` then later decide to remove, the value stays in enum forever. Accept as PG limitation, or use TEXT column with CHECK constraint instead? **Recommend TEXT + CHECK for new statuses** to keep flexibility.

2. **Backward compat duration:** 12 weeks legacy support seems long. Shorter (4 weeks) means faster cleanup but risks active legacy runs. Recommend monitoring: keep legacy alive while any run with `legacy=true` is in non-terminal state.

3. **Feature flag granularity:** 6 flags = 64 combos. Test matrix too big. Recommend: **enforced linear order** — flag N can only be true if flag N-1 is true. Reduces combos to 7.

4. **Dual-mode skill (review-debate):** during transition, skill must handle both v1 (markdown state) and v2 (Python dispatch) runs. Add `legacy` check at top, route to old skill content if true.

5. **DB connection pooling under flag changes:** flag flip at runtime affects new requests only. Existing in-flight don't pick up — is that ok? Recommend: flag read once per pipeline invocation, not per LLM call.

6. **Telemetry:** how to detect "flag X enabled caused regression"? Need per-flag attribution in eval. Add `flags_active` to eval run metadata.

7. **Migration auditing UI:** would CLI `ai-dev migrate status` showing run counts per classification help? Defer until needed.

---

## Out of Scope

- Auto-rollback on metric regression (manual decision)
- Multi-tenant migration (single-tenant system)
- Cross-region migration coordination
- Schema migration for data warehouse / replica (sync via standard tools)
- Backfilling Beads audit trail for v1 runs to v2 schema
- Migration for non-Phase-1 components (Phase 2-4 untouched in this plan)
