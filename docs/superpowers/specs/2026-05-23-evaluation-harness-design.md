# Design Spec: Phase 1 Evaluation Harness

**Date:** 2026-05-23
**Status:** Draft
**Scope:** Measurable quality framework for intake brief + question generation. Pre-requisite for any "improve Phase 1" PR.

---

## Motivation

Phase 1 hiện tại (và sau khi có Intake Wizard) đều chạy 100% bằng cảm tính:

- Không biết câu hỏi sinh ra **nông hay sâu** mức nào
- Không biết brief intake **đầy đủ hay thiếu** mảng nào với từng loại idea
- Không biết PR sửa prompt/logic có thực sự **cải thiện** hay làm tệ hơn
- Không có baseline để so sánh giữa các template version (`generic_v1` vs `saas_v1` tương lai)

Spec [Intake Wizard](2026-05-23-intake-wizard-design.md) liệt kê 7 failure mode (F1-F7) của question generation. Cách duy nhất biết fix có đập được F1-F7 không là **đo trên dataset cố định**.

Harness này phải build **trước** khi merge bất kỳ thay đổi nào về intake/questions/debate. Không có nó, mọi cải tiến là đoán.

---

## Goals

- Golden dataset 5-10 idea đại diện cho các loại project khác nhau
- 6 metric đo được tự động, không cần human đánh giá mỗi lần chạy
- Chạy được qua CLI 1 lệnh: `ai-dev eval run`
- So sánh được 2 run (before/after) → output diff table
- Tách 3 layer eval: intake brief, question generation, debate quality. Layer nào dùng riêng được layer đó
- Stub-mode mặc định (không tốn API credit). Real-LLM mode opt-in cho release gate

## Non-goals

- **Không** thay LLM judge cho spec quality (làm sau)
- **Không** đo execution runner / task graph (out of Phase 1 scope)
- **Không** auto-tune prompt (chỉ measure, không optimize)
- **Không** UI dashboard — output là file + console table

---

## Architecture

```
src/ai_dev_system/eval/
├── golden/                          # Golden dataset (versioned)
│   ├── ideas/
│   │   ├── 01_internal_forum.yaml
│   │   ├── 02_data_pipeline.yaml
│   │   ├── 03_mobile_b2c_app.yaml
│   │   ├── 04_ml_inference_service.yaml
│   │   ├── 05_cli_devtool.yaml
│   │   ├── 06_saas_b2b.yaml
│   │   ├── 07_legacy_migration.yaml
│   │   └── 08_security_audit_tool.yaml
│   └── expected/                    # Per-idea expected output
│       ├── 01_internal_forum/
│       │   ├── brief_expected.yaml         # ground-truth brief
│       │   ├── decisions_required.yaml     # decisions phải có trong question set
│       │   ├── decisions_forbidden.yaml    # decisions KHÔNG được hỏi (đã có default)
│       │   └── notes.md                    # rationale (cho human review)
│       └── ...
├── runners/
│   ├── intake_runner.py             # scripted intake (auto-answer từ expected brief)
│   ├── questions_runner.py          # gọi generate_questions, log output
│   └── debate_runner.py             # gọi run_debate, log output
├── metrics/
│   ├── brief_metrics.py             # 6 metric cho intake
│   ├── question_metrics.py          # 8 metric cho questions
│   └── debate_metrics.py            # 4 metric cho debate
├── report.py                        # render console + markdown + json
├── compare.py                       # diff 2 run
└── cli.py                           # `ai-dev eval ...`
```

---

## Golden Dataset

### Idea selection criteria

8 idea phủ các trục:

| Idea | Scope type | Complexity | Đặc trưng test |
|---|---|---|---|
| 01_internal_forum | product | medium | Auth/SSO, search, leaderboard — multi-domain |
| 02_data_pipeline | feature | medium | Data residency, batch vs streaming, schema |
| 03_mobile_b2c_app | product | high | Offline, push notification, app store |
| 04_ml_inference_service | feature | medium | GPU cost, latency p99, model versioning |
| 05_cli_devtool | product | low | Distribution, plugin model, no auth |
| 06_saas_b2b | product | high | Multi-tenant, billing, SLA |
| 07_legacy_migration | experiment | high | Backward compat, dual-write, rollback |
| 08_security_audit_tool | feature | medium | Compliance-heavy, audit trail, RBAC |

