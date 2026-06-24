# Design Spec: Question Generation Redesign (Phase 1b)

**Date:** 2026-05-23
**Status:** Draft
**Scope:** Replace single-LLM-call `generate_questions()` with 4-stage pipeline: Decision Inventory → Question Materialization → Critic Loop → Coverage Validator.

---

## Motivation

Phân tích Phase 1 chỉ ra 7 failure mode của question generation. [Intake Wizard](2026-05-23-intake-wizard-design.md) đập được F2/F3/F5 (vì brief có context). Spec này đập F1/F4/F7 (cần kiến trúc mới, không chỉ prompt tốt hơn) và double-down F2/F5.

**Failure mode cần đập:**
- **F1:** Câu binary yes/no không context → moderator resolved 0.95 vòng 1 (giả thắng)
- **F2:** Câu quá rộng, không actionable
- **F3:** Câu lệch khỏi idea
- **F4:** Câu trùng nhau ngôn ngữ khác
- **F5:** Bỏ sót domain quan trọng
- **F6:** Phân loại classification sai (REQUIRED → OPTIONAL → silent skip) — đã đẩy sang Debate spec [B]
- **F7:** Domain enum cứng 5 giá trị

**Root cause chung:** 1 LLM call, không tự kiểm tra, không có baseline. Cần kiến trúc multi-pass với critic.

---

## Goals

- 4-stage pipeline có separation of concerns
- Bắt được F1/F4 bằng critic, không bằng prompt cầu nguyện
- Coverage check rule-based + LLM, không phụ thuộc 1 prompt
- Domain enum mở rộng + extensible
- Output backward compatible với debate engine (vẫn là `list[Question]`)
- Đo được qua [Evaluation Harness](2026-05-23-evaluation-harness-design.md)

## Non-goals

- **Không** thay đổi debate engine (sẽ làm ở spec B)
- **Không** thay đổi `Question` data class signature
- **Không** auto-tune prompts (chỉ đo được, không optimize)
- **Không** multi-language question generation (defer)

---

## Architecture

```
brief_v2  ──→  [1. Decision Inventory]  ──→  decisions[]
                                              │
                                              ▼
              [2. Question Materializer]  ──→  questions_draft[]
                                              │
                                              ▼
              [3. Critic Loop (≤2 iter)]  ──→  questions_refined[]
                                              │
                                              ▼
              [4. Coverage Validator]    ──→  questions_final[] + coverage_report
                                              │
                                              ▼
                                          (debate)
```

**Phân chia file:**

```
src/ai_dev_system/debate/questions/
├── __init__.py
├── pipeline.py             # orchestrate 4 stage
├── inventory.py            # Stage 1
├── materializer.py         # Stage 2
├── critic.py               # Stage 3
├── coverage.py             # Stage 4
├── domains.py              # extensible domain registry
└── prompts/                # all prompts in separate files for diff-ability
    ├── inventory.txt
    ├── materializer.txt
    ├── critic.txt
    └── coverage_judge.txt
```

Cũ: `src/ai_dev_system/debate/questions.py` (single file) → deprecate, giữ stub để import compat trong 1 release cycle.

---

## Stage 1: Decision Inventory

### Input
- `brief_v2` (full structured brief từ Intake Wizard)

### Output
```python
@dataclass
class Decision:
    id: str                      # snake_case, vd "search_engine_choice"
    description: str             # 1 câu mô tả quyết định cần ra
    reason_required: str         # vì sao quyết định này quan trọng cho brief này
    has_safe_default: bool       # có default an toàn không (→ OPTIONAL)
    default_if_any: str | None   # nếu có default
    domain_hints: list[str]      # domain liên quan (≥1)
    blocks_what: list[str]       # tên feature/scope_in items sẽ bị block nếu không quyết
```

### Prompt template (`prompts/inventory.txt`)

```
Bạn là solution architect. Cho brief dự án có cấu trúc, liệt kê TẤT CẢ quyết định
kỹ thuật/sản phẩm cần ra để xây được cái này.

Brief:
{brief_json}

Quy tắc:
1. Quyết định = câu trả lời cho "chọn cái gì / như thế nào", KHÔNG phải "có làm không"
2. Mỗi quyết định phải ảnh hưởng đến ≥1 item trong scope_in
3. Nếu brief đã trả lời (must_use_stack, existing_auth, deployment_target có value)
   → KHÔNG đưa vào inventory (decision đã chốt)
4. Phân loại has_safe_default = true nếu có industry-standard default an toàn cho
   scope của brief (vd: REST API cho B2B SaaS có default; auth mechanism KHÔNG có default)
5. Tối thiểu 8 decisions, tối đa 25

Trả về JSON array:
[
  {
    "id": "snake_case_id",
    "description": "...",
    "reason_required": "...",
    "has_safe_default": bool,
    "default_if_any": "..." | null,
    "domain_hints": ["backend", "security", ...],
    "blocks_what": ["search feature", "leaderboard", ...]
  }
]
```

