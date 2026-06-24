# Design Spec: Debate Engine Upgrade

**Date:** 2026-05-23
**Status:** Draft
**Scope:** Replace shallow ~3-line agent prompts with real agency-agents library, robust moderator JSON handling, OPTIONAL question visibility, extensible agent registry.

---

## Motivation

Phân tích Phase 1 + docs/code mismatch:

- **Agent prompts thin:** [agents.py:3-40](src/ai_dev_system/debate/agents.py#L3-L40) chỉ 3 dòng/agent, không phải "agency-agents" thật như [architecture.md:13](docs/architecture.md#L13) hứa
- **Moderator JSON failure silent:** [rounds.py:44-52](src/ai_dev_system/debate/rounds.py#L44-L52) catch JSONDecodeError → fallback `NEED_MORE_EVIDENCE` confidence 0 → không phân biệt được "thật sự cần human" vs "LLM hỏng JSON"
- **OPTIONAL questions auto-resolve, không show ở Gate 1** — nếu LLM phân loại sai REQUIRED thành OPTIONAL, silently drop
- **Domain enum cứng 6 agent** — không cover mobile / ML / data eng / legal / frontend
- **Confidence 0.8 threshold dễ đạt giả** vì prompt nông, 2 agent đồng ý ngay vòng 1
- **Không có diversity enforcement** — 2 agent có thể đồng quan điểm vì backstory giống nhau

---

## Goals

- Load agency-agents prompts thật (dense, ≥500 token/agent) từ `references/agency-agents/`
- Moderator JSON failure phân biệt với genuine ESCALATE
- OPTIONAL questions vẫn show ở Gate 1 (collapsed, có thể expand)
- Extensible agent registry: thêm agent không cần sửa code
- Diversity guardrails: 2 agent debate phải có lens khác biệt
- Confidence calibration: chống "giả thắng" vòng 1

## Non-goals

- **Không** đổi debate flow (vẫn 5-round max, A → B → Moderator)
- **Không** đổi `RoundResult` / `Question` data structure (chỉ thêm field)
- **Không** multi-agent (>2 per question) — defer
- **Không** retrieval-augmented agent (RAG) — defer

---

## Architecture

```
src/ai_dev_system/debate/
├── agents/                       # was: agents.py (single file)
│   ├── __init__.py
│   ├── registry.py               # AgentRegistry + DomainSpec
│   ├── loader.py                 # load from references/agency-agents/*.md
│   ├── prompts/                  # cached / fallback prompts
│   │   ├── _fallback.py          # 3-line generic fallback nếu file thiếu
│   │   └── _moderator.py         # moderator system prompt
│   └── diversity.py              # check 2 agent có khác lens không
├── rounds.py                     # updated: moderator JSON robust
├── engine.py                     # updated: OPTIONAL still visible
└── report.py                     # updated: add ParseFailReason
```

```
references/agency-agents/         # actual prompt library (NEW dir)
├── security_specialist.md
├── backend_architect.md
├── devops_specialist.md
├── product_manager.md
├── database_specialist.md
├── qa_engineer.md
├── frontend_architect.md
├── mobile_engineer.md
├── ml_engineer.md
├── data_engineer.md
├── legal_advisor.md
├── finance_analyst.md
└── _moderator.md
```

---

## Agency-Agent Prompt Format

### File template

```markdown
---
agent_key: SecuritySpecialist
domain: security
version: 1
aliases: [sec, compliance, security_engineer]
debate_role: critic_first      # or "advocate_first" | "neutral"
typical_paired_with: [BackendArchitect, ProductManager, DevOpsSpecialist]
---

# Identity
Bạn là Security Specialist với 10+ năm kinh nghiệm AppSec/CloudSec. Bạn đã chứng kiến
breach từ misconfigured IAM tới supply chain attack, và bạn approach mọi proposal với
threat modeling mindset.

# Mission
Trong debate này, bạn defend phía security/compliance/privacy. Nhiệm vụ KHÔNG phải
luôn nói "no" — mà là raise risks cụ thể và đề xuất mitigation có cost/benefit rõ.

# Lens
Đánh giá proposal qua 6 trục:
1. **Authentication** — mechanism, lifetime, revocation
2. **Authorization** — granularity, default deny, audit
3. **Data exposure** — at-rest, in-transit, in-logs, in-LLM-prompts
4. **Threat surface** — attack vectors, blast radius
5. **Compliance** — GDPR/HIPAA/SOC2/PCI-DSS theo brief.compliance
6. **Operational security** — secret rotation, incident response

# Workflow trong 1 debate round
1. Đọc question + previous round summary (nếu có)
2. Identify 1-2 lens cụ thể relevant nhất với question này
3. Đưa quan điểm có structure:
   - Risk statement (1 câu)
   - Evidence/threat scenario cụ thể (1-2 câu)
   - Proposed approach (1 câu)
   - Trade-off acknowledgment (1 câu)
4. KHÔNG repeat điểm agent kia đã nêu

# Deliverable
Position statement, max 200 từ, structure như Workflow.

# What you DO NOT do
- Không nói "depends on threat model" mà không cụ thể hóa
- Không refuse to take a position
- Không đồng ý ngay vòng 1 nếu có risk thật
- Không hallucinate compliance requirement không có trong brief

# Tone
Direct, evidence-based, không sợ disagree. Acknowledge khi đối phương có điểm đúng.
```

### Why dense prompts matter

- 200+ token system prompt → agent có structured way to think
- Lens enumeration → debate có specific axis để bám vào, không generic
- Workflow → output có shape consistent → moderator dễ extract
- "What you DO NOT do" → chống failure modes cụ thể đã quan sát

### Loading strategy

```python
# loader.py
def load_agent_prompt(agent_key: str) -> AgentPrompt:
    path = Path("references/agency-agents") / f"{snake(agent_key)}.md"
    if not path.exists():
        # fallback (warn loudly)
        logger.warning(f"Agency-agent prompt missing for {agent_key}, using fallback")
        return _FALLBACK_PROMPTS[agent_key]
    return parse_agent_md(path.read_text())
```

`parse_agent_md` parse frontmatter YAML + body markdown.

Caching: load once at module init, in-memory dict. File watch không cần (prompt thay đổi → restart process là ok).

---

## Agent Registry

### Structure

```python
@dataclass
class AgentSpec:
    key: str                          # "SecuritySpecialist"
    domain: str                       # "security"
    version: int
    aliases: list[str]
    debate_role: str                  # critic_first | advocate_first | neutral
    typical_paired_with: list[str]
    system_prompt: str                # full body
    file_path: Path | None            # for traceability

class AgentRegistry:
    def get(self, key: str) -> AgentSpec: ...
    def by_domain(self, domain: str) -> list[AgentSpec]: ...
    def list_all(self) -> list[AgentSpec]: ...
    def pair_suggestion(self, primary: str, decision_domains: list[str]) -> str:
        """Suggest counterparty agent maximizing lens diversity."""
```

### Pair suggestion algorithm

Given decision's `domain_hints` from Stage 1 (Question Gen spec), pick 2 agents to maximize coverage:
1. Primary agent = first match domain_hints[0]
2. Counter agent = agent in `primary.typical_paired_with` ∩ remaining domain_hints
3. Fallback counter = agent in different domain than primary (anti-echo-chamber)

---

## Diversity Guardrails

### Same-domain pairing detected
If `agent_a.domain == agent_b.domain` → reject pairing, log warning, force re-pair via `pair_suggestion`. This was happening with hardcoded `BackendArchitect` fallback in old `questions.py`.

### Echo detection in rounds
After round 1, check semantic similarity of `agent_a_position` vs `agent_b_position`:
```python
if cosine_similarity(emb_a, emb_b) > 0.85 and round_num == 1:
    # both agents agreed verbatim → suspicious
    inject_skeptic_prompt_for_round_2()
```

`inject_skeptic_prompt`: prepend to agent_b round 2 prompt:
> *"Round 1 cho thấy bạn và {agent_a} đồng ý gần như hoàn toàn. Hãy steel-man phía ngược lại, hoặc raise edge case mà cả 2 đã miss."*

Stop after round 2 if still agree (genuine consensus).

---

## Moderator JSON Robustness

### Current failure
```python
try: verdict = json.loads(moderator_raw)
except: verdict = {"status": "NEED_MORE_EVIDENCE", "confidence": 0.0, ...}
```
→ Indistinguishable from real NEED_MORE_EVIDENCE.

### New flow

```python
class ParseFailReason(Enum):
    JSON_INVALID = "json_invalid"
    MISSING_FIELDS = "missing_fields"
    INVALID_STATUS = "invalid_status"
    INVALID_CONFIDENCE = "invalid_confidence"

def parse_moderator_response(raw: str) -> tuple[RoundResult | None, ParseFailReason | None]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Try to extract JSON block from prose (LLM sometimes wraps in ```json)
        data = extract_json_block(raw)
        if data is None:
            return None, ParseFailReason.JSON_INVALID

    if not all(k in data for k in ["status", "confidence", "summary"]):
        return None, ParseFailReason.MISSING_FIELDS

    if data["status"] not in VALID_STATUSES:
        return None, ParseFailReason.INVALID_STATUS

    try:
        conf = float(data["confidence"])
        if not (0 <= conf <= 1):
            return None, ParseFailReason.INVALID_CONFIDENCE
    except (TypeError, ValueError):
        return None, ParseFailReason.INVALID_CONFIDENCE

    return RoundResult(...), None
```

### Retry on parse fail

```python
for retry in range(MAX_MODERATOR_RETRIES):  # 2
    raw = llm.complete(MODERATOR_PROMPT_STRICT, debate_context)
    result, fail_reason = parse_moderator_response(raw)
    if result:
        return result
    # add fail feedback to next prompt
    debate_context = f"{debate_context}\n\nPrevious response failed parsing: {fail_reason}. Return STRICT JSON only."

# all retries exhausted → mark as JSON_FAIL distinctly
return RoundResult(
    resolution_status="MODERATOR_PARSE_FAILED",  # NEW status
    confidence=0.0,
    moderator_summary=raw[:500],  # preserve raw for debug
    caveat=f"Moderator failed JSON {MAX_MODERATOR_RETRIES} times: {fail_reason}",
)
```

### New `resolution_status` enum
```
RESOLVED
RESOLVED_WITH_CAVEAT
ESCALATE_TO_HUMAN
NEED_MORE_EVIDENCE
MODERATOR_PARSE_FAILED      # NEW
```

Gate 1 skill (spec D) handles `MODERATOR_PARSE_FAILED` distinctly:
- Show raw moderator output
- Treat as "need human to read raw debate and decide"
- Different UI treatment than ESCALATE_TO_HUMAN (which means agents disagreed substantively)

---

## OPTIONAL Questions Visibility

### Current
```python
if q.classification == "OPTIONAL":
    results.append(auto_resolve(q))  # skipped, no debate
    continue
```
Gate 1 skill currently shows under "AI resolved" generically.

### New: 2-tier display at Gate 1

OPTIONAL questions still auto-resolve (no debate cost) BUT:
- Show in Gate 1 report under section "AI auto-resolved (OPTIONAL)" — collapsed by default
- Skill provides command: `expand Q5` → show what the auto-resolution was
- User can `override Q5: <new answer>` if they think classification was wrong

### Implementation
- `auto_resolve()` adds `auto_resolution_reason: str` to RoundResult: "OPTIONAL — using safe default: {decision.default_if_any}"
- Gate 1 review skill renders 3 sections instead of 2:
  1. **Cần quyết định của bạn** (ESCALATE / PARSE_FAILED)
  2. **Đã resolve qua debate** (RESOLVED / RESOLVED_WITH_CAVEAT)
  3. **Auto-resolved (OPTIONAL)** — collapsed (xem spec D)

---

## Round Prompt Updates

### Round context enrichment

Currently agents only see question text + previous summary. Add:
- Brief digest (~300 token from brief_v2)
- Decision context (description + reason_required from Stage 1)

```python
agent_user = f"""
Project context: {brief_digest}

Decision đang debate: {decision.description}
Vì sao quan trọng: {decision.reason_required}
Sẽ block: {decision.blocks_what}

Câu hỏi: {question.text}
{prev_context}

{role_instruction}
"""
```

Lý do: agent có context tại sao quyết định này quan trọng → debate sâu hơn, không generic.

### Round-1 vs Round-N

Round 1: full context, no previous summary.
Round 2+: tóm tắt vòng trước + **explicit "what changed your mind / what's still unresolved"** prompt.

---

## Confidence Calibration

### Problem
Threshold 0.8 confidence dễ đạt giả vòng 1 với prompt nông.

### Mitigation 1: Minimum rounds for REQUIRED
```python
if q.classification == "REQUIRED" and round_num == 1:
    # never accept resolve on round 1, regardless of confidence
    continue
```
REQUIRED phải debate ≥2 round → forces dialectic.

### Mitigation 2: Calibrated moderator prompt
Update moderator system prompt:
> *"Cẩn thận với high confidence ở vòng 1. Nếu 2 agent đồng ý mà không nêu trade-off cụ thể, đó là echo chamber — return confidence ≤ 0.6 và NEED_MORE_EVIDENCE để force round 2."*

### Mitigation 3: Diversity-weighted confidence
```python
if cosine_similarity(emb_a, emb_b) > 0.85:
    confidence *= 0.7  # discount agreement that's too similar
```

---

## Configuration

```python
@dataclass
class DebateConfig:
    max_rounds: int = 5
    confidence_threshold: float = 0.8
    required_min_rounds: int = 2          # NEW
    max_moderator_retries: int = 2        # NEW
    echo_similarity_threshold: float = 0.85   # NEW
    diversity_confidence_penalty: float = 0.7  # NEW
    agent_prompt_dir: str = "references/agency-agents"
```

---

## Backward Compatibility

### Data
- `RoundResult` thêm `resolution_status="MODERATOR_PARSE_FAILED"` + optional `auto_resolution_reason: str | None`
- `Question` unchanged from spec A
- Old debate reports remain readable (new fields are optional)

### Code
- `debate/agents.py` old file → re-export from `debate/agents/__init__.py` for 1 release
- `AGENT_PROMPTS` dict deprecated in favor of `AgentRegistry.get()`
- `VALID_AGENT_KEYS` deprecated in favor of `registry.list_all()`

### Skills
- `/review-debate` skill must handle `MODERATOR_PARSE_FAILED` (spec D)

---

## Testing Strategy

### Unit
- `loader.py`: parse golden agent .md file, assert frontmatter + body
- `registry.py`: pair_suggestion với various decision domains
- `diversity.py`: same-domain rejection, echo detection
- `rounds.py`: parse_moderator_response 5 fail reasons × retry logic
- `agents/__init__.py`: deprecation shim still imports

### Integration
- Full debate run với stub LLM, all 5 RoundResult statuses produced
- Real-mode (smoke) debate 1 question 1 round, assert no fallback prompt loaded

### Eval (cross-spec)
- E10 (debate runner in harness) trigger debate metrics
- New metric `d.parse_fail_rate` must be 0 with new system

### Manual review
- Read 1 generated debate transcript per agent_key, check tone/structure match prompt expectations

---

## Migration

### Existing 6 agents
- Write `.md` for each in `references/agency-agents/` based on current 3-line prompts but expanded to dense format
- Reviewed manually before merge — these prompts define product behavior

### Run cũ
- Runs with old `debate_report.json` schema: missing `auto_resolution_reason` and possibly using old status names
- Gate 1 skill handles missing fields gracefully (default to "no reason given")
- No data migration needed

---

## Build Order

| Slice | Đầu ra | Test |
|---|---|---|
| **D1** | Agent .md format spec + 2 sample files (Security, Backend) | manual review |
| **D2** | `loader.py` + `parse_agent_md()` | unit parse |
| **D3** | `AgentRegistry` + `domains.py` integration | unit |
| **D4** | Diversity guardrails (same-domain reject, echo detect) | unit + integration |
| **D5** | Moderator JSON robustness + new PARSE_FAILED status | unit 5 fail modes |
| **D6** | Round prompt enrichment (brief digest + decision context) | integration regression |
| **D7** | Confidence calibration (3 mitigations) | unit + integration |
| **D8** | 10 remaining agent .md (Frontend, Mobile, ML, ...) | manual review |
| **D9** | OPTIONAL visibility hooks (auto_resolution_reason field) | integration |
| **D10** | Deprecation shim + remove old agents.py | regression |

D1-D5 = MVP (works with new schema). D6-D9 = quality. D10 = cleanup.

---

## Cost Analysis

Per question (vs current):

| Stage | Current | New | Delta |
|---|---|---|---|
| Agent A | 1 call, ~200 token system | 1 call, ~600 token system | +0.4k context |
| Agent B | same | same | +0.4k context |
| Moderator | 1 call | 1-3 call (retry on parse fail) | +0 to +2 calls |
| Round count avg | 2.5 | 2.5-3 (min 2 for REQUIRED) | +0.5 round |

Per question: ~3-4x token, ~1.2x calls. Per project (10 questions): +$0.20-0.50 with Sonnet 4.6.

---

## Open Questions

1. **Where to host agency-agents prompts?** In repo (`references/agency-agents/`) means versioned with code. External (S3 / vector DB) means update without deploy. Recommend in-repo for now, simpler.

2. **Multilingual prompts?** Brief tiếng Việt, agent prompts hiện tiếng Việt. Khi mở rộng thị trường, có nên có `_en.md` variant? Defer.

3. **Per-domain agent count:** mỗi domain 1 agent là enough? Có cần multiple variants (vd `SecuritySpecialist_Startup` vs `SecuritySpecialist_Enterprise`)? Defer until 10+ projects show pattern.

4. **Cost of dense prompts:** +0.4k system prompt mỗi call × N questions × 2 agents × 5 rounds = ~5-10k tokens/project. Trivial nhưng cumulative across many projects ăn budget. Mitigation: prompt caching (Anthropic API supports).

5. **Embedding for echo detection:** cần model embedding nào? OpenAI text-embedding-3-small (~$0.02/1M) đủ rẻ. Cache embeddings per round.

---

## Out of Scope

- Multi-agent (>2 per question) debate
- RAG for agent (retrieval from docs / past projects)
- Agent learning / fine-tuning from past debates
- User-authored custom agents (let user define their own .md)
- Voice debate (audio agent positions)
- Live human-as-third-agent injection mid-debate