Mỗi idea có `raw_idea` (1-3 câu user thật sự gõ) + `intake_script` (kịch bản trả lời wizard) + `expected_brief` + `expected_decisions`.

### Idea file format (`golden/ideas/01_internal_forum.yaml`)

```yaml
id: "01_internal_forum"
raw_idea: |
  Xây forum chia sẻ kiến thức nội bộ cho công ty.
  Có leaderboard contributor.

# Auto-answer cho intake wizard runner (scripted, không cần human)
intake_script:
  problem_statement: "Nhân viên không tìm được kiến thức cũ, lặp lại câu hỏi trên Slack"
  who_feels_pain: "~500 nhân viên VN, ít tech savvy"
  current_workaround: "Slack search + Notion phân mảnh"
  cost_of_doing_nothing: "~2h/tuần/người tìm lại thông tin"
  scope_in: ["posts", "comments", "voting", "leaderboard", "search"]
  scope_out: ["DM", "file upload >10MB", "mobile native app"]
  success_metric: "WAU >= 200 sau 1 tháng, search NPS >= 7"
  done_definition: "Deploy prod + 50 user thật dùng 1 tuần không bug critical"
  deadline: "2026-08-01"
  primary_user: "Software engineer + PM, tech savvy"
  user_count_now: 500
  user_count_year1: 800
  user_languages: ["vi", "en"]
  accessibility: "không yêu cầu đặc biệt"
  must_use_stack: ["PostgreSQL"]
  must_not_use: ["MongoDB"]
  compliance: "none"
  data_residency: "VN"
  budget_infra: "$200/month"
  team_skills: ["Python", "React", "PostgreSQL"]
  greenfield_or_brownfield: "greenfield"
  existing_auth: "Azure AD SSO"
  data_sources: []
  must_integrate_with: ["Azure AD"]
  deployment_target: "?"   # ← cố tình skip → test AI suggest
  nfr_priority: ["Time-to-market", "Maintainability", "Performance"]
  expected_rps: 50
  expected_data_volume: "10GB"
  availability_target: "99%"
  latency_target: "300ms p95"
  known_unknowns: ["search engine selection", "moderation policy"]
  failed_attempts: []
  inspiration_refs: ["Discourse", "StackOverflow"]
  political_constraints: ""
```

### Expected decisions format

```yaml
# expected/01_internal_forum/decisions_required.yaml
# Mỗi entry = 1 decision generator PHẢI hỏi (bằng cách nào đó)
required_decisions:
  - id: "search_engine_choice"
    why: "scope_in có 'search', known_unknowns có 'search engine selection'"
    accept_question_patterns:
      - regex: "(?i)search.*(engine|backend|elastic|postgres.*fts|meili|typesense)"
      - regex: "(?i)(elastic|meili|typesense|opensearch|postgres.*full.?text)"
    domain_expected: ["backend", "database"]

  - id: "moderation_policy"
    why: "known_unknowns mention moderation"
    accept_question_patterns:
      - regex: "(?i)moderat(ion|or)"
      - regex: "(?i)(spam|abuse|report).*polic"
    domain_expected: ["product", "qa"]

  - id: "voting_anti_abuse"
    why: "scope_in có voting, không có context anti-abuse"
    accept_question_patterns:
      - regex: "(?i)vot.*(sock.?puppet|abuse|rate.?limit|fraud)"
    domain_expected: ["security", "backend"]

  - id: "leaderboard_refresh_strategy"
    why: "scope_in có leaderboard — cần biết realtime vs batch"
    accept_question_patterns:
      - regex: "(?i)leaderboard.*(realtime|batch|cron|refresh)"
    domain_expected: ["backend"]

  - id: "search_indexing_strategy"
    why: "khi nào index? sync vs async vs cron?"
    accept_question_patterns:
      - regex: "(?i)index.*(sync|async|cron|realtime)"
    domain_expected: ["backend", "database"]

  # ... ~8-12 decisions per idea
```

