# Design Spec: Debate Engine + Gate 1 Redesign + Supporting Systems

**Date:** 2026-03-30
**Status:** Approved
**Scope:** Items 1–6 of workflow-v2 gap analysis

---

## Overview

Implements the remaining components to bring the system to full workflow-v2 compliance.
The current implementation (normalize → brief edit → spec bundle → task graph → execution)
is replaced by a richer pipeline with AI debate, structured approval gates, rule injection,
and Beads audit integration.

**Core architectural decision:** Pipeline splits at Gate 1. Python handles computation;
a Claude Code Skill handles the Gate 1 conversation.

---

## Architecture: Split Pipeline

```
[Python Phase A]  normalize → question gen → debate → DEBATE_REPORT artifact
                                                            ↓ run.status = PAUSED_FOR_GATE1
[Gate 1 Skill]    /review-debate → state machine → approved_answers + decision_log artifacts
                                                            ↓ run.status = GATE1_APPROVED
[Python Phase B]  finalize_spec → task_graph → Gate 2 → beads_sync → execution (+ rules)
```

Bridge: Skill calls `finalize_gate1(run_id, decisions, storage_root)` which writes artifacts
and updates `runs.status`. Phase B reads `run_id` from CLI arg, queries DB for artifact paths.

---

## Component 1: Debate Engine

### File Structure

```
src/ai_dev_system/debate/
├── __init__.py
├── questions.py    # generate_questions()
├── agents.py       # AgentRegistry, AGENT_PROMPTS
├── rounds.py       # run_debate_round()
├── engine.py       # run_debate()
└── report.py       # DebateReport, QuestionDebateResult, RoundResult dataclasses
```

### Data Contracts

```python
@dataclass
class Question:
    id: str                   # "Q1", "Q2" ...
    text: str
    classification: Literal["REQUIRED", "STRATEGIC", "OPTIONAL"]
    domain: str               # "security", "backend", "product", "database", "qa"
    agent_a: str              # agent registry key
    agent_b: str

@dataclass
class RoundResult:
    round_number: int
    agent_a_position: str
    agent_b_position: str
    moderator_summary: str
    resolution_status: Literal[
        "RESOLVED", "RESOLVED_WITH_CAVEAT",
        "ESCALATE_TO_HUMAN", "NEED_MORE_EVIDENCE"
    ]
    confidence: float         # 0.0–1.0
    caveat: str | None

@dataclass
class QuestionDebateResult:
    question: Question
    rounds: list[RoundResult]
    final: RoundResult        # last round = authoritative verdict

@dataclass
class DebateReport:
    run_id: str
    brief: dict
    results: list[QuestionDebateResult]
    generated_at: str         # ISO UTC
```

### generate_questions(brief, llm_client) → list[Question]

Single LLM call with forced JSON schema output:

```
System: Analyst role — generate clarifying questions needed to write a complete spec.
User:   brief JSON
Output: [{ "id", "text", "classification", "domain", "agent_a", "agent_b" }, ...]
```

OPTIONAL questions are generated but skipped in debate (auto-RESOLVED with generic answer).

### Agent Registry

Six personas with ~200-word system prompts defining lens and debate style:

| Key | Lens |
|-----|------|
| `SecuritySpecialist` | Security, compliance, risk, threat model |
| `BackendArchitect` | Scalability, API design, patterns, performance |
| `DevOpsSpecialist` | Infrastructure, deployment, observability, ops cost |
| `ProductManager` | User value, simplicity, MVP scope, business fit |
| `DatabaseSpecialist` | Schema design, query patterns, data integrity |
| `QAEngineer` | Testability, coverage, edge cases, regression risk |

LLM selects the pair as part of `generate_questions()` structured output.
Each system prompt ends with: *"Argue your position concisely (max 150 words). Acknowledge
trade-offs. Do not repeat the other agent's points."*

### run_debate_round(question, round_num, prev_moderator_summary, llm_client) → RoundResult

Three sequential API calls:

**Call 1 — Agent A:**
```
system: AGENT_PROMPTS[question.agent_a]
user:   question.text
        + (prev_moderator_summary if round_num > 1 else "")
        + instruction: "Đưa ra / điều chỉnh quan điểm của bạn"
→ agent_a_position (string, max 150 words)
```

**Call 2 — Agent B:**
```
system: AGENT_PROMPTS[question.agent_b]
user:   question.text
        + agent_a_position
        + (prev_moderator_summary if round_num > 1 else "")
        + instruction: "Phản biện và đưa ra quan điểm riêng"
→ agent_b_position (string, max 150 words)
```

**Call 3 — Moderator:**
```
system: MODERATOR_PROMPT (neutral synthesis role)
user:   question.text + agent_a_position + agent_b_position
→ JSON: { status, confidence, summary, caveat }
```

Each call passes only: question + previous moderator summary (if round > 1) + counterpart's
current position. Full transcript is NOT passed to API calls — it is stored in
`QuestionDebateResult.rounds` for Gate 1 display only.

### run_debate(questions, llm_client) → DebateReport

