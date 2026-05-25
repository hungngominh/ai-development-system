# Phase 1 v2 — Locked Decisions (Review Pass Outcome)

**Date locked:** 2026-05-23
**Status:** All 34 open questions across 8 specs resolved. Implementation can begin.

This file is **authoritative** for design decisions. If any spec's "Open Questions" section conflicts with entries here, this file wins.

---

## Theme 1: Cost & Performance Optimization (LOCKED)

| # | Spec | Decision | Implementation note |
|---|---|---|---|
| 1 | Intake | **Cache suggestions in session** keyed by `(field_id, brief.source_hash)`. Invalidate when brief changes. | `intake/suggest.py` adds in-memory dict, scope = single wizard run |
| 2 | Intake | **Build brief_digest function** (~500 token summary). Used for debate rounds + materializer. | New util `intake/digest.py`. Brief v2 full schema → 500-token digest with critical fields only |
| 3 | Eval | **Cache embeddings** for duplicate detection, per golden-set tag. | `.eval_runs/<tag>/embeddings_cache.pkl` |
| 4 | Question Gen | **Inventory caching** by hash of structural brief fields (strip formatting/whitespace before hashing). | `pipeline.py` checks cache before Stage 1 LLM call |
| 5 | Debate | **Enable Anthropic prompt caching** for dense agent system prompts from slice D1. | Wire `cache_control` field in `llm/anthropic.py` |
| 6 | Debate | **Embedding model: OpenAI `text-embedding-3-small`** for echo detection. | Cost ~$0.02/1M tokens, acceptable |
| 7 | CLI | **Run-id completion via file cache** `~/.ai-dev-system/recent_runs.txt`, refresh every 60s. | `cli/core/completion.py` |
| 8 | Gate 1 | **Clear `gate1_session_state`** when `run.status` transitions out of `PAUSED_AT_GATE_1`. | DB trigger or finalize_gate1() hook |

---

## Theme 2: Quality vs Simplicity Trade-offs (LOCKED)

| # | Spec | Decision |
|---|---|---|
| 9 | Question Gen | **Critic uses same model as materializer by default.** Cross-model critic (Claude+GPT-4) requires A/B test on golden set before enabling. Config flag exists but default off. |
| 10 | Question Gen | **Critic loop guard:** track sha256 of question_text. If rewrite produces previously-seen text → force `action=drop` instead of rewriting again. |
| 11 | Spec Gen | **Inline marker granularity = paragraph-level by default.** Sentence-level only for verbatim quotes (e.g., problem_statement quoted exactly). |
| 12 | Spec Gen | **Section length: soft warn + log.** Never truncate generated content. Log event `SECTION_LENGTH_EXCEEDED` for review. |
| 13 | Spec Gen | **Repair tracks violation delta.** Pre/post repair violation diff stored in `SPEC_GROUNDING_VIOLATIONS` artifact. Alert event if repair introduces new violation. |
| 14 | Spec Gen | **Trace map synthesis type:** `{"type": "synthesis", "sources": [<list of contributing fields>]}`. Not required to be 1-to-1 with single source. |
| 15 | Gate 1 | **NLU fallback: 1 small LLM call** when regex doesn't match. Cache `(input_normalized, parsed_action)` per session. |

---

## Theme 3: Critical-Path Decisions (LOCKED)

| # | Spec | Decision | Rationale |
|---|---|---|---|
| 16 | Migration | **Use TEXT + CHECK constraint** for new status enums (not PG ENUM). | PG enum values cannot be removed; TEXT keeps flexibility |
| 17 | Migration | **Backward compat = monitoring-based.** Keep legacy alive while any non-terminal legacy run exists. **Minimum 4 weeks.** | Avoids data loss from premature cleanup |
| 18 | Migration | **Feature flags enforced linear order:** flag N can only be `true` if flag N-1 is `true`. Reduces 64 combos → 7. | Test matrix manageable, prevents incoherent state |
| 19 | Migration | **Eval run metadata includes `flags_active` field.** Per-flag attribution for regression analysis. | Without this, can't tell which flag caused metric drop |
| 20 | CLI | **Library: typer.** | Type-hint driven, modern, less boilerplate than argparse |
| 21 | CLI | **Renderer: rich.** | Already transitive via crewai; mature markdown/table support |
| 22 | CLI | **Async commands block by default + `--background` flag** to detach. | Consistent UX, opt-in async |
| 23 | Debate | **Host agency-agents in-repo** at `references/agency-agents/*.md`. Versioned with code. | Simpler than external; updates require deploy (acceptable for prompt changes) |

