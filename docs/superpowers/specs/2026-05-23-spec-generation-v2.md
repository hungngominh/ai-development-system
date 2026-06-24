# Design Spec: Spec Generation v2 (finalize_spec)

**Date:** 2026-05-23
**Status:** Draft
**Scope:** Replace single-LLM-call `finalize_spec()` with multi-section generator that reads brief v2 + approved_answers, produces 5-file spec bundle with traceability map and grounding checks.

---

## Motivation

Current [finalize_spec.py](src/ai_dev_system/finalize_spec.py):

```python
response = llm_client.complete(
    system=SHORT_PROMPT,
    user=json.dumps({"run_id": run_id, "approved_answers": approved_answers}),
)
sections = json.loads(response)  # 5-section dict
# write 5 .md files, done
```

**Problems:**

1. **Reads only approved_answers** — does not see raw brief, scope, NFR priority, constraints. Spec drifts from original intent.
2. **Single LLM call for 5 sections** — token budget split → each section gets ~1k tokens, shallow.
3. **No traceability** — can't trace "spec section X came from decision Y / brief field Z".
4. **No grounding check** — LLM can hallucinate features not in scope_in, miss items in scope_out.
5. **JSON fallback strategy is destructive** — if LLM returns non-JSON, all 5 files get same garbage content with header `# {key}`.
6. **No section-level retry** — if `acceptance_criteria` fails, must re-run whole pipeline.
7. **Spec assumes consensus** — doesn't surface assumptions or open questions from brief.assumptions[].

---

## Goals