```yaml
# expected/01_internal_forum/decisions_forbidden.yaml
# Generator KHÔNG NÊN hỏi (đã có default an toàn / đã trả lời trong brief)
forbidden_decisions:
  - id: "database_choice"
    why: "brief đã ghi must_use_stack=PostgreSQL"
    reject_question_patterns:
      - regex: "(?i)(which|what).*database"
      - regex: "(?i)postgres.*(or|vs).*mongo"

  - id: "authentication_method"
    why: "brief đã ghi existing_auth=Azure AD"
    reject_question_patterns:
      - regex: "(?i)(jwt|session|oauth).*(or|vs|choose)"

  - id: "deploy_cloud"
    why: "có thể suggest từ data_residency=VN, không cần debate"
    reject_question_patterns:
      - regex: "(?i)(aws|gcp|azure).*(or|vs|choose)"
```

### Authoring rule cho expected files

- Mỗi `required_decision` phải có ≥1 regex pattern match được câu hỏi "đúng đáp ứng"
- Mỗi `forbidden_decision` phải có ≥1 regex match câu hỏi "đã thừa"
- Pattern viết bằng tiếng Việt OR tiếng Anh (generator có thể dùng cả 2)
- `notes.md` ghi tại sao chọn decision này — để 6 tháng sau review không quên context

---

## Metrics

### Layer 1: Brief Quality (intake wizard output)

| Metric | Định nghĩa | Threshold pass |
|---|---|---|
| `brief.critical_fill_rate` | (8 critical fields có value non-null) / 8 | ≥ 0.875 (7/8) |
| `brief.ai_suggest_acceptance` | (AI suggestions user confirm) / (AI suggestions offered) | ≥ 0.6 |
| `brief.assumption_count` | Số field skipped không có suggest confirm | ≤ 5 |
| `brief.consistency_violations` | Số rule trong `consistency_rules.py` fire | = 0 |
| `brief.field_coverage_per_section` | Min fill rate trên 7 section | ≥ 0.5 |
| `brief.followup_question_count` | Số câu followup Stage 2 tốn | ≤ 10 |

### Layer 2: Question Quality (generate_questions output)

| Metric | Định nghĩa | Threshold pass |
|---|---|---|
| `q.required_decision_coverage` | (required_decisions có ≥1 question match) / total required | ≥ 0.85 |
| `q.forbidden_decision_rate` | (forbidden_decisions có question match) / total forbidden | = 0 |
| `q.binary_yes_no_ratio` | Số câu LLM-rated "binary yes/no không context" / total | ≤ 0.15 |
| `q.duplicate_pair_count` | Số cặp câu cosine similarity ≥ 0.85 trong embedding | = 0 |
| `q.domain_balance_entropy` | Shannon entropy phân bố domain (cao = đều) | ≥ 1.5 |
| `q.avg_question_length` | Trung bình ký tự / câu (proxy specificity) | 60-300 |
| `q.classification_distribution` | % REQUIRED / STRATEGIC / OPTIONAL | REQUIRED ≥ 0.4 |
| `q.scope_drift_count` | Câu LLM-rated "không liên quan brief.scope_in" | = 0 |

### Layer 3: Debate Quality (run_debate output)

| Metric | Định nghĩa | Threshold pass |
|---|---|---|
| `d.escalate_rate` | Câu kết thúc ESCALATE_TO_HUMAN / total | 0.1 - 0.4 |
| `d.json_parse_fail_rate` | Câu moderator trả non-JSON / total | = 0 |
| `d.round1_resolve_rate` | Câu resolved vòng 1 với confidence ≥ 0.8 / total | ≤ 0.5 |
| `d.avg_rounds_to_resolve` | Trung bình rounds tới khi resolved | 1.5 - 3.5 |

**Lý do range thay vì >threshold:**
- `escalate_rate` quá thấp = AI tự quyết mọi thứ (đáng ngờ). Quá cao = AI không làm gì cho user
- `round1_resolve_rate` quá cao = câu hỏi nông, agent đồng ý ngay (failure mode F1)
- `avg_rounds` quá thấp = không thực sự debate. Quá cao = câu hỏi không có đáp án rõ

### Metrics cần LLM (judge calls)

- `q.binary_yes_no_ratio`: 1 LLM call rate batch tất câu hỏi
- `q.scope_drift_count`: 1 LLM call so từng câu với scope_in
- `brief.field_coverage_per_section`: rule-based (đếm value non-null)

