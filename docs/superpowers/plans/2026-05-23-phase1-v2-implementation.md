# Phase 1 v2 Implementation Plan (Master Roadmap)

> **For agentic workers:** Use `superpowers:executing-plans` or `superpowers:subagent-driven-development` to implement task-by-task. Checkboxes (`- [ ]`) track progress.

**Goal:** Implement 8 design specs (Intake, Eval, Question Gen, Debate, Spec Gen, Gate 1, Migration, CLI) + 34 locked decisions, taking Phase 1 from current 1-call-per-stage to full multi-stage pipeline with measurement, brief v2, dense agents, traceable specs.

**Authoritative references:**
- All specs: `docs/superpowers/specs/2026-05-23-*.md`
- Locked decisions: `2026-05-23-phase1-v2-locked-decisions.md` (wins if conflicting)
- Memory: `[[phase1-v2-spec-bundle]]`, `[[intake-wizard-design]]`, `[[phase-1-eval-harness-design]]`

**Tech stack additions:** typer ≥0.12, rich ≥13, openai SDK (embeddings), existing Python 3.12 stack.

**Estimated duration:** 10-14 weeks solo dev with AI assist (~68 atomic slices across 9 milestones).

---

## Working Conventions

- **Branch per milestone:** `feat/phase1-v2-m<N>-<short-name>` off `master`
- **One PR per slice** (vertical, mergeable independently per [[vertical-slice-first]])
- **Every PR must include:**
  1. Tests (unit + integration as applicable)
  2. `ai-dev eval compare <prev-tag> <this-tag>` output in description (once eval exists)
  3. Migration notes if schema changes
