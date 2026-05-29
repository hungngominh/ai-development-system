# Changelog

Định dạng dựa trên [Keep a Changelog](https://keepachangelog.com/vi/1.1.0/).

## [Unreleased]

### Phase 1 v2 — M2 Intake Wizard S4 (followup + consistency rules, 2026-05-23)
- `intake/consistency_rules.py` — 10 cross-field consistency rules as a pure-function registry (no I/O, no LLM). Rules: `avail_vs_budget`, `scope_vs_deadline`, `residency_vs_deploy`, `team_vs_stack`, `greenfield_vs_existing_auth`, `brownfield_vs_data_sources`, `user_count_year1_lt_now`, `rps_vs_users`, `accessibility_vs_user_facing`, `latency_vs_availability`. Each rule returns optional `ConsistencyHit` with `target_field_id=None` for warning-only or a field id for "fix this" gaps. Robust parse helpers for deadlines (weeks/days/months), availability %, and budget USD/VND.
- `intake/followup.py` — `Gap` dataclass (serializable via `to_dict/from_dict` for run state persistence) + `detect_gaps(state, template, llm=None, ambiguity_threshold=0.5)`. Detects in priority order: (1) critical_blank for skipped or never-answered critical fields, (2) inconsistency/scope_mismatch from `consistency_rules.check_all()`, (3) ambiguity for short or LLM-rated low-clarity text_long answers (stub-mode: no LLM = no ambiguity gaps).
- `intake/engine.py` extended with `FOLLOWUP` stage:
  - `IntakeState.pending_gaps`, `followup_idx`, `suggesting_return_stage` — fully roundtrip via to/from_json.
  - `enter_followup(state, gaps)` — no-op on empty gap list; sets stage and resets idx.
  - Field-targeting gap: answer / skip / `?` (routes through SUGGESTING with `suggesting_return_stage=FOLLOWUP`) / `back`.
  - Warning gap: `continue` to advance, `edit <field>` to jump to ASKING for that field, `enough` to skip all.
  - `enough` escape hatch records remaining gap target fields as `source=skipped` and appends `followup_assumed` audit entries.
  - `save` / `show` work the same as ASKING; `back` from first gap errors `cannot_back_from_first`.
- `intake/runner.py` — `_maybe_intercept_for_followup()` runs gap detection when stage transitions to CONFIRM with empty `pending_gaps`. Emits `INTAKE_FIELD_SUGGESTED` event with `stage=FOLLOWUP` and gap_count on entry.
- Bug fix: `?` invoked from FOLLOWUP now sets `state.field_idx = template.field_index(target)` before SUGGESTING — previously rendered the wrong field's prompt.
- Bug fix: `parse_budget_usd` regex now uses optional suffix capture so `"1k USD"` → 1000.0 and `"5tr VND"` → ~200 USD (was returning the raw number because `\b(k)\b` doesn't match between digit and letter).
- 34 new unit tests (`test_consistency_rules.py`) for parse helpers, every rule firing/not-firing, registry robustness on weird input types.
- 12 new unit tests (`test_followup.py`) for Gap roundtrip, critical_blank detection, consistency → gap mapping, ambiguity LLM scoring (with/without/error), ordering.
- 20 new unit tests (`test_engine_followup.py`) for `enter_followup`, field/warning gap rendering, all FOLLOWUP commands (text/skip/?/back/continue/edit/enough/save/show), `?` routing through SUGGESTING and back, `enough` records remaining as assumptions, serialization roundtrip with pending_gaps.
- 5 new integration tests (`test_intake_followup.py`) — full intake → gap → FOLLOWUP → brief: critical-blank answered recovers the field, `enough` marks remaining as assumptions in brief, `?` from FOLLOWUP through SUGGESTING confirms with `ai_suggested_confirmed` source, `save` during FOLLOWUP persists pending_gaps for resume, `INTAKE_FIELD_SUGGESTED` event with stage=FOLLOWUP is emitted.
- Total suite: 538 passed, 9 xfailed.
- **Deferred to M2 S5+**: `intake resume` CLI verb, skill `/start-project` rewrite, `finalize_spec` brief-v2 wiring, brief digest (M2.10), eval compare (M2.16).

### Phase 1 v2 — M2 Intake Wizard S3 (suggest module, 2026-05-23)
- `intake/suggest_deps.py` — hardcoded dependency map: per target field, the 3-5 already-answered fields most relevant to inject as context (cheaper tokens, sharper proposals). Falls back to `[problem_statement, primary_user, scope_in, deployment_target]` when target not mapped.
- `intake/suggest.py` — `Suggester` class. Session-scoped in-memory cache keyed by `(field_id, sha256-of-answers)` (locked decision #1). Strict JSON parsing with markdown-fence tolerance + type coercion (`list_str` accepts `"a, b"` strings, `enum` validates against template options, `number` coerces from string).
- `intake/engine.py` extended:
  - new `SUGGESTING` stage (serializable via `state.pending_suggestion`)
  - `step()` accepts optional `suggest_fn` callback so the engine stays pure-ish (LLM call is injected)
  - `?` / `không biết` / `idk` triggers SUGGESTING when `ai_can_suggest=true`, else shows refuse message
  - `a / ok` accepts proposal (source = `ai_suggested_confirmed`), `b <text>` overrides (source = `user`), `c / skip` declines, `?` regenerates, `back` cancels
  - LLM exception caught and surfaced as `suggest_failed` error without crashing wizard
- `intake/runner.py` — accepts optional `llm` arg, builds a per-run `Suggester` (cache scoped to one wizard run), wires it as `suggest_fn`, emits `INTAKE_FIELD_SUGGESTED` event when LLM is called.
- `cli/commands/intake.py` — `intake start` auto-loads `RealLLMClient` from env; `--no-llm` flag disables suggest for offline runs.
- 26 new unit tests (`test_suggest.py`) covering refuse list, JSON parsing edge cases, cache hit/invalidate, prompt content, all engine transitions (a/b/c/back/regenerate/null-suggestion/exception-handling).
- 5 new integration tests (`test_intake_suggest.py`) exercising the full ?-flow → INTAKE_BRIEF artifact end-to-end with a stub LLM.
- Total suite: 467 passed, 9 xfailed.
- **Deferred to M2 S4+**: followup module (4 gap detection logics), consistency rules, brief digest, `intake resume` CLI verb, skill `/start-project` rewrite, `finalize_spec` brief-v2 wiring.

### Phase 1 v2 — M2 Intake Wizard (slices S1+S2, 2026-05-23)
- `src/ai_dev_system/intake/templates/generic_v1.yaml` — 34-field template, 8 critical (`problem_statement`, `scope_in`, `scope_out`, `success_metric`, `primary_user`, `deployment_target`, `compliance`, `current_workaround`).
- `intake/template.py` — frozen dataclass loader with schema validation + sha256 schema hash.
- `intake/engine.py` — pure state machine `start()/step()` over states `ASKING → CONFIRM → DONE/PAUSED`. Commands: `skip / back / save / show / edit <field> / confirm`. Brief v2 serializer.
- `intake/repo.py` — checkpoint to `runs.intake_state` JSON column after every transition.
- `intake/brief.py` — atomic promotion to `INTAKE_BRIEF` artifact + `runs.intake_brief_id` set + `runs.status = READY_FOR_DEBATE`.
- `intake/runner.py` — orchestrator wiring engine + DB + I/O via injectable `prompt_fn`.
- `cli/commands/intake.py` — `ai-dev intake start --project-name … [--json]` and `ai-dev intake show --run-id`.
- 26 unit tests (template + engine state transitions + serialization roundtrip) + 4 integration tests (full intake → brief, save → pause, skip critical → assumption, INTAKE_STARTED/COMPLETED events).
- Total suite: 436 passed, 9 xfailed.
- **Deferred to M2 S3+**: suggest module (`?` → LLM), followup (4 gap detection logics), consistency rules, brief digest, resume CLI verb, skill `/start-project` rewrite, `finalize_spec` brief-v2 wiring, eval compare.

### Phase 1 v2 — M0.5 SQLite migration (2026-05-23)
- **Full migration from PostgreSQL to SQLite** — zero-install, mọi máy chạy được không cần Postgres server.
- `src/ai_dev_system/db/connection.py` mới: `get_connection()` dùng `sqlite3` stdlib (WAL, FK enforced, Row factory).
- `src/ai_dev_system/db/helpers.py` mới: `load_json`, `dump_json`, `new_uuid`, `load_array`, `parse_iso`, `to_db_bool`.
- `src/ai_dev_system/db/migrator.py` mới: `apply_schema()` đọc các file SQL theo thứ tự + V5 ALTER TABLE.
- `docs/schema/control-layer-schema.sql` viết lại: TEXT+CHECK thay PG ENUM, JSON TEXT thay JSONB, UUID app-side, `?` thay `%s`.
- Tất cả 6 repos (`runs`, `task_runs`, `artifacts`, `events`, `escalations`, `version_locks`) viết lại theo style SQLite.
- Engine modules (`runner`, `worker`, `loop`, `background`, `materializer`, `resolver`, `failure`, `escalation`, `heartbeat`) bỏ `FOR UPDATE`, `RETURNING`, `ANY(array)`, `COUNT FILTER`, `jsonb_set`, `gen_random_uuid()` — dùng Python iteration + `datetime('now', '-N seconds')`.
- `Config.from_env()` mặc định SQLite (`sqlite:///~/.ai-dev-system/control.db`) — không còn yêu cầu env vars.
- `setup_wizard` chuyển sang gọi `apply_schema()` thay vì connect PG.
- `pyproject.toml`: bỏ `psycopg[binary]>=3.1`. Đăng ký `integration` pytest marker.
- 406 tests pass, 9 xfail (runner threading + subprocess CLI cần file-backed DB — defer cho M2).

### Phase 1 v2 — Foundation (M0, 2026-05-23)
- 9 design specs trong `docs/superpowers/specs/2026-05-23-*.md` (intake, eval, question gen, debate, spec gen, gate 1, migration, CLI, locked decisions)
- Master implementation plan `docs/superpowers/plans/2026-05-23-phase1-v2-implementation.md`
- Dependencies: `typer>=0.12`, `rich>=13`, `pyyaml>=6.0`
- Schema migration `v5-phase1-v2.sql` + rollback: TEXT+CHECK pattern thay PG enum, columns mới cho intake/gate1/legacy
- `src/ai_dev_system/feature_flags.py` — 6-flag linear order enforcement (decision #18)
- `src/ai_dev_system/cli/core/` — typer-based CLI framework (output, context, registry)
- `src/ai_dev_system/cli/commands/` — auto-registered command modules; `setup` migrated, `start`/`run` thành legacy alias
- 30+ unit tests cho FeatureFlags

### Thêm mới (cũ)
- Khởi tạo dự án research hub
- Design spec và implementation plan
- README tổng quan với bản đồ 5 thành phần và quick start
- 3 sơ đồ Mermaid: system overview, data flow, memory layers
- 4 tài liệu docs: architecture, integration guide, memory analysis, workflow
- 5 thẻ tham chiếu cho CrewAI, agency-agents, Beads, OpenSpec, Superpowers
- Research notes: phân tích ban đầu, convention, template thí nghiệm
- 3 code examples: basic crew, spec-driven crew, full pipeline