- Each spec section generated independently (parallel-able, retry-able per section)
- Grounding: spec MUST reference brief fields verbatim where applicable
- Traceability map: every assertion → source (brief field / decision / answer)
- Scope guardrails: every functional requirement ∈ scope_in, none ∈ scope_out
- Surface assumptions explicitly (don't hide unknowns)
- Backward compat: output still 5 files with same names

## Non-goals

- **Không** thay `SpecBundle` data class shape (5 files unchanged)
- **Không** auto-generate diagrams (defer)
- **Không** multi-language spec output (defer)
- **Không** custom spec templates (always 5 sections)

---

## Architecture

```
brief_v2 + approved_answers + decisions + questions
              │
              ▼
       [Section Planner]   ──→  section_outlines[]
              │
              ▼
   [Parallel Section Generators × 5]
       │   │   │   │   │
       ▼   ▼   ▼   ▼   ▼
   proposal  design  functional  non_func  acceptance
       │   │   │   │   │
       └───┴───┴───┴───┘
              │
              ▼
        [Grounding Checker]   ──→ violations[]
              │
       ┌──────┴──────┐
   pass│             │fail
       ▼             ▼
   [Trace Map]    [Auto-repair × 1]
       │             │
       └─────┬───────┘
             ▼
        SpecBundle + trace_map.json
```

```
src/ai_dev_system/spec/
├── __init__.py
├── pipeline.py             # orchestrator
├── planner.py              # Section Planner
├── generators/
│   ├── proposal.py
│   ├── design.py
│   ├── functional.py
│   ├── non_functional.py
│   └── acceptance_criteria.py
├── grounding.py            # rule + LLM checks
├── tracer.py               # build trace map
├── repair.py               # auto-fix violations
└── prompts/
    ├── planner.txt
    ├── proposal.txt
    ├── design.txt
    ├── functional.txt
    ├── non_functional.txt
    ├── acceptance_criteria.txt
    ├── grounding_judge.txt
    └── repair.txt
```

Old `finalize_spec.py` → keep as wrapper for backward compat, internally call new pipeline.

---

## Stage 1: Section Planner

### Purpose
Pre-allocate "what each section should contain" before parallel generation. Prevents overlap (e.g. functional reqs leaking into design.md) and gaps.

### Input
- `brief_v2`
- `approved_answers` (from Gate 1)
- `decisions[]` (from Question Gen Stage 1)
- `questions[]` (final, with `source_decision_id`)

### Output
```python
@dataclass
class SectionOutline:
    section: str                     # "proposal" | "design" | ...
    must_cover: list[str]            # bullet points required
    must_reference: list[str]        # brief field IDs / decision IDs / scope_in items
    must_not_mention: list[str]      # things owned by other sections
    estimated_tokens: int
```

### Outline rules per section (deterministic, not LLM)

```python
PROPOSAL_RULES = SectionRules(
    must_cover=[
        "problem statement (verbatim from brief.problem_statement)",
        "target users (from brief.who_feels_pain)",
        "cost of doing nothing",
        "success metrics (from brief.success_metric)",
        "scope summary (in + out)",
    ],
    must_reference=["problem_statement", "success_metric", "scope_in", "scope_out"],
    must_not_mention=["specific tech stack", "API endpoints", "DB schema"],
)

DESIGN_RULES = SectionRules(
    must_cover=[
        "architecture overview",
        "tech stack decisions (from approved_answers)",
        "integration points (from brief.must_integrate_with, existing_auth)",
        "deployment topology (from brief.deployment_target)",
    ],
    must_reference=[
        "must_use_stack", "must_not_use", "existing_auth",
        "deployment_target", "data_residency",
        ...all decision IDs with domain=backend|devops|database
    ],
    must_not_mention=["business goals", "acceptance tests"],
)

FUNCTIONAL_RULES = SectionRules(
    must_cover=[
        f"every item in scope_in expanded to user stories / requirements",
        "explicit exclusion of scope_out items",
        "user flows for primary persona",
    ],
    must_reference=["scope_in", "scope_out", "primary_user"],
    must_not_mention=["latency targets", "scaling decisions"],
)

NON_FUNCTIONAL_RULES = SectionRules(
    must_cover=[
        "NFR priority ranking (from brief.nfr_priority — verbatim order)",
        "performance targets (expected_rps, latency_target)",
        "availability target",
        "data volume + retention",
        "compliance requirements",
    ],
    must_reference=[
        "nfr_priority", "expected_rps", "availability_target",
        "latency_target", "expected_data_volume", "compliance"
    ],
    must_not_mention=["feature list"],
)

ACCEPTANCE_RULES = SectionRules(
    must_cover=[
        "Given/When/Then scenarios cho mỗi scope_in item",
        "metrics from brief.success_metric must each have an AC",
        "measurable thresholds (no 'should be fast' — must be 'p95 < Xms')",
    ],
    must_reference=["scope_in", "success_metric", "done_definition"],
    must_not_mention=["how to implement"],
)
```

### Planner output

```python
{
    "outlines": [SectionOutline(...) for each section],
    "assumptions_to_surface": [
        # from brief.assumptions and decisions with low confidence
        "Search engine selection not yet decided",
        "Moderation policy pending stakeholder input",
    ],
    "open_questions": [
        # from debate ESCALATE / MODERATOR_PARSE_FAILED
    ]
}
```

This is **rule-based + 1 LLM call** to expand "must_cover" bullets with brief-specific detail. No section content generated yet.

---

## Stage 2: Parallel Section Generators

### Generator interface

```python
def generate_section(
    section: str,
    outline: SectionOutline,
    brief: dict,
    approved_answers: dict,
    decisions: list[Decision],
    llm_client,
) -> SectionDraft:
    ...
```

### Common prompt structure (per section)

```
You are writing the "{section}" section of a technical spec.

PROJECT BRIEF (ground truth — quote verbatim when needed):
{brief_digest_for_section}

APPROVED DECISIONS:
{decisions_relevant_to_section}

YOUR SECTION OUTLINE:
Must cover:
{outline.must_cover}

Must reference these brief fields verbatim:
{outline.must_reference}

Must NOT mention (owned by other sections):
{outline.must_not_mention}

Open assumptions (surface in "Open Questions" subsection):
{outline.assumptions_for_this_section}

Quy tắc viết:
1. Mỗi assertion phải reference được về brief field hoặc decision id.
   Format: "...[brief:problem_statement]" or "...[decision:db_choice]" inline.
2. Không invent features không có trong scope_in.
3. Không contradict scope_out (nếu scope_out có 'DM', không được viết tính năng DM).
4. Verbatim quote brief field khi đề cập: "Theo brief, {{field_name}}: '{{field_value}}'"

Trả về Markdown thuần. Không JSON wrap.
```

### Per-section nuances

**Proposal** (`prompts/proposal.txt`):
- Tone: executive summary, không kỹ thuật
- Length: 300-500 words
- Structure: Problem → Users → Value → Scope → Success

**Design** (`prompts/design.txt`):
- Tone: technical, decision-heavy
- Length: 800-1500 words
- Structure: Architecture → Components → Integration → Deployment → Trade-offs
- Include explicit "Decisions" table mapping decision_id → choice → rationale

**Functional** (`prompts/functional.txt`):
- Tone: requirement language (MUST/SHOULD/MAY)
- Length: 600-1200 words
- Structure: one subsection per scope_in item
- Each subsection: User story → Detailed requirements → Out of scope (cross-ref scope_out)

**Non-functional** (`prompts/non_functional.txt`):
- Tone: measurable, numeric
- Length: 400-700 words
- Structure: NFR priority order from brief.nfr_priority → each NFR with target value
- No "should be performant" — must be "p95 latency < 300ms"

**Acceptance Criteria** (`prompts/acceptance_criteria.txt`):
- Tone: testable, Given/When/Then
- Length: 500-1000 words
- Structure: one AC group per scope_in + one for each success_metric

### Parallelization

```python
with ThreadPoolExecutor(max_workers=5) as ex:
    futures = {ex.submit(generate_section, s, ...): s for s in SECTIONS}
    drafts = {s: f.result() for f, s in futures.items()}
```

5 sections in parallel → wall time ~= longest section, not sum.

### Per-section retry
If section generator throws or returns empty content, retry once with simpler prompt. After 2 fails → mark section as `degraded`, emit event `SECTION_GENERATION_DEGRADED`, continue pipeline (Phase B can still proceed with 4/5 sections; degraded section gets placeholder).

---

## Stage 3: Grounding Checker

### Rule-based checks (fast, deterministic)

**G1. Scope adherence**
```python
for section in [functional, acceptance]:
    for item in scope_out:
        if mentions_positively(section.text, item):
            violations.append(("scope_out_violation", section, item))
    coverage = sum(1 for item in scope_in if mentions(section.text, item)) / len(scope_in)
    if coverage < 0.8:
        violations.append(("scope_in_coverage_low", section, coverage))
```

**G2. Brief reference presence**
```python
for section, outline in zip(sections, outlines):
    refs_found = extract_refs(section.text)  # parse [brief:...] / [decision:...] markers
    missing = set(outline.must_reference) - refs_found
    if missing:
        violations.append(("missing_brief_ref", section, missing))
```

**G3. Verbatim quote check**
```python
for required_field in outline.must_reference_verbatim:
    quote = brief.fields[required_field].value
    if quote not in section.text:
        violations.append(("verbatim_missing", section, required_field))
```

**G4. Measurable AC check** (only for acceptance_criteria)
```python
for ac_block in parse_ac_blocks(acceptance.text):
    if not has_measurable_threshold(ac_block):
        violations.append(("non_measurable_ac", ac_block))
```
`has_measurable_threshold` regex: `\d+(\.\d+)?\s*(ms|s|%|RPS|MB|GB|user|day|week|month)`.

### LLM-based check (1 call, batched)

**G5. Hallucination detection**

```
You are auditing a spec for hallucinations. For each spec section, list any
factual claim (number, technology, integration, behavior) that is NOT supported by:
- brief fields
- approved decisions
- the question debate results

Brief: {brief_digest}
Decisions: {decisions_summary}
Spec sections: {sections}

Return JSON:
{
  "violations": [
    {"section": "...", "claim": "...", "issue": "no support in brief/decisions"}
  ]
}
```

---

## Stage 4: Auto-Repair (1 iteration)

### When triggered
- Any G1/G3/G5 violation
- G2 missing critical references
- G4 non-measurable AC

### How
For each violation, re-prompt section generator with:
```
Original section had violations:
{violations_for_this_section}

Brief context: {brief_digest_for_section}
Decision context: {decisions_for_section}

Rewrite the section to fix violations. KEEP everything else identical.
```

Single retry per section. If still violates → log to `SPEC_GROUNDING_VIOLATIONS` artifact, emit event, but **do not block pipeline**. Phase 1 user can decide at Gate 2 / Verification.

### Auto-repair cost cap
Max 5 repair calls per spec generation (1 per section). After cap, abandon repair, ship with violations logged.

---

## Stage 5: Trace Map Builder

### Output: `trace_map.json` (saved as separate artifact)

```json
{
  "spec_bundle_id": "uuid",
  "generated_at": "2026-05-23T...",
  "section_traces": {
    "functional.md": {
      "assertions": [
        {
          "line_range": [12, 18],
          "summary": "Forum supports voting on posts",
          "sources": [
            {"type": "brief_field", "id": "scope_in", "value": "voting"},
            {"type": "decision", "id": "voting_anti_abuse"},
            {"type": "question_answer", "q_id": "Q3_voting_anti_abuse"}
          ]
        },
        ...
      ],
      "unsourced_assertions": [],   // should be empty
      "verbatim_quotes": [
        {"field": "problem_statement", "line": 5}
      ]
    },
    ...
  },
  "open_assumptions": [
    "search engine not selected",
    "moderation policy pending"
  ],
  "grounding_violations": []  // copy from Stage 3 final state
}
```

### How built
Parse markdown sections, extract `[brief:...]` / `[decision:...]` / `[answer:Q\d+]` inline markers added by generators. Each marker becomes a trace entry.

### Why valuable
- Verification phase (existing) can use trace to spot-check
- User reviewing spec can click "where did this come from" → see source
- Future tooling: if brief field changes, identify which spec sections need re-gen

---

## Backward Compatibility

### `finalize_spec()` wrapper

```python
def finalize_spec(approved_answers, run_id, llm_client, output_dir) -> SpecBundle:
    """DEPRECATED signature. Loads brief from run_id if available."""
    brief = _load_brief_v2_from_run(run_id) or _build_legacy_brief(approved_answers)
    decisions = _load_decisions_from_run(run_id) or []
    questions = _load_questions_from_run(run_id) or []
    return run_spec_pipeline(brief, approved_answers, decisions, questions, output_dir, llm_client)
```

Legacy run (no brief v2): build a stub brief with only `approved_answers` content + mark `legacy=true`. Section generators see stub and produce weaker spec — same quality as today.

### SpecBundle shape

```python
@dataclass
class SpecBundle:
    version: int  # bumped to 2
    root_dir: Path
    files: dict[str, Path]   # unchanged keys
    trace_map_path: Path | None = None   # NEW
    grounding_violations: list = field(default_factory=list)   # NEW
```

### Storage

New artifact types:
- `SPEC_TRACE_MAP` (jsonb file alongside SPEC_BUNDLE)
- `SPEC_GROUNDING_VIOLATIONS` (only created if violations remain after repair)

---

## Testing Strategy

### Unit
- `planner.py`: each section rule → fixture brief → assert outline correctness
- `grounding.py` G1-G4 rule checks with positive + negative fixtures
- `tracer.py`: parse markdown with markers, assert trace entries
- `generators/proposal.py` (and 4 others): stub LLM, assert prompt structure

### Integration
- Full spec pipeline with stub LLM end-to-end
- Inject violations → assert auto-repair fires
- Legacy brief (no brief v2) → assert wrapper falls back

### Regression
- Existing finalize_spec integration tests must still pass
- spec_bundle.py file output format identical for downstream

### Manual
- Generate spec for golden idea 01_internal_forum, manually review:
  - Verbatim quotes present
  - No scope_out items mentioned positively
  - AC measurable
  - Trace map covers ≥80% assertions

---

## Cost Analysis

Per spec generation:

| Stage | Calls | Tokens |
|---|---|---|
| 1. Planner | 1 | brief 2k in, outlines 1k out |
| 2. Generators (parallel) | 5 | each: 3k in, 1-2k out |
| 3. Grounding rule | 0 | 0 |
| 3. Grounding LLM | 1 | 5k in, 0.5k out |
| 4. Repair (worst case) | 5 | 3k in, 1.5k out per section |
| 5. Trace map | 0 | 0 (parsing) |

Total worst-case: ~12 calls, ~50k tokens. Best-case (no repair): ~7 calls, ~25k tokens.

Current: 1 call, ~5k tokens.

Cost: +5-10x. With Sonnet 4.6, ~$0.30-0.60/spec. Acceptable for 1-shot Phase 1 cost.

---

## Configuration

```python
@dataclass
class SpecPipelineConfig:
    parallel_sections: bool = True
    max_repair_iterations: int = 1
    max_repair_calls: int = 5
    grounding_llm_check: bool = True
    fail_on_violations: bool = False     # if True, abort instead of log
    require_trace_map: bool = True
    section_max_words: dict = field(default_factory=lambda: {
        "proposal": 500, "design": 1500, "functional": 1200,
        "non_functional": 700, "acceptance_criteria": 1000,
    })
```

---

## Build Order

| Slice | Đầu ra | Test |
|---|---|---|
| **SP1** | `planner.py` (rule-based outlines) + 5 SectionRules constants | unit per section |
| **SP2** | `generators/proposal.py` + `generators/functional.py` | stub LLM integration |
| **SP3** | Remaining 3 generators (design, non_func, AC) | stub integration |
| **SP4** | Parallel ThreadPoolExecutor orchestration | timing test (parallel < serial) |
| **SP5** | `grounding.py` G1-G4 rule checks | unit + integration |
| **SP6** | Repair stage (1 retry per section) | integration |
| **SP7** | Grounding LLM check G5 | unit stub + integration |
| **SP8** | `tracer.py` + trace map artifact | unit parse |
| **SP9** | `finalize_spec()` wrapper backward compat | regression existing tests |
| **SP10** | Trace map UI in Gate review (link from spec) | manual |

SP1-SP4 = MVP usable (no grounding). SP5-SP7 = quality. SP8-SP10 = traceability + integration.

---

## Open Questions

1. **Granularity of inline markers:** mỗi assertion 1 marker hay paragraph-level? Trade-off: granular = more useful trace, more noise in spec text. Recommend paragraph-level default, sentence-level only for verbatim quotes.

2. **Trace map for sections without obvious source:** "Architecture overview" in design.md là synthesis, không có 1 brief field tương ứng. Trace as `{type: synthesis, sources: [<list of contributing fields>]}`?

3. **Section length enforcement:** hard cap (truncate) hay soft warn? Recommend warn + log, không truncate (user có thể edit sau).

4. **Repair feedback loop:** nếu repair tạo violation mới khác → max_repair=1 means we ship with new violation. Cần track violation delta để alert.

5. **Spec section ordering:** hiện cố định 5 file. Có nên dynamic theo project type sau này (typed templates)? Defer.

6. **Diagram generation:** design section thường cần architecture diagram. Out of scope, nhưng spec phải có placeholder section "Diagram (TBD)" để verification thấy gap?

---

## Out of Scope (deferred)

- Auto-generate Mermaid / PlantUML diagrams
- Custom spec templates per project type
- Spec version diff (when re-generating, show what changed)
- Cross-spec consistency check across multiple runs in same project
- Localization (Vi/En spec output)
- PDF / HTML export
- AI-generated cover image (😅)