Tất cả các metric khác là **rule-based** (regex, count, entropy) — chạy offline không cần LLM.

→ Trong stub-mode, các metric cần LLM trả về `0.5` (neutral) hoặc skip. Real-mode chạy đầy đủ.

---

## CLI Surface

```
ai-dev eval run [options]
  --idea ID                    chỉ chạy 1 idea (default: all)
  --layer brief|questions|debate|all     (default: all)
  --mode stub|real             (default: stub)
  --tag NAME                   tag cho run (vd: "before-fix-F1"), default = git short SHA
  --output-dir PATH            (default: .eval_runs/<tag>/)

ai-dev eval compare TAG_A TAG_B
  → render diff table console + write markdown report

ai-dev eval list
  → list tất cả run đã có trong .eval_runs/

ai-dev eval show TAG
  → render full report của 1 run
```

### Run output structure

```
.eval_runs/<tag>/
├── meta.yaml                   # git_sha, model, timestamp, mode
├── per_idea/
│   ├── 01_internal_forum/
│   │   ├── brief.json          # intake_runner output
│   │   ├── questions.json      # generator output
│   │   ├── debate.json         # debate output (nếu layer=debate|all)
│   │   ├── metrics.json        # tất cả metric
│   │   └── flags.md            # human-readable failure notes
│   └── ...
├── aggregate.json              # avg metrics across all ideas
└── report.md                   # markdown summary
```

### Report format (console)

```
Eval Run: before-fix-F1  (claude-4-7-sonnet, stub)  2026-05-23T10:00:00

┌──────────────────────────────┬─────────┬──────────┬──────────┐
│ Metric                       │  Value  │ Pass     │ vs prev  │
├──────────────────────────────┼─────────┼──────────┼──────────┤
│ Brief                        │         │          │          │
│   critical_fill_rate         │  0.875  │   ✓     │   +0.0   │
│   assumption_count           │  3.2    │   ✓     │   -1.1   │
│ Questions                    │         │          │          │
│   required_decision_coverage │  0.62   │   ✗     │   +0.18  │
│   forbidden_decision_rate    │  0.08   │   ✗     │   -0.02  │
│   binary_yes_no_ratio        │  0.31   │   ✗     │   -0.05  │
│   domain_balance_entropy     │  1.21   │   ✗     │   +0.31  │
│ Debate                       │         │          │          │
│   escalate_rate              │  0.45   │   ⚠     │   +0.10  │
│   round1_resolve_rate        │  0.62   │   ✗     │   -0.05  │
└──────────────────────────────┴─────────┴──────────┴──────────┘

Failure flags by idea (top 3):
  01_internal_forum: missing "search_engine_choice", binary "Should auth use OAuth?"
  03_mobile_b2c_app: missing "offline_strategy", scope_drift Q4
  06_saas_b2b: forbidden "database_choice" appeared, duplicate Q2/Q9
```

---

## Comparison Workflow

```
# baseline
git checkout master
ai-dev eval run --tag baseline --mode stub

# thử nghiệm
git checkout fix-F1-critic-loop
ai-dev eval run --tag fix-F1 --mode stub

# so sánh
ai-dev eval compare baseline fix-F1
```

`compare` render diff per-metric per-idea + flag regression (metric tụt > threshold).

**Release gate (manual, không auto-CI):**
- PR sửa Phase 1 phải attach output `ai-dev eval compare master <branch>` trong description
- Reviewer xem có regression nào không
- Mode `real` chạy trước merge (tốn credit nhưng cần thiết)

---

## Authoring Tooling

Vì golden dataset là tay viết → cần tool hỗ trợ:

```
ai-dev eval golden init <idea_id>
  → tạo skeleton 4 file (idea, brief_expected, decisions_required, decisions_forbidden, notes)

ai-dev eval golden validate <idea_id>
  → kiểm tra:
    • intake_script đầy đủ field theo template generic_v1
    • mỗi required_decision có ≥1 regex
    • regex compile được
    • forbidden_decision không overlap required

ai-dev eval golden dryrun <idea_id> --mode real
  → chạy 1 idea với real LLM, render output để human review pattern match
```