```python
for q in questions:
    if q.classification == "OPTIONAL":
        # auto-RESOLVED, no API calls
        results.append(auto_resolve(q))
        continue
    prev_summary = None
    rounds = []
    for round_num in range(1, MAX_ROUNDS + 1):   # MAX_ROUNDS = 5
        result = run_debate_round(q, round_num, prev_summary, llm_client)
        rounds.append(result)
        if result.confidence >= 0.8:
            break
        prev_summary = result.moderator_summary
    results.append(QuestionDebateResult(question=q, rounds=rounds, final=rounds[-1]))
```

### New Artifact Type: DEBATE_REPORT

Added to `ARTIFACT_TYPE_TO_KEY`:
```python
"DEBATE_REPORT": "debate_report_id"
```

### New Run Status: PAUSED_FOR_GATE1

Added to `runs.status` valid values. Phase A ends with:
```python
run_repo.update_status(run_id, "PAUSED_FOR_GATE1")
```

---

## Component 2: Gate 1 Skill

### Skill File

`skills/review-debate.md` — invoked via `/review-debate` in Claude Code.

Reads `debate_report.json` from the DEBATE_REPORT artifact path (queried by run_id from DB).

### State Machine

```
PRESENT → COLLECT_FORCED → COLLECT_CONSENSUS → CONFIRM → DONE
                                  ↑                 |
                                  └─────────────────┘  (if user wants to revise)
```

**PRESENT:** Render debate report. ESCALATE_TO_HUMAN items displayed first with full
agent positions + moderator summary + caveat. RESOLVED items listed compactly at the end.
Single message, no per-question prompting.

**COLLECT_FORCED:** Track `forced_pending: dict[str, None]` for each ESCALATE_TO_HUMAN question.
Parse any input form:

| Input | Parsed as |
|-------|-----------|
| `"Q6 đồng ý moderator"` | `APPROVED_MODERATOR` |
| `"Q6: dùng JWT"` | `OVERRIDE("dùng JWT")` |
| `"chọn option A"` | `APPROVED_AGENT_A` |
| `"approve all"` (FORCED pending) | **Blocked** — explain why |
| Ambiguous | Clarify immediately before recording |

Reminder appended to every response while any FORCED item is pending.

**COLLECT_CONSENSUS:** After all FORCED resolved, single prompt:
*"7 câu còn lại đã resolved. Approve tất cả, hay muốn xem/sửa câu nào?"*

Supports batch: `"approve all, Q4 dùng Vue"` → Q4=OVERRIDE, rest=APPROVED_CONSENSUS.

**CONFIRM:** Structured summary of all N decisions with resolution type markers
(✅ Consensus / ✏️ Override / 👤 Human decision). User confirms or requests revision.
On revision: return to COLLECT_CONSENSUS for specified question, then re-CONFIRM.

### finalize_gate1(run_id, decisions, storage_root, conn)

Called by Skill after CONFIRM:

```python
@dataclass
class Decision:
    question_id: str
    question_text: str
    classification: str
    resolution_type: Literal["CONSENSUS", "FORCED_HUMAN", "OVERRIDE"]
    answer: str
    options_considered: list[str]
    rationale: str            # user explanation if OVERRIDE, else ""

def finalize_gate1(run_id, decisions, storage_root, conn) -> tuple[str, str]:
    """Write approved_answers + decision_log artifacts. Returns (aa_id, dl_id)."""
    # write approved_answers.json: {"Q1": "answer", ...}
    # write decision_log.json: {"run_id": ..., "decisions": [...], "confirmed_at": ...}
    # promote APPROVED_ANSWERS artifact
    # promote DECISION_LOG artifact
    # run_repo.update_status(run_id, "GATE1_APPROVED")
```

New artifact types: `APPROVED_ANSWERS`, `DECISION_LOG`.

---

## Component 3: finalize_spec (Spec Bundle Redesign)

Replaces `generate_spec_bundle(approved_brief)`.

### Interface

```python
def finalize_spec(
    approved_answers: dict,   # {"Q1": "answer", ...}
    run_id: str,
    conn,
    config: Config,
    llm_client,
) -> SpecBundle
```

### 5 Output Files

| File | Derived from |
|------|-------------|
| `proposal.md` | Problem statement + goals from approved answers |
| `design.md` | Architecture decisions (tech stack, patterns, auth) |
| `functional.md` | Features + user flows from REQUIRED/STRATEGIC answers |
| `non-functional.md` | Performance, security, scalability constraints |
| `acceptance-criteria.md` | Success signals + done definitions |

Single LLM call: `approved_answers` JSON → 5-section structured output.
LLM writes coherent prose from decisions, not template substitution.

`generate_task_graph()` interface unchanged — still receives `spec_bundle_content` dict.

---

## Component 4: Rule Registry

### File Structure

```
src/ai_dev_system/rules/
├── __init__.py
├── registry.py
└── definitions/
    ├── tdd.yaml
    ├── code-review.yaml
    └── security.yaml
```

### Rule Format