### Validation
- ≥8, ≤25 decisions
- Mỗi `id` unique
- Mỗi `domain_hints` ≥1 element thuộc Domain Registry (Stage 4)
- Mỗi `blocks_what` reference value trong `brief.fields.scope_in`

Nếu fail validation, retry **1 lần** với error feedback trong prompt. Vẫn fail → raise `InventoryGenerationError`, pipeline abort, status `FAILED_AT_QUESTION_INVENTORY`.

### Lý do tách stage này

Bắt được F2 (câu quá rộng) và F5 (bỏ sót domain) bằng **structure thay vì prompt**. Generator sau phải sinh câu cho từng decision rõ ràng → không thể "quá rộng" được nữa.

---

## Stage 2: Question Materializer

### Input
- `decisions[]` từ Stage 1
- `brief_v2`

### Output
- `questions_draft: list[Question]` (1 question / decision)

### Prompt template (`prompts/materializer.txt`)

```
Bạn là technical writer. Cho 1 decision cần ra, viết 1 câu hỏi debate-able cho 2 agent AI.

Decision:
  id: {decision.id}
  description: {decision.description}
  reason: {decision.reason_required}
  blocks: {decision.blocks_what}
  has_safe_default: {decision.has_safe_default}
  default: {decision.default_if_any}

Brief context (tóm tắt 500 token):
{brief_digest}

Quy tắc:
1. Câu hỏi PHẢI có ≥2 option khả thi trong câu (vd: "PostgreSQL FTS vs Elasticsearch vs Meilisearch?")
2. KHÔNG hỏi yes/no không có lựa chọn (BAD: "Should we have search?")
3. Có context cụ thể từ brief (vd: scale, user count) để agent debate dựa số liệu
4. Độ dài 80-250 ký tự
5. Phân loại:
   - has_safe_default=true → "OPTIONAL"
   - blocks_what có item critical cho MVP → "REQUIRED"
   - còn lại → "STRATEGIC"
6. Chọn 2 agent debate (agent_a, agent_b) từ domain_hints. Phải khác nhau.

Trả JSON:
{
  "text": "...",
  "classification": "REQUIRED" | "STRATEGIC" | "OPTIONAL",
  "agent_a": "<AgentKey>",
  "agent_b": "<AgentKey>",
  "domain": "<primary domain from decision.domain_hints>"
}
```

### Mapping decision → Question
```python
Question(
    id=f"Q{idx+1}_{decision.id}",          # readable + traceable
    text=materializer_output["text"],
    classification=materializer_output["classification"],
    domain=materializer_output["domain"],
    agent_a=materializer_output["agent_a"],
    agent_b=materializer_output["agent_b"],
    source_decision_id=decision.id,         # NEW: traceability
)
```

Field `source_decision_id` mới thêm vào `Question` → giữ link tới decision gốc → spec generator dùng để trace question→spec section (xem spec C).

### Performance
- Batch N decisions thành 1 LLM call (prompt template support array)
- Fallback: nếu batch fail JSON, fallback per-decision call (chậm nhưng resilient)

---

## Stage 3: Critic Loop

### Input
- `questions_draft[]`
- `brief_v2`

### Output
- `questions_refined[]` (subset hoặc rewrite của draft)

### Logic

```
iteration = 0
questions = draft.copy()
while iteration < MAX_CRITIC_ITER:  # MAX=2
    flags = critic_judge(questions, brief)
    if not flags:
        break
    for flag in flags:
        if flag.action == "drop":
            questions.remove(flag.q_id)
        elif flag.action == "rewrite":
            questions[flag.q_id] = rewrite(flag.q_id, flag.reason, brief)
        elif flag.action == "merge":
            questions = merge_pair(questions, flag.q_id_a, flag.q_id_b)
    iteration += 1
```

### Critic Judge prompt (`prompts/critic.txt`)