### Linear flag order (for #18)

```
Flag 1: eval_harness_enabled
Flag 2: use_intake_wizard          (requires flag 1)
Flag 3: use_question_pipeline_v2   (requires flag 2)
Flag 4: use_debate_v2              (requires flag 3)
Flag 5: use_gate1_v2               (requires flag 4)
Flag 6: use_spec_gen_v2            (requires flag 5)
```

Code enforces: enabling flag N when N-1 is false → CLI error with explanation.

---

## Theme 4: Defer-Until-Needed (LOCKED as deferred)

All 11 deferred. Revisit triggers:

| # | Spec | Item | Trigger to revisit |
|---|---|---|---|
| 24 | Intake | Multi-language template (`lang: vi/en`) | First non-Vi user |
| 25 | Intake | `micro_v1` template for tiny ideas | ≥10 real projects show pattern |
| 26 | Question Gen | Conditional / dependent questions | Golden idea exposes blocker |
| 27 | Question Gen | Multi-language brief detection | Same as #24 |
| 28 | Debate | Multilingual agent prompts (`_en.md`) | Same as #24 |
| 29 | Debate | Per-domain agent variants | ≥10 projects show pattern |
| 30 | Spec Gen | Diagram generation (Mermaid/PlantUML) | Verification phase explicit requirement |
| 31 | Spec Gen | Spec section ordering per project type | Same as #25 |
| 32 | CLI | Short aliases (`ai-dev g` etc) | User requests |
| 33 | CLI | Plugin extensibility (entry_points) | External contributor emerges |
| 34 | Migration | Migration auditing UI | When migration_audit table reaches >100 rows |

**Rule:** None of the above blocks Phase 1 v2 rollout. Adding any requires its own spec.

---

## Theme 5+6: Eval & Skill Polish (LOCKED)

| # | Spec | Decision |
|---|---|---|
| 35-Eval-1 | Eval | **Start with 8 golden ideas.** Add more only when measurable gap appears. |
| 35-Eval-2 | Eval | **Regex-first for coverage check.** Add LLM judge fallback only if regex miss rate >20% on golden set. |
| 35-Eval-3 | Eval | **NO auto-block CI on metric regression.** Comparison rendered in PR description, human reviewer decides. |
| 35-Eval-4 | Eval | **Refresh regex patterns quarterly** OR when LLM model upgrades. Calendar reminder in `docs/maintenance.md`. |
| 35-Gate1-1 | Gate 1 | **Accept shrunk skill markdown** (~50 lines). Skill header links to design spec for context. |
| 35-Gate1-2 | Gate 1 | **Ship G7 (brief edit-only) before G8 (re-trigger).** Two separate slices. |
| 35-Gate1-3 | Gate 1 | **UI language: Vietnamese for user-facing text, English for field IDs / artifact names.** |
| 35-Mig-1 | Migration | **Dual-mode skill:** `/review-debate` checks `run.legacy` flag at top, routes to old content if true. |
| 35-Mig-2 | Migration | **Feature flag read once per pipeline invocation**, not per LLM call. Avoids mid-pipeline behavior change. |
| 35-CLI-1 | CLI | **Validate config on every command.** Warn (not block) if validation fails. Suggest `ai-dev setup`. |

---

## Consolidated Implementation Impact