```yaml
# definitions/tdd.yaml
name: tdd
applies_to:
  task_types: [code, implementation, test]
  tags: []                  # empty = wildcard (matches any tag combination)
file_rules:
  - docs/guides/tdd.md
skill_rules:
  - superpowers:test-driven-development
```

### RuleRegistry.match_rules(task) → RuleMatch

```python
@dataclass
class RuleMatch:
    file_rules: list[str]     # absolute file paths, content injected into agent context
    skill_rules: list[str]    # skill names printed as reminders to terminal

def match_rules(self, task: dict) -> RuleMatch:
    # rule matches if task.task_type in rule.applies_to.task_types
    #               OR any(tag in rule.applies_to.tags for tag in task.get("tags", []))
    # (empty tags in rule = match all tasks of matching type)
```

Called in `worker.py` before agent execution. Match results logged to `events` table
as `RULES_APPLIED` event. `skill_rules` printed to terminal as non-blocking reminders.

---

## Component 5: Beads Integration

### beads_sync(run_id, graph, conn)

Called in Phase B after Gate 2 approval, before `run_execution()`.

```python
def beads_sync(run_id: str, graph: dict, conn) -> None:
    tasks = topological_sort(graph["tasks"])
    for task in tasks:
        result = subprocess.run(
            ["bd", "create", task["id"], "--title", task["objective"], "--status", "pending"],
            capture_output=True
        )
        if result.returncode != 0 and "already exists" not in result.stderr.decode():
            logger.warning("beads_sync: bd create failed for %s: %s", task["id"], result.stderr)

    for task in graph["tasks"]:
        for dep in task.get("deps", []):
            subprocess.run(["bd", "dep", "add", task["id"], dep], capture_output=True)
```

- `bd` not in PATH → log warning, skip entirely (non-blocking)
- `bd create` for existing ID → ignored (idempotent by Beads convention)
- Errors logged to `events` table as `BEADS_SYNC_WARNING`, never raise

---

## Pipeline Entry Points

### Phase A: run_debate_pipeline(raw_idea, config, conn, project_id, llm_client)

```python
# normalize_idea() → brief
# generate_questions(brief, llm_client) → questions
# run_debate(questions, llm_client) → debate_report
# promote DEBATE_REPORT artifact
# run_repo.update_status(run_id, "PAUSED_FOR_GATE1")
# return DebatePipelineResult(run_id, debate_report, artifact_id)
```

### Phase B: run_spec_pipeline(run_id, config, conn_factory, gate2_io, agent, llm_client)

```python
# load approved_answers from APPROVED_ANSWERS artifact
# finalize_spec(approved_answers, ...) → spec_bundle
# generate_task_graph(spec_bundle, ...) → task_graph
# run_gate_2(task_graph, gate2_io) → approved
# beads_sync(run_id, approved_graph, conn)
# run_execution(run_id, graph_artifact_id, config, agent) → ExecutionResult
```

---

## New Artifact Types Summary

| Type | Key in current_artifacts | Phase |
|------|--------------------------|-------|
| `DEBATE_REPORT` | `debate_report_id` | A |
| `APPROVED_ANSWERS` | `approved_answers_id` | Gate 1 |
| `DECISION_LOG` | `decision_log_id` | Gate 1 |

Existing artifact types (`INITIAL_BRIEF`, `APPROVED_BRIEF`, `SPEC_BUNDLE`, `TASK_GRAPH_GENERATED`,
`TASK_GRAPH_APPROVED`) remain unchanged. `APPROVED_BRIEF` is superseded by `APPROVED_ANSWERS`
as the primary brief artifact — the existing field is kept for backward compatibility.

---

## New Run Statuses

| Status | Set by | Meaning |
|--------|--------|---------|
| `PAUSED_FOR_GATE1` | Phase A end | Waiting for Skill Gate 1 |
| `GATE1_APPROVED` | `finalize_gate1()` | Gate 1 complete, Phase B can start |

---

## Testing Strategy

**Unit tests (no DB, no LLM):**
- `debate/test_questions.py` — question parsing, classification logic
- `debate/test_rounds.py` — round orchestration with stub LLM client
- `debate/test_engine.py` — stop conditions (confidence ≥ 0.8, max rounds, OPTIONAL skip)
- `rules/test_registry.py` — YAML loading, match logic (type match, tag match, wildcard)

**Integration tests (DB, stub LLM):**
- `test_debate_pipeline.py` — Phase A end-to-end: normalize → debate → PAUSED_FOR_GATE1
- `test_finalize_gate1.py` — artifact creation, status transition to GATE1_APPROVED
- `test_spec_pipeline_phase_b.py` — Phase B: approved_answers → spec → task_graph → execution
- `test_beads_sync.py` — subprocess mock, idempotency, graceful skip when bd absent

**Stub LLM client:**
```python
class StubLLMClient:
    def complete(self, system, user, response_format=None) -> str:
        # returns deterministic fixture data based on system prompt role
```

---

## Out of Scope

- Real-time streaming of debate rounds to UI
- Debate re-run (regenerate questions for same run)
- Gate 1 Skill `"xem Q3"` showing full per-round transcript (shows summary only)
- Beads error recovery (best-effort sync only)