```
Bạn là QA reviewer cho question generator. Đánh giá danh sách câu hỏi và flag câu
có vấn đề. KHÔNG tự viết lại — chỉ flag.

Brief tóm tắt: {brief_digest}

Questions:
{numbered_list}

Quy tắc flag, mỗi câu kiểm tra 4 vấn đề:

A. SHALLOW: Binary yes/no không có 2+ option cụ thể trong câu.
   → action: "rewrite", reason: "binary without options"

B. OUT_OF_SCOPE: Câu không liên quan đến scope_in/scope_out/known_unknowns của brief.
   → action: "drop", reason: "scope drift"

C. ALREADY_DECIDED: Câu hỏi về thứ brief.fields đã trả lời rõ ràng.
   → action: "drop", reason: "already decided in brief: {field_name}"

D. DUPLICATE: Pair câu hỏi cùng quyết định (semantic, không chỉ literal).
   → action: "merge", reason: "duplicate of Q{n}"

Trả JSON:
{
  "flags": [
    {"q_id": "Q3", "action": "rewrite", "reason": "..."},
    {"q_id": "Q5", "action": "drop", "reason": "..."},
    {"q_ids": ["Q7", "Q9"], "action": "merge", "reason": "..."}
  ]
}

Nếu không có vấn đề: `{"flags": []}`
```

### Rewrite sub-call (`prompts/critic_rewrite.txt`)

```
Câu hỏi "{original_text}" bị flag: "{reason}".

Viết lại với:
- Cùng decision đang nhắm tới: {source_decision_summary}
- Khắc phục đúng vấn đề flag
- Giữ classification, agent_a, agent_b, domain

Trả JSON: {"text": "..."}
```

### Merge sub-logic
- Giữ Question có classification cao hơn (REQUIRED > STRATEGIC > OPTIONAL)
- Text mới = LLM call merge 2 câu thành 1 broader question
- `source_decision_id` = list of cả 2 decision

### Stop condition
- 2 iteration hết quota
- HOẶC critic trả `flags=[]`
- HOẶC số question còn lại < 5 (đã cắt quá nhiều) → trigger alert, ghi vào pipeline event, vẫn pass với output hiện có

### Critic bias mitigation
Critic dùng **cùng LLM** với materializer có rủi ro tự đồng ý với chính mình. Mitigation:
- Prompt critic có **explicit list** 4 vấn đề (rule-based, không "judge generally")
- System message critic emphasize "skeptical reviewer, default flag rather than approve"
- Real-mode: dùng model khác cho critic (vd materializer = Claude, critic = GPT-4) — config được

---

## Stage 4: Coverage Validator

### Input
- `questions_refined[]`
- `decisions[]` (từ Stage 1)
- `brief_v2`

### Output
- `questions_final[]` (same as refined, hoặc throw error)
- `coverage_report: dict` (log only)

### 4 rule-based check

**C1. Decision coverage**
```python
covered = {q.source_decision_id for q in questions if q.source_decision_id}
missing = {d.id for d in decisions if d.id not in covered}
# fail if any decision with classification != OPTIONAL is missing
```

**C2. Domain balance**
```python
domain_counts = Counter(q.domain for q in questions)
required_domains = set().union(*[set(d.domain_hints) for d in decisions if not d.has_safe_default])
missing_domains = required_domains - set(domain_counts.keys())
# fail if any required_domain has 0 questions
```

**C3. Classification distribution**
```python
required_count = sum(1 for q in questions if q.classification == "REQUIRED")
# fail if required_count / total < 0.3 (suspiciously low)
# fail if required_count == 0 (impossible for non-trivial project)
```

**C4. Question count sanity**
```python
total = len(questions)
expected = 0.6 * len(decisions)  # 40% có thể bị drop bởi critic
# fail if total < max(5, expected * 0.5)
```

### Fail handling
- C1 missing decisions → re-trigger Stage 2 chỉ cho decisions thiếu, append vào list, re-run critic (1 vòng)
- C2 missing domains → log warning, continue (không block — có thể decision thật sự không cần domain đó)
- C3 → log warning, continue
- C4 → throw `InsufficientQuestionsError`, pipeline abort

### Coverage report format
```yaml
total_decisions: 12
total_questions_final: 9
decision_coverage_pct: 0.75
missing_decisions: ["leaderboard_refresh_strategy"]
domain_distribution: {backend: 4, security: 2, product: 2, qa: 1}
classification_distribution: {REQUIRED: 5, STRATEGIC: 3, OPTIONAL: 1}
critic_iterations_used: 2
critic_drops: 3
critic_rewrites: 2
critic_merges: 1
```