### New utility modules (referenced across specs)
- `intake/digest.py` — brief_v2 → 500-token digest (decision #2)
- `cli/core/completion.py` — run-id cache + shell completion (decision #7)
- `eval/embeddings_cache.py` — duplicate detection cache (decision #3)
- `db/legacy_loader.py` — read legacy artifacts post-deprecation (Migration plan)

### Schema constraint changes (Migration v5 SQL)
- Use `TEXT + CHECK` for new status enums (decision #16):
  ```sql
  -- Instead of ALTER TYPE ... ADD VALUE for new statuses
  ALTER TABLE runs ADD CONSTRAINT runs_status_check_v2 CHECK (
    status IN (... full list including new statuses ...)
  );
  ```
- Replace `ALTER TYPE artifact_type ADD VALUE` similarly

### Dependencies to add
- `typer>=0.12` (decision #20)
- `rich>=13` (decision #21, may already be transitive)
- OpenAI SDK for embeddings (decision #6, already in deps)

### Test matrix shrinkage
- Feature flag combo tests: 7 linear states instead of 64 combos (decision #18)
- Golden set: 8 ideas, not 12 (decision #35-Eval-1)

---

## Cross-References to Spec Files

| Decision area | Authoritative spec |
|---|---|
| 1, 2 (intake cache, digest) | `2026-05-23-intake-wizard-design.md` |
| 3, 35-Eval-* (eval) | `2026-05-23-evaluation-harness-design.md` |
| 4, 9, 10 (question gen) | `2026-05-23-question-generation-redesign.md` |
| 5, 6, 23 (debate) | `2026-05-23-debate-engine-upgrade.md` |
| 11-14 (spec gen) | `2026-05-23-spec-generation-v2.md` |
| 8, 15, 35-Gate1-* (gate 1) | `2026-05-23-gate1-skill-redesign.md` |
| 16-19, 35-Mig-* (migration) | `2026-05-23-phase1-migration-plan.md` |
| 7, 20-22, 35-CLI-1 (CLI) | `2026-05-23-cli-unification.md` |

When implementing, refer to **both** the spec and this decisions doc. Spec defines *what*, decisions doc defines *which option*.

---

## Implementation Readiness Checklist

Before starting any slice:

- [x] All 34 open questions resolved
- [x] Build order documented in each spec
- [x] Dependency graph between specs documented (in [[phase1-v2-spec-bundle]] memory)
- [x] Feature flag linear order locked
- [x] Migration plan v5 SQL drafted
- [ ] Eval baseline captured on master (Slice E1-E2 before anything else)
- [ ] v5 SQL applied to test DB
- [ ] `typer` + `rich` added to pyproject.toml

Last 3 items are **first concrete actions** when implementation kicks off.

---

## Addendum 2026-05-25 — Cross-cutting decisions from M3–M7 readiness audit

Closing the 7 gaps identified during the M3–M7 pre-implementation review. These supersede any conflicting "Open Question" notes still left in individual M-specs.

| # | Spec / area | Decision | Rationale / implementation note |
|---|---|---|---|
| 36 | Gate 1 G8 — brief edit re-trigger | **Materializer-only on diff.** Re-run Stage 2 (Materializer) for decision fields touched by the brief edit. Do NOT re-run Stage 1 Inventory. Append new questions to existing `QUESTION_COVERAGE_REPORT`. | Cheapest path that fits vertical-slice. Risk: new decisions appearing from edit are missed → accept, revisit in a later milestone if a golden idea exposes the gap. See `2026-05-25-g8-brief-edit-retrigger.md` for full flow. |
| 37 | Spec Gen v2 — trace map marker syntax | **Inline `[brief:field_id]` and `[decision:decision_id]` markers** embedded directly in section markdown. Tracer extracts via regex `\[(brief\|decision):([a-z0-9_]+)\]`. | Visible to human reviewers, simple regex parse, no markdown linter friction. Generator prompts must instruct LLM to emit markers inline at paragraph end. |
| 38 | LLM-as-judge model selection | **Sonnet 4.6 default; Opus 4.7 only for M7 Spec Gen grounding check (G5).** Applies to eval metrics, Question Gen critic (Stage 3), and any future LLM judge. Single-pass, no ensemble. | Cost cap ~$0.05/eval run, ~$0.10/spec. Grounding hallucinations have the highest downstream cost (bad spec ships) → Opus justified there only. |
| 39 | Brief digest lifecycle | **Compute once at intake promote; persist as `BRIEF_DIGEST` artifact; downstream loads by artifact_id.** Brief edit at Gate 1 → invalidate (mark superseded) + emit new `BRIEF_DIGEST` artifact. Never compute on the fly. | Single source of truth; deterministic across M4/M5/M7; clean invalidation. Adds one artifact_type to schema (see below). |
| 40 | Domain registry ownership | **Live in `src/ai_dev_system/debate/domains.py`** as canonical 12-domain enum + alias map. Both M4 (question gen domain hints) and M5 (debate agent loader) import from here. | Avoids duplication and drift. Specific 12-domain list approved separately (see Appendix A below once finalized). |
| 41 | G8 cap exceed behavior | **Soft warn at edits 6+, never block.** After the 5th edit per Gate 1 session, each subsequent edit emits a `BRIEF_EDIT_THRESHOLD_EXCEEDED` event and shows a CLI warning, but the edit is applied and the loop continues. | User exploration matters more than LLM cost protection at Phase 1. Audit retained in full `BRIEF_EDIT_LOG`. |
| 42 | Debate `RoundResult.resolution_status` | **Add `MODERATOR_PARSE_FAILED` to Python `Literal` in `src/ai_dev_system/debate/report.py`** as M0 patch (applied 2026-05-25). Distinct from the event-type `MODERATOR_PARSE_FAIL` (already in `control-layer-schema.sql:286`). | Unblocks M5 D5 (JSON robust parse) and M6 Gate 1 parse-failed section UI without waiting for M5 start. |
| 43 | Question Gen Critic — merge action | **MVP simplification**: merge = drop the source question, keep the target question's text unchanged. Source `source_decision_id` is lost (target keeps its own). Per-merge LLM call to synthesize broader text is deferred. Question.source_decision_id stays `str \| None` (not list). | Spec M4 §"Merge sub-logic" (line 293-296) describes LLM-synthesized merged text with `source_decision_id: list[str]` and higher-classification preservation. Implementing fully would ripple through Question schema, coverage.py (C1 check), and add 1 LLM call per merge. Vertical-slice priority + dedup is the primary value; revisit when a golden idea exposes a measurable quality loss. Documented in `critic.py` docstring. |

### Schema impact from Decision #39

Add `BRIEF_DIGEST` to `artifact_type` CHECK list in `control-layer-schema.sql` and v5 migration when M2 wiring lands. Until then, the digest is computed but not persisted (transient).

### Cost cap monitoring (Decision #38)

Per-run LLM spend tracked via existing `events` table (event_type `LLM_CALL_COMPLETED` with token counts). A run that exceeds the cap by >2x emits a `COST_CAP_EXCEEDED` event; no hard abort.

### Appendix A — 12-domain registry (approved 2026-05-25)

Canonical 12 domains used by both M4 Question Gen (domain hints per decision) and M5 Debate (agent loader + diversity guardrail). Source-of-truth lives in `src/ai_dev_system/debate/domains.py`; this table mirrors that module for spec readers.

| # | `id` | Label (Vi) | Decision scope | Alias map (LLM frequently emits → resolve to) |
|---|---|---|---|---|
| 1 | `backend` | Backend | API server, business logic, service boundaries | `api`, `server`, `service`, `microservice` |
| 2 | `frontend` | Frontend | Web UI framework, state, bundling, rendering | `web`, `ui`, `client`, `spa`, `react`, `vue` |
| 3 | `mobile` | Mobile | iOS/Android, RN/Flutter, offline, app store | `ios`, `android`, `react-native`, `flutter` |
| 4 | `data` | Data | Schema, ETL, warehouse, analytics pipeline | `database`, `db`, `etl`, `analytics`, `warehouse` |
| 5 | `ml` | ML/AI | Model choice, training, inference, prompts | `ai`, `llm`, `model`, `ml-ops` |
| 6 | `security` | Security | AuthN/AuthZ, threat model, secrets, technical compliance | `auth`, `authn`, `authz`, `crypto`, `compliance` |
| 7 | `infra` | Infrastructure | Cloud, networking, scaling, storage choice | `cloud`, `aws`, `gcp`, `azure`, `k8s`, `kubernetes` |
| 8 | `devops` | DevOps/SRE | CI/CD, observability, on-call, incident response | `ci`, `cd`, `monitoring`, `sre`, `observability` |
| 9 | `qa` | Quality/Testing | Test strategy, automation, perf, AC measurability | `testing`, `test`, `qa-automation`, `performance` |
| 10 | `product` | Product | Feature scope, MVP cut, user story, prioritization | `pm`, `prd`, `mvp`, `roadmap` |
| 11 | `design` | UX/Design | UX flow, IA, visual, accessibility | `ux`, `ui-design`, `a11y`, `figma` |
| 12 | `legal` | Legal/Privacy | GDPR/PDPA, license, T&C, data residency | `privacy`, `gdpr`, `pdpa`, `license`, `compliance-legal` |

**Unrecognized domain rule:** if the LLM returns a domain string not in the canonical id list and not in the alias map, log a `DOMAIN_UNRECOGNIZED` event with the raw text and default to `backend`. This both keeps the pipeline moving and preserves the raw signal for later audit (closes M4 audit gap on alias fallback observability).

**Adding a 13th domain:** requires a new spec (per Locked Decision #23 cap on 12) and updates to: `domains.py`, this Appendix, agent loader files under `references/agency-agents/`, eval golden ideas covering the new domain.