---

## Stub LLM Behavior cho Eval

Stub hiện tại trả response cố định. Cho eval, cần stub **đa dạng có structure**:

- `stub.generate_questions`: trả 8-12 question template hợp lý theo idea_id (hardcoded mapping)
- `stub.debate`: trả pattern cố định (60% resolved, 25% escalate, 15% need_more_evidence)
- `stub.suggest`: trả "stub_suggestion_for_<field>"

→ Stub-mode metrics ổn định, dùng để test harness chính nó. Real-mode mới đo chất lượng thật.

`tests/unit/test_eval_metrics.py`: feed brief/questions/debate cố định, assert metric value.

---

## Build Order

| Slice | Đầu ra | Test |
|---|---|---|
| **E1** | 2 idea golden (01_forum + 05_cli) + format spec | manual review schemas |
| **E2** | `brief_metrics.py` (6 metric) + unit tests với fixtures | unit pass |
| **E3** | `question_metrics.py` rule-based portion (5/8 metric) + unit tests | unit pass |
| **E4** | `intake_runner.py` (scripted answer from intake_script) | e2e: idea_01 → brief.json |
| **E5** | `questions_runner.py` + `report.py` console output | e2e: idea_01 → metrics.json + console table |
| **E6** | `cli.py` (`run`, `show`) + tag/output structure | manual: `ai-dev eval run --idea 01 --mode stub` |
| **E7** | `compare.py` + diff render | manual: tạo 2 fake run, compare |
| **E8** | 6 idea còn lại + `golden validate` tool | golden suite đầy đủ |
| **E9** | LLM-based metrics (3 còn lại) + real-mode | smoke test real-mode 1 idea |
| **E10** | `debate_runner.py` + 4 debate metric | e2e: idea_01 layer=debate |

Có thể merge từng slice. **E1-E6 đủ để dùng** — phần còn lại expand coverage.

---

## Integration với Intake Wizard Spec

| Khi nào dùng harness | Khi nào không cần |
|---|---|
| Trước merge PR sửa `generate_questions` | PR sửa CLI cosmetics |
| Trước merge PR sửa `intake/engine.py` | PR sửa migration script |
| Trước merge PR sửa prompt agent debate | PR sửa storage backend |
| Khi upgrade LLM model version | PR sửa Beads sync |
| Khi thêm template mới (`saas_v1`) | PR sửa DB schema không ảnh hưởng logic |

**Critical:** Intake Wizard Spec build order S1→S8 nên xen kẽ E1-E6:
- Sau S2 (CLI intake start) → chạy E4 (`intake_runner`) để verify wizard không drift
- Sau S7 (Phase B integration) → chạy E5-E7 full để verify Phase 1 end-to-end không regression

---

## Open Questions

1. **Idea count:** 8 đủ đại diện chưa? Lý thuyết cần phủ scope×complexity = 12 ô. Defer thêm idea tới khi thấy gap thật.

2. **Pattern regex maintenance:** khi LLM đổi từ tiếng Việt sang tiếng Anh hoặc ngược lại, pattern fail giả. Giải pháp: pattern viết cả 2 ngôn ngữ, OR dùng LLM judge thay regex cho `required_decision_coverage` (đắt hơn).

3. **Embedding cost:** `duplicate_pair_count` cần embedding all-pairs. Với 10 câu = 45 cặp. Có thể cache embedding cho golden output để rerun nhanh.

4. **Eval drift:** golden dataset có thể outdated khi LLM model upgrade. Cần ai chạy `golden dryrun` mỗi 3 tháng để refresh regex.

5. **CI integration:** có nên block merge nếu metric regression? Đề xuất **không**, chỉ render comparison trong PR description. Lý do: LLM non-deterministic, false-fail tỷ lệ cao sẽ làm dev bypass.

---

## Out of Scope

- Spec quality metrics (so spec với brief) — spec evaluation harness riêng sau
- Execution success metrics (task pass/fail rate) — Phase 3 eval
- Cost tracking (token, latency) — observability harness riêng
- A/B test framework (run 2 prompt variants on production) — premature
- Human eval workflow (Mechanical Turk-style scoring) — chỉ dùng nếu LLM judge không đủ tin