Saved as artifact `QUESTION_COVERAGE_REPORT` cho audit.

---

## Domain Registry (extensible)

### Hiện tại (hardcoded 6)
`SecuritySpecialist, BackendArchitect, DevOpsSpecialist, ProductManager, DatabaseSpecialist, QAEngineer`

### Mở rộng (12)
File `domains.py`:

```python
DOMAIN_REGISTRY: dict[str, DomainSpec] = {
    "security": DomainSpec(agent_key="SecuritySpecialist", aliases=["sec", "compliance"]),
    "backend": DomainSpec(agent_key="BackendArchitect", aliases=["api", "server"]),
    "devops": DomainSpec(agent_key="DevOpsSpecialist", aliases=["infra", "ops"]),
    "product": DomainSpec(agent_key="ProductManager", aliases=["pm", "ux"]),
    "database": DomainSpec(agent_key="DatabaseSpecialist", aliases=["db", "data"]),
    "qa": DomainSpec(agent_key="QAEngineer", aliases=["test", "quality"]),
    # NEW:
    "frontend": DomainSpec(agent_key="FrontendArchitect", aliases=["ui", "client"]),
    "mobile": DomainSpec(agent_key="MobileEngineer", aliases=["ios", "android"]),
    "ml": DomainSpec(agent_key="MLEngineer", aliases=["ai", "model"]),
    "data_eng": DomainSpec(agent_key="DataEngineer", aliases=["etl", "pipeline"]),
    "legal": DomainSpec(agent_key="LegalAdvisor", aliases=["privacy", "gdpr"]),
    "finance": DomainSpec(agent_key="FinanceAnalyst", aliases=["cost", "billing"]),
}
```

`DomainSpec.agent_key` map sang `AGENT_PROMPTS` (sẽ load real agency-agents trong spec B).

### Aliases handling
LLM có thể trả `"infra"` thay `"devops"`. Resolver:
```python
def resolve_domain(raw: str) -> str:
    raw = raw.lower().strip()
    if raw in DOMAIN_REGISTRY:
        return raw
    for canonical, spec in DOMAIN_REGISTRY.items():
        if raw in spec.aliases:
            return canonical
    return None  # → fallback "backend" in materializer
```

### Adding new domain
- Add entry vào `DOMAIN_REGISTRY`
- Add prompt key vào `AGENT_PROMPTS` (spec B)
- Add golden idea với domain đó (eval harness)
- No code change needed elsewhere

---

## Backward Compatibility

`Question` dataclass thêm 1 field optional `source_decision_id: str | None = None`. Existing code dùng `Question` không break.

`generate_questions(brief, llm_client)` cũ → giữ làm wrapper:
```python
def generate_questions(brief, llm_client):
    """DEPRECATED. Use questions.pipeline.run() with brief_v2."""
    if brief.get("brief_version") == 2:
        return run_pipeline(brief, llm_client).questions_final
    # legacy path (brief v1 skeleton)
    return legacy_single_call(brief, llm_client)
```

→ Run cũ vẫn chạy. Migration sang brief v2 ở Spec E.

---

## Configuration

```python
@dataclass
class QuestionPipelineConfig:
    max_decisions: int = 25
    min_decisions: int = 8
    max_critic_iterations: int = 2
    min_final_questions: int = 5
    batch_materialize: bool = True
    critic_model: str | None = None  # None = same as main, else override
    coverage_strict: bool = False    # True = abort on any C2/C3 fail
```

Load từ env (`QUESTION_PIPELINE_*`) hoặc config file.

---

## Error Handling & Pipeline States

New run statuses:
- `RUNNING_PHASE_1B_INVENTORY` (Stage 1)
- `RUNNING_PHASE_1B_MATERIALIZE` (Stage 2)
- `RUNNING_PHASE_1B_CRITIC` (Stage 3)
- `RUNNING_PHASE_1B_COVERAGE` (Stage 4)
- `FAILED_AT_QUESTION_INVENTORY`
- `FAILED_AT_QUESTION_COVERAGE` (C1/C4 fail)

Each stage emits events:
- `QUESTION_INVENTORY_GENERATED` (count)
- `QUESTION_DRAFT_GENERATED`
- `CRITIC_ITERATION_DONE` (drops, rewrites, merges)
- `COVERAGE_REPORT_GENERATED`