- **Feature flag rule (decision #18):** flag N enabled only after flag N-1 fully shipped + monitored ≥3 days
- **No code without spec reference.** Each PR body cites which spec slice it implements (e.g., "Implements Spec A slice Q3").

---

## Milestone Dependency Graph

```
M0 Foundation ──┬─→ M1 Eval Baseline ──┐
                │                       ├─→ M4 Question Pipeline ──┐
                ├─→ M2 Intake Wizard ──┤                            ├─→ M7 Spec Gen v2 ──→ M9 Rollout
                │                       └─→ M5 Debate Upgrade ─────┘                        │
                └─→ M3 Eval Expansion ──────────────────────────────→ M6 Gate 1 ───────────┘

M8 CLI Polish — parallel throughout M2-M7
```

---

## M0 — Foundation (Week 1)

**Goal:** Unblock all downstream work. Apply schema, add deps, build minimal CLI framework.

**Acceptance:** `ai-dev info --version` works, v5 SQL applied to test DB, all existing tests still pass.

- [ ] **M0.1** Add `typer>=0.12` and `rich>=13` to `pyproject.toml` dev + runtime deps
- [ ] **M0.2** Apply v5 SQL migration to test DB (`docs/schema/migrations/v5-phase1-v2.sql` per Migration spec, **using TEXT+CHECK not ENUM** per decision #16)
- [ ] **M0.3** Implement `cli/core/parser.py`, `cli/core/output.py`, `cli/core/context.py` (CLI spec slice C1)
- [ ] **M0.4** Implement `cli/core/registry.py` with `@command` decorator + auto-discovery (CLI slice C2)
- [ ] **M0.5** Migrate `ai-dev setup` to new framework, keep behavior identical (CLI slice C3)
- [ ] **M0.6** Wire `--json`, `--quiet`, `--verbose`, `--feature` global flags (CLI spec)
- [ ] **M0.7** Add `FeatureFlags` class with linear-order enforcement (decision #18) — 6 flags default `false`
- [ ] **M0.8** Set up CHANGELOG.md entry for "Phase 1 v2 in progress"
- [ ] **M0.9** Smoke test: `pytest tests/` all green, `ai-dev setup` works, `ai-dev --help` shows new tree

**Branch:** `feat/phase1-v2-m0-foundation`

---

## M1 — Eval Baseline (Weeks 2-3)

**Goal:** Measurement infrastructure first so all later changes have baseline. Captures pre-v2 quality.

**Acceptance:** `ai-dev eval run --tag baseline` produces report.md + aggregate.json on master codebase.

- [ ] **M1.1** (Eval E1) Write 2 golden ideas: `01_internal_forum`, `05_cli_devtool` with 4 files each (raw + intake_script + decisions_required + decisions_forbidden + notes.md)
- [ ] **M1.2** (Eval E2) Implement `eval/metrics/brief_metrics.py` — 6 metrics + unit tests with fixtures
- [ ] **M1.3** (Eval E3) Implement `eval/metrics/question_metrics.py` rule-based portion (5 of 8 metrics: required_decision_coverage, forbidden_decision_rate, duplicate_pair_count, domain_balance_entropy, avg_question_length)
- [ ] **M1.4** (Eval E4) Implement `eval/runners/intake_runner.py` (scripted answer)
- [ ] **M1.5** (Eval E5) Implement `eval/runners/questions_runner.py` (call existing legacy `generate_questions`, log output)
- [ ] **M1.6** (Eval E5) Implement `eval/report.py` — console table + markdown export
- [ ] **M1.7** (Eval E6) Implement `ai-dev eval run|show|list` commands + tag/output structure
- [ ] **M1.8** (Eval E7) Implement `eval/compare.py` — diff between 2 tags
- [ ] **M1.9** Run baseline: `ai-dev eval run --tag pre-v2 --mode real --idea all` on master, commit `migration_baseline.md`
- [ ] **M1.10** Enable feature flag `eval_harness_enabled=true` in default config

**Branch:** `feat/phase1-v2-m1-eval-baseline`
**Deferred to M3:** E8 (6 more ideas), E9 (LLM-based metrics), E10 (debate runner)

---

## M2 — Intake Wizard (Weeks 3-5)

**Goal:** Replace empty `normalize_idea()` with 30+ field wizard. Brief v2 artifact created.

**Acceptance:** `ai-dev intake start` produces `INTAKE_BRIEF` artifact with critical fields filled. Resume works after kill.

- [ ] **M2.1** (Intake S1) Write `intake/templates/generic_v1.yaml` with 30+ field schema (8 critical fields per decision: problem_statement, scope_in, scope_out, success_metric, primary_user, deployment_target, compliance, current_workaround)
- [ ] **M2.2** (Intake S1) Implement `intake/engine.py` pure state machine `next_step(state, input) → (new_state, render)`
- [ ] **M2.3** (Intake S2) Implement `cli/commands/intake.py` — `start` verb running wizard via stdin/stdout
- [ ] **M2.4** (Intake S3) Implement `intake/suggest.py` with dependency map + cache (decision #1)
- [ ] **M2.5** (Intake S3) Implement refuse list for `ai_can_suggest: false` fields
- [ ] **M2.6** (Intake S4) Implement `intake/followup.py` — 4 gap detection logics
- [ ] **M2.7** (Intake S4) Implement `intake/consistency_rules.py` with ~10-15 cross-field rules
- [ ] **M2.8** (Intake S5) Implement DB checkpoint after every answer/suggest/skip
- [ ] **M2.9** (Intake S5) Implement `ai-dev intake resume --run-id` + skill-side auto-detect
- [ ] **M2.10** (Intake) Implement `intake/digest.py` — brief_v2 → 500-token digest (decision #2)
- [ ] **M2.11** (Intake S6) Rewrite `skills/start-project.md` as launcher (~50 lines)
- [ ] **M2.12** (Intake S7) Update `finalize_spec.py` wrapper to accept brief v2 + approved_answers (legacy compat preserved)
- [ ] **M2.13** (Intake S8) Migration script: legacy run flag, status detection
- [ ] **M2.14** Integration test: full wizard run with stub LLM, kill mid-way, resume
- [ ] **M2.15** Enable feature flag `use_intake_wizard=true` for new runs
- [ ] **M2.16** Run eval compare: `ai-dev eval compare pre-v2 m2-intake` — verify brief metrics improve

**Branch:** `feat/phase1-v2-m2-intake-wizard`

---

## M3 — Eval Expansion (Week 5, parallel with M2 tail)

**Goal:** Extend golden set + add LLM-based metrics + comparison polish.

**Acceptance:** All 8 golden ideas have full expected files. `ai-dev eval compare` produces meaningful diff.

- [ ] **M3.1** (Eval E8) Write 6 remaining golden ideas: 02_data_pipeline, 03_mobile_b2c_app, 04_ml_inference_service, 06_saas_b2b, 07_legacy_migration, 08_security_audit_tool
- [ ] **M3.2** (Eval E8) Implement `ai-dev golden init|validate|dryrun` tooling
- [ ] **M3.3** (Eval E9) Implement LLM-based metrics: `q.binary_yes_no_ratio`, `q.scope_drift_count`, brief field_coverage
- [ ] **M3.4** (Eval E9) Stub-mode behavior for LLM metrics (return neutral 0.5)
- [ ] **M3.5** Run eval on all 8 ideas, real-mode, save as `pre-v2-full` baseline tag

**Branch:** `feat/phase1-v2-m3-eval-expansion`

---

## M4 — Question Pipeline v2 (Weeks 6-8)

**Goal:** Replace single-call `generate_questions` with 4-stage pipeline.

**Acceptance:** Eval question metrics improve ≥20% on golden set. Question coverage ≥85%.

- [ ] **M4.1** (Question Q1) Implement `debate/questions/domains.py` — 12-domain registry (decision: extensible)
- [ ] **M4.2** (Question Q1) Add `source_decision_id: str | None` field to `Question` dataclass
- [ ] **M4.3** (Question Q2) Implement `questions/inventory.py` + `prompts/inventory.txt`
- [ ] **M4.4** (Question Q2) Implement inventory caching by structural brief hash (decision #4)
- [ ] **M4.5** (Question Q3) Implement `questions/materializer.py` with batch + per-decision fallback
- [ ] **M4.6** (Question Q4) Implement `questions/pipeline.py` skeleton wiring stages 1+2
- [ ] **M4.7** (Question Q5) Implement `questions/critic.py` — flag detection only (4 flag types)
- [ ] **M4.8** (Question Q6) Implement critic rewrite + merge sub-logic + infinite-loop guard (decision #10)
- [ ] **M4.9** (Question Q7) Implement `questions/coverage.py` — C1+C4 block, C2+C3 warn
- [ ] **M4.10** (Question Q8) Wire pipeline into `debate_pipeline.py` behind feature flag
- [ ] **M4.11** (Question Q9) Emit `QUESTION_COVERAGE_REPORT` artifact + audit events
- [ ] **M4.12** (Question Q10) Integration with eval harness — confirm metrics pickup
- [ ] **M4.13** Enable feature flag `use_question_pipeline_v2=true`
- [ ] **M4.14** Run eval compare: must show `required_decision_coverage ≥ 0.85`, `binary_yes_no_ratio ≤ 0.15`

**Branch:** `feat/phase1-v2-m4-question-pipeline`

---

## M5 — Debate Engine Upgrade (Weeks 7-9, partial overlap M4)

**Goal:** Dense agent prompts + robust moderator + diversity guardrails.

**Acceptance:** `d.parse_fail_rate = 0`, `d.round1_resolve_rate ≤ 0.5`, dense prompts loaded for all 12 agents.

- [ ] **M5.1** (Debate D1) Write agent .md format spec + 2 sample files: SecuritySpecialist, BackendArchitect (≥500 token each)
- [ ] **M5.2** (Debate D2) Implement `debate/agents/loader.py` + frontmatter parser
- [ ] **M5.3** (Debate D3) Implement `AgentRegistry` + `DomainSpec` + pair_suggestion algorithm
- [ ] **M5.4** (Debate D4) Implement diversity guardrails — same-domain rejection + echo detection with embedding cache (decision #3, #6)
- [ ] **M5.5** (Debate D5) Implement moderator JSON robustness — 4 ParseFailReason types + retry (max 2)
- [ ] **M5.6** (Debate D5) Add `MODERATOR_PARSE_FAILED` to status check constraint (decision #16)
- [ ] **M5.7** (Debate D6) Round prompt enrichment — inject brief digest + decision context
- [ ] **M5.8** (Debate D7) Confidence calibration — required_min_rounds=2, calibrated moderator prompt, diversity penalty
- [ ] **M5.9** (Debate D8) Write 10 remaining agent .md files (DevOps, Product, Database, QA, Frontend, Mobile, ML, DataEng, Legal, Finance) — manual review each
- [ ] **M5.10** (Debate D9) OPTIONAL visibility — `auto_resolution_reason` field, no silent skip
- [ ] **M5.11** Wire Anthropic prompt caching for dense prompts (decision #5)
- [ ] **M5.12** (Debate D10) Deprecation shim — `debate/agents.py` re-exports for 1 release
- [ ] **M5.13** Enable feature flag `use_debate_v2=true`
- [ ] **M5.14** Run eval compare: must show no PARSE_FAILED unaccounted, debate metrics within target range

**Branch:** `feat/phase1-v2-m5-debate-upgrade`

---

## M6 — Gate 1 Skill Redesign (Weeks 9-10)

**Goal:** Skill becomes thin launcher, Python backend handles state. Brief edit at gate.

**Acceptance:** `/review-debate` renders 4 sections, handles `MODERATOR_PARSE_FAILED` distinctly, allows brief field edit.

- [ ] **M6.1** (Gate G1) Implement `gate/gate1_review/loader.py` — load all artifacts for run
- [ ] **M6.2** (Gate G2) Implement `gate1_review/sections.py` — 4-way classification
- [ ] **M6.3** (Gate G3) Implement `gate1_review/renderer.py` — markdown blocks per section
- [ ] **M6.4** (Gate G4) Implement `gate1_review/parser.py` — 20+ regex patterns
- [ ] **M6.5** (Gate G5) Implement CLI: `ai-dev gate review-debate load|parse|finalize`
- [ ] **M6.6** (Gate G6) Rewrite `skills/review-debate.md` as launcher (~50 lines), legacy flag check (decision #35-Mig-1)
- [ ] **M6.7** (Gate G7) Implement `gate1_review/editor.py` — brief edit whitelist + impact detection (no re-trigger yet)
- [ ] **M6.8** (Gate G9) LLM-based NLU fallback for ambiguous input + session cache (decision #15)
- [ ] **M6.9** (Gate G10) Session state in `runs.gate1_session_state jsonb` + resume
- [ ] **M6.10** Enable feature flag `use_gate1_v2=true`
- [ ] **M6.11** Deferred: G8 (brief edit re-trigger) → separate slice in M9 cleanup phase

**Branch:** `feat/phase1-v2-m6-gate1-redesign`

---

## M7 — Spec Generation v2 (Weeks 10-12)

**Goal:** Parallel section generators + grounding checker + trace map.

**Acceptance:** Spec generated has trace map covering ≥80% assertions. Grounding violations ≤2 per spec after auto-repair.

- [ ] **M7.1** (Spec SP1) Implement `spec/planner.py` + 5 SectionRules constants
- [ ] **M7.2** (Spec SP2) Implement `spec/generators/proposal.py` + `functional.py`
- [ ] **M7.3** (Spec SP3) Implement `design.py`, `non_functional.py`, `acceptance_criteria.py`
- [ ] **M7.4** (Spec SP4) Implement ThreadPoolExecutor orchestration (5 sections parallel)
- [ ] **M7.5** (Spec SP5) Implement `spec/grounding.py` — G1-G4 rule checks
- [ ] **M7.6** (Spec SP6) Implement `spec/repair.py` — 1 retry per section, max 5 total
- [ ] **M7.7** (Spec SP7) Implement G5 LLM-based hallucination detection
- [ ] **M7.8** (Spec SP8) Implement `spec/tracer.py` — parse markers, build trace map artifact
- [ ] **M7.9** (Spec SP9) Update `finalize_spec()` wrapper — backward compat with legacy briefs
- [ ] **M7.10** (Spec SP10) Trace map link in Gate review UI (depends M6)
- [ ] **M7.11** Enable feature flag `use_spec_gen_v2=true`
- [ ] **M7.12** Run eval compare: spec hallucination metrics improve, trace coverage ≥80%

**Branch:** `feat/phase1-v2-m7-spec-gen-v2`

---

## M8 — CLI Polish (Weeks 6-12, parallel)

**Goal:** Full noun-verb CLI tree, help system, completion.

**Acceptance:** `ai-dev <noun> <verb> --help` works for all subcommands. Bash/Zsh/PowerShell completion functional.

- [ ] **M8.1** (CLI C4) Migrate `start` as legacy alias → `intake start` (already covered M2.11)
- [ ] **M8.2** (CLI C5) Migrate `run` → `phase-b run` with legacy alias
- [ ] **M8.3** (CLI C6) Implement `eval` + `golden` commands (already covered M1.7, M3.2)
- [ ] **M8.4** (CLI C7) Implement `ai-dev info <run-id>` + `gate` subcommands (already partial M6.5)
- [ ] **M8.5** (CLI C8) Implement help system with markdown rendering via `rich`
- [ ] **M8.6** (CLI C9) Implement shell completion generator (bash/zsh/powershell)
- [ ] **M8.7** (CLI C9) Lazy completers for `--run-id` (decision #7 file cache)
- [ ] **M8.8** (CLI C10) Implement `ai-dev migrate status` + `migrate classify`

**Branch:** `feat/phase1-v2-m8-cli-polish` (split if too long)

---

## M9 — Migration Rollout (Weeks 12+)

**Goal:** Stage rollout per Migration Plan, T+0 through T+12w.

**Acceptance:** All features default `true`, legacy code paths removed, v6 cleanup migration applied.

- [ ] **M9.1** Pre-flight: full eval compare master vs all-flags-on, document in `migration_baseline.md`
- [ ] **M9.2** T+0: Deploy code with all flags `false`. v5 SQL applied prod.
- [ ] **M9.3** T+3d: Enable `eval_harness_enabled=true`, capture baseline
- [ ] **M9.4** T+1w: Enable `use_intake_wizard=true`. Monitor 3+ days.
- [ ] **M9.5** T+2w: Enable `use_question_pipeline_v2=true` if eval delta acceptable
- [ ] **M9.6** T+3w: Enable `use_debate_v2=true`
- [ ] **M9.7** T+4w: Enable `use_gate1_v2=true`
- [ ] **M9.8** T+5w: Enable `use_spec_gen_v2=true`
- [ ] **M9.9** T+8w: Begin deprecation phase 1 — add `DeprecationWarning` to legacy code paths
- [ ] **M9.10** (Gate G8) Implement brief edit re-trigger logic (deferred from M6)
- [ ] **M9.11** T+12w: Remove legacy code, deploy v6 cleanup migration
- [ ] **M9.12** Final eval compare: full v2 vs pre-v2 baseline, document outcomes

**Branch:** `feat/phase1-v2-m9-rollout` (mostly ops, not code)

---

## Risk Mitigation Tracking

| Risk | Trigger | Action |
|---|---|---|
| Eval false-fail blocks rollout | Metric regresses >10% on golden but manual review says ok | Manual override flag in eval config |
| LLM cost spike | Per-run cost >$2 | Investigate caching gaps; consider model downgrade for non-critical calls |
| Intake abandonment | User quits wizard mid-way >50% of time | UX research, consider micro_v1 template |
| Brief v2 schema change after release | Required field discovered missing | Bump to brief_version=3, write migration script |
| Critic loop drops too many questions | `coverage_report.critic_drops > 0.5 * total` | Tighten flag thresholds, fallback to materializer-only |
| Migration v5 SQL fails on prod | Schema diverged from test | Roll back code, investigate, defer |

---

## Stop / Pause Points (Human-as-Approver gates)

Implementation pauses for explicit user approval at:

1. **End of M0** — confirm framework before parallel work
2. **End of M1** — review baseline metrics, agree they reflect reality
3. **End of M2** — try wizard on real idea, confirm UX acceptable
4. **End of M5** — read 1 real debate transcript, confirm agent prompts produce quality
5. **End of M7** — read 1 generated spec, confirm trace map useful
6. **Before M9.4 onward** — each flag enable in production requires approval

Between approval points, AI proceeds autonomously through slices.

---

## What This Plan Does NOT Cover

- Phase 2 (task graph) changes
- Phase 3 (execution runner) changes
- Phase 4 (verification) changes
- Multi-tenant / multi-user features
- Cost monitoring / observability dashboard
- Customer-facing documentation update (do at M9.11)

These belong to separate plans, not this one.

---

## Next Concrete Step

After user approves this roadmap:

**Begin M0.1:** Add `typer>=0.12` and `rich>=13` to `pyproject.toml`. PR title: `chore: add typer + rich for CLI unification (M0.1)`.