---

## Testing Strategy

### Unit
- `inventory.py`: stub LLM trả JSON cố định, assert Decision list parse đúng
- `materializer.py`: 1 decision → 1 question, assert classification logic
- `critic.py`: feed questions có biased flag, assert action map đúng
- `coverage.py`: rule check với fixture questions/decisions
- `domains.py`: alias resolver tests

### Integration
- `tests/integration/test_question_pipeline.py`: full pipeline với stub LLM, assert output structure + coverage_report saved
- Test mỗi error path: inventory fail validation, materializer fail JSON, critic infinite loop guard, coverage missing decisions trigger re-materialize

### Eval (cross-spec)
- Eval Harness Layer 2 (8 metric về question) cover spec này
- E2: run pipeline trên 5 golden idea (stub mode), assert metrics pass threshold
- E9: real mode trên 2 idea, assert no regression vs prev tag

---

## LLM Cost Analysis

Per project (vs 1 call hiện tại):

| Stage | Calls | Tokens (~) |
|---|---|---|
| 1. Inventory | 1 | brief 2k in, decisions 1.5k out |
| 2. Materialize | 1 (batched) or 12 (fallback) | 4k in, 2k out |
| 3. Critic | 2 × (judge + N rewrites + M merges) ≈ 4-8 | 6-12k in, 2-4k out |
| 4. Coverage | 0 (rule-based) | 0 |

Tổng: **~6-12 LLM calls vs 1 hiện tại**. Cost +6-12x.

Trade-off: Phase 1b chạy 1 lần/project. +6 call ≈ +$0.10-0.30 với Sonnet 4.6. Đổi lại question quality không phải đoán.

**Optimization sau:**
- Cache inventory by brief.source_hash (cùng idea = cùng inventory)
- Skip critic iteration 2 nếu iteration 1 trả flags=[]
- Skip materializer batch nếu < 5 decisions

---

## Build Order

| Slice | Đầu ra | Test |
|---|---|---|
| **Q1** | `domains.py` + `Question.source_decision_id` field | unit alias resolver |
| **Q2** | `inventory.py` + prompts/inventory.txt | unit stub LLM |
| **Q3** | `materializer.py` + batch logic | unit per-decision + batch |
| **Q4** | `pipeline.py` skeleton (1+2 chained, skip 3+4) | integration e2e |
| **Q5** | `critic.py` flag detection only (no rewrite/merge) | unit flag parsing |
| **Q6** | Critic rewrite + merge sub-logic | integration với fixture flags |
| **Q7** | `coverage.py` C1+C4 (block) + C2+C3 (warn) | unit + integration |
| **Q8** | Wire pipeline vào debate_pipeline.py, deprecate old | regression test full Phase 1 |
| **Q9** | Coverage report artifact + audit events | integration |
| **Q10** | Eval harness E5 chạy được trên pipeline mới | smoke test |

Q1-Q4 = MVP usable. Q5-Q8 = quality. Q9-Q10 = audit + measurement.

---

## Open Questions

1. **Critic infinite loop:** rule "max 2 iter" có thể bỏ sót flag cycle (rewrite → critic flag lại → rewrite tiếp). Mitigation: track question_text hash, nếu rewrite ra cùng text 2 lần → force drop.

2. **Cross-model critic:** dùng GPT-4 critic Claude output có thể strict quá → drop nhiều. Cần A/B test trên golden set trước khi enable.

3. **Inventory caching:** brief.source_hash thay đổi mỗi khi user edit 1 ký tự brief. Có nên hash structural fields (loại bỏ formatting) không?

4. **Question dependency:** một số question logically depend vào answer của question khác (chọn DB → query pattern → indexing strategy). Hiện không model dependency. Cần spec riêng "Conditional Questions" nếu hữu ích.

5. **Multi-language brief:** brief tiếng Việt, agent prompts tiếng Anh — materializer trả câu hỏi tiếng gì? Hiện chưa quyết. Đề xuất: match brief language by detection.

---

## Out of Scope (deferred)

- Conditional / dependent questions (Q2 phụ thuộc answer Q1)
- Multi-turn clarification per question (user can refine question text trước khi debate)
- Per-question evidence retrieval (RAG để agent có thêm context)
- Question priority scoring (which question matters most)
- User-injected questions (user thêm câu hỏi của riêng vào pool)
