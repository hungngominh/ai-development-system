# Debate Engine + Gate 1 Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the full debate pipeline (Phase A + Gate 1 Skill + Phase B) with Rule Registry and Beads integration per the approved spec.

**Architecture:** Python Phase A (normalize → question gen → debate → DEBATE_REPORT artifact → PAUSED_AT_GATE_1), then Gate 1 Skill (`/review-debate`) collects human decisions and calls `finalize_gate1()`, then Python Phase B (finalize_spec → task_graph → Gate 2 → beads_sync → execution).

**Tech Stack:** Python 3.12, psycopg3, dataclasses, PyYAML, pytest, subprocess (beads), Claude Code skill (Markdown)

---

## Working Directory

All file paths are relative to the worktree root. This plan should be executed in a fresh git worktree branched off `main`.

---

## File Map

### New files
| File | Responsibility |
|------|---------------|
| `src/ai_dev_system/debate/__init__.py` | Package marker |
| `src/ai_dev_system/debate/report.py` | Dataclasses: Question, RoundResult, QuestionDebateResult, DebateReport |
| `src/ai_dev_system/debate/llm.py` | DebateLLMClient Protocol + StubDebateLLMClient |
| `src/ai_dev_system/debate/questions.py` | generate_questions() |
| `src/ai_dev_system/debate/agents.py` | AGENT_PROMPTS dict + MODERATOR_PROMPT |
| `src/ai_dev_system/debate/rounds.py` | run_debate_round() |
| `src/ai_dev_system/debate/engine.py` | run_debate() |
| `src/ai_dev_system/gate/gate1_bridge.py` | finalize_gate1() + Decision dataclass |
| `src/ai_dev_system/finalize_spec.py` | finalize_spec() |
| `src/ai_dev_system/rules/__init__.py` | Package marker |
| `src/ai_dev_system/rules/registry.py` | RuleRegistry, RuleMatch |
| `src/ai_dev_system/rules/definitions/tdd.yaml` | TDD rule |
| `src/ai_dev_system/rules/definitions/code-review.yaml` | Code review rule |
| `src/ai_dev_system/rules/definitions/security.yaml` | Security rule |
| `src/ai_dev_system/beads/__init__.py` | Package marker |
| `src/ai_dev_system/beads/sync.py` | beads_sync() |
| `src/ai_dev_system/debate_pipeline.py` | run_debate_pipeline() (Phase A) + run_phase_b_pipeline() (Phase B) |
| `skills/review-debate.md` | Gate 1 Skill invoked via /review-debate |
| `docs/schema/migrations/v3-debate-engine.sql` | Add RULES_APPLIED, BEADS_SYNC_WARNING event types |
| `tests/unit/debate/test_report.py` | Dataclass construction + auto_resolve |
| `tests/unit/debate/test_questions.py` | Question parsing, classification |
| `tests/unit/debate/test_rounds.py` | Round orchestration with stub LLM |
| `tests/unit/debate/test_engine.py` | Stop conditions |
| `tests/unit/rules/test_registry.py` | YAML loading, match logic |
| `tests/integration/test_debate_pipeline.py` | Phase A end-to-end |
| `tests/integration/test_finalize_gate1.py` | Artifact creation, status transition |
| `tests/integration/test_beads_sync.py` | Subprocess mock, idempotency |

### Modified files
| File | Change |
|------|--------|
| `src/ai_dev_system/db/repos/runs.py` | Add `update_status()` method |
| `src/ai_dev_system/engine/worker.py` | Inject RuleRegistry before agent execution |

---

## Task 1: v3 Schema Migration

**Files:**
- Create: `docs/schema/migrations/v3-debate-engine.sql`

- [ ] **Step 1: Write the migration file**

```sql
-- v3-debate-engine.sql
-- Adds event types used by Debate Engine and Rule Registry.
-- Safe to run on a DB that already has the base schema (control-layer-schema.sql)
-- and v2-execution-runner.sql applied.

ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'RULES_APPLIED';
ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'BEADS_SYNC_WARNING';
```

- [ ] **Step 2: Apply the migration**

```bash
psql $DATABASE_URL -f docs/schema/migrations/v3-debate-engine.sql
```

Expected: `ALTER TYPE` (twice, no errors)

- [ ] **Step 3: Commit**

```bash
git add docs/schema/migrations/v3-debate-engine.sql
git commit -m "feat(schema): add RULES_APPLIED and BEADS_SYNC_WARNING event types (v3)"
```

---

## Task 2: RunRepo.update_status()

**Files:**
- Modify: `src/ai_dev_system/db/repos/runs.py`
- Test: `tests/unit/test_run_repo.py` (create or extend existing)

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_run_repo.py` (create file if it doesn't exist):

```python
def test_update_status_changes_run_status(db_conn, sample_run_id):
    repo = RunRepo(db_conn)
    repo.update_status(sample_run_id, "PAUSED_AT_GATE_1")
    row = db_conn.execute(
        "SELECT status FROM runs WHERE run_id = %s", (sample_run_id,)
    ).fetchone()
    assert row["status"] == "PAUSED_AT_GATE_1"
```

- [ ] **Step 2: Run to confirm it fails**

```bash
pytest tests/unit/test_run_repo.py::test_update_status_changes_run_status -v
```

Expected: `AttributeError: 'RunRepo' object has no attribute 'update_status'`

- [ ] **Step 3: Implement update_status()**

Add to `src/ai_dev_system/db/repos/runs.py` (after `update_current_artifact`):

```python
def update_status(self, run_id: str, status: str) -> None:
    self.conn.execute("""
        UPDATE runs SET status = %s, last_activity_at = now() WHERE run_id = %s
    """, (status, run_id))
```

- [ ] **Step 4: Run test to confirm it passes**

```bash
pytest tests/unit/test_run_repo.py::test_update_status_changes_run_status -v
```

Expected: `PASSED`

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/db/repos/runs.py tests/unit/test_run_repo.py
git commit -m "feat(db): add RunRepo.update_status()"
```

---

## Task 3: Debate Dataclasses

**Files:**
- Create: `src/ai_dev_system/debate/__init__.py`
- Create: `src/ai_dev_system/debate/report.py`
- Test: `tests/unit/debate/test_report.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/debate/__init__.py` (empty), then `tests/unit/debate/test_report.py`:

```python
from ai_dev_system.debate.report import (
    Question, RoundResult, QuestionDebateResult, DebateReport, auto_resolve
)

def test_question_construction():
    q = Question(id="Q1", text="Use JWT?", classification="REQUIRED",
                 domain="security", agent_a="SecuritySpecialist", agent_b="BackendArchitect")
    assert q.id == "Q1"
    assert q.classification == "REQUIRED"

def test_round_result_construction():
    r = RoundResult(round_number=1, agent_a_position="Use JWT",
                    agent_b_position="Use sessions", moderator_summary="JWT preferred",
                    resolution_status="RESOLVED", confidence=0.9, caveat=None)
    assert r.confidence == 0.9

def test_auto_resolve_optional():
    q = Question(id="Q5", text="Color scheme?", classification="OPTIONAL",
                 domain="product", agent_a="ProductManager", agent_b="QAEngineer")
    result = auto_resolve(q)
    assert result.final.resolution_status == "RESOLVED"
    assert result.final.confidence == 1.0
    assert len(result.rounds) == 1

def test_debate_report_escalated_and_resolved():
    q1 = Question(id="Q1", text="Auth?", classification="REQUIRED",
                  domain="security", agent_a="SecuritySpecialist", agent_b="BackendArchitect")
    r1 = RoundResult(1, "JWT", "Sessions", "JWT wins", "ESCALATE_TO_HUMAN", 0.4, None)
    qdr1 = QuestionDebateResult(question=q1, rounds=[r1], final=r1)

    q2 = Question(id="Q2", text="DB?", classification="STRATEGIC",
                  domain="database", agent_a="DatabaseSpecialist", agent_b="BackendArchitect")
    r2 = RoundResult(1, "Postgres", "MySQL", "Postgres", "RESOLVED", 0.95, None)
    qdr2 = QuestionDebateResult(question=q2, rounds=[r2], final=r2)

    report = DebateReport(run_id="r1", brief={"raw_idea": "x"},
                          results=[qdr1, qdr2], generated_at="2026-03-30T00:00:00Z")
    escalated = [r for r in report.results if r.final.resolution_status == "ESCALATE_TO_HUMAN"]
    resolved = [r for r in report.results if r.final.resolution_status != "ESCALATE_TO_HUMAN"]
    assert len(escalated) == 1
    assert len(resolved) == 1
```

- [ ] **Step 2: Run to confirm it fails**

```bash
pytest tests/unit/debate/test_report.py -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Create package markers**

`src/ai_dev_system/debate/__init__.py` — empty file.

- [ ] **Step 4: Implement report.py**

```python
# src/ai_dev_system/debate/report.py
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class Question:
    id: str
    text: str
    classification: Literal["REQUIRED", "STRATEGIC", "OPTIONAL"]
    domain: str
    agent_a: str
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
    confidence: float
    caveat: str | None


@dataclass
class QuestionDebateResult:
    question: Question
    rounds: list[RoundResult]
    final: RoundResult


@dataclass
class DebateReport:
    run_id: str
    brief: dict
    results: list[QuestionDebateResult]
    generated_at: str  # ISO UTC


def auto_resolve(question: Question) -> QuestionDebateResult:
    """Auto-resolve OPTIONAL questions without LLM calls."""
    round_result = RoundResult(
        round_number=1,
        agent_a_position="",
        agent_b_position="",
        moderator_summary="Optional question auto-resolved.",
        resolution_status="RESOLVED",
        confidence=1.0,
        caveat=None,
    )
    return QuestionDebateResult(question=question, rounds=[round_result], final=round_result)
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/unit/debate/test_report.py -v
```

Expected: all 4 tests `PASSED`

- [ ] **Step 6: Commit**

```bash
git add src/ai_dev_system/debate/ tests/unit/debate/
git commit -m "feat(debate): add debate report dataclasses and auto_resolve"
```

---

## Task 4: DebateLLMClient Protocol + StubDebateLLMClient

**Files:**
- Create: `src/ai_dev_system/debate/llm.py`

The debate module needs a richer LLM interface than the existing `enricher.py` LLMClient (which only has `complete(prompt)`). We define a separate Protocol with `system` + `user` arguments.

- [ ] **Step 1: Write llm.py**

```python
# src/ai_dev_system/debate/llm.py
import json
from typing import Protocol


class DebateLLMClient(Protocol):
    def complete(self, system: str, user: str) -> str:
        """Call LLM with system + user prompt. Returns raw string response."""
        ...


class StubDebateLLMClient:
    """Deterministic stub for testing. Returns fixture JSON based on role keyword in system prompt."""

    def complete(self, system: str, user: str) -> str:
        system_lower = system.lower()
        if "moderator" in system_lower or "synthesis" in system_lower:
            return json.dumps({
                "status": "RESOLVED",
                "confidence": 0.9,
                "summary": "Both agents agree on the proposed approach.",
                "caveat": None,
            })
        if "generate" in system_lower and "question" in system_lower:
            return json.dumps([
                {
                    "id": "Q1",
                    "text": "Should authentication use JWT tokens?",
                    "classification": "REQUIRED",
                    "domain": "security",
                    "agent_a": "SecuritySpecialist",
                    "agent_b": "BackendArchitect",
                },
                {
                    "id": "Q2",
                    "text": "Which database engine?",
                    "classification": "STRATEGIC",
                    "domain": "database",
                    "agent_a": "DatabaseSpecialist",
                    "agent_b": "BackendArchitect",
                },
            ])
        if "finalize" in system_lower or "spec" in system_lower:
            return json.dumps({
                "proposal": "# Proposal\nThis system solves the stated problem.",
                "design": "# Design\nUse standard MVC patterns.",
                "functional": "# Functional Requirements\nCore CRUD operations.",
                "non_functional": "# Non-Functional\nResponse time under 200ms.",
                "acceptance_criteria": "# Acceptance Criteria\nAll tests pass.",
            })
        # Default: agent position
        return "This approach is preferred because it balances trade-offs effectively."
```

- [ ] **Step 2: Verify import works**

```bash
python -c "from ai_dev_system.debate.llm import StubDebateLLMClient; c = StubDebateLLMClient(); print(c.complete('moderator synthesis', 'Q1'))"
```

Expected: JSON string with `"status": "RESOLVED"`

- [ ] **Step 3: Commit**

```bash
git add src/ai_dev_system/debate/llm.py
git commit -m "feat(debate): add DebateLLMClient protocol and StubDebateLLMClient"
```

---

## Task 5: Agent Registry

**Files:**
- Create: `src/ai_dev_system/debate/agents.py`

No tests needed — this is a constants module. Verified implicitly by rounds tests.

- [ ] **Step 1: Write agents.py**

```python
# src/ai_dev_system/debate/agents.py

AGENT_PROMPTS: dict[str, str] = {
    "SecuritySpecialist": (
        "You are a Security Specialist. Your lens: security, compliance, risk, and threat modeling. "
        "Evaluate proposals for vulnerabilities, authentication weaknesses, data exposure, and regulatory risk. "
        "Argue your position concisely (max 150 words). Acknowledge trade-offs. "
        "Do not repeat the other agent's points."
    ),
    "BackendArchitect": (
        "You are a Backend Architect. Your lens: scalability, API design, patterns, and performance. "
        "Evaluate proposals for maintainability, system boundaries, and long-term extensibility. "
        "Argue your position concisely (max 150 words). Acknowledge trade-offs. "
        "Do not repeat the other agent's points."
    ),
    "DevOpsSpecialist": (
        "You are a DevOps Specialist. Your lens: infrastructure, deployment, observability, and operational cost. "
        "Evaluate proposals for deployment complexity, monitoring gaps, and ops burden. "
        "Argue your position concisely (max 150 words). Acknowledge trade-offs. "
        "Do not repeat the other agent's points."
    ),
    "ProductManager": (
        "You are a Product Manager. Your lens: user value, simplicity, MVP scope, and business fit. "
        "Evaluate proposals for user impact, feature scope creep, and time-to-market. "
        "Argue your position concisely (max 150 words). Acknowledge trade-offs. "
        "Do not repeat the other agent's points."
    ),
    "DatabaseSpecialist": (
        "You are a Database Specialist. Your lens: schema design, query patterns, and data integrity. "
        "Evaluate proposals for normalization, indexing strategy, and consistency guarantees. "
        "Argue your position concisely (max 150 words). Acknowledge trade-offs. "
        "Do not repeat the other agent's points."
    ),
    "QAEngineer": (
        "You are a QA Engineer. Your lens: testability, coverage, edge cases, and regression risk. "
        "Evaluate proposals for testability, observability of failures, and hidden edge cases. "
        "Argue your position concisely (max 150 words). Acknowledge trade-offs. "
        "Do not repeat the other agent's points."
    ),
}

MODERATOR_PROMPT = (
    "You are a neutral debate moderator. Given a question and two agent positions, "
    "synthesize a conclusion. Respond ONLY with a JSON object: "
    '{"status": "RESOLVED"|"RESOLVED_WITH_CAVEAT"|"ESCALATE_TO_HUMAN"|"NEED_MORE_EVIDENCE", '
    '"confidence": 0.0-1.0, "summary": "...", "caveat": "..." or null}. '
    "Use ESCALATE_TO_HUMAN when agents fundamentally disagree and the decision requires human judgment. "
    "Use RESOLVED_WITH_CAVEAT when there is a clear answer but with important caveats. "
    "confidence >= 0.8 means no further debate rounds are needed."
)

VALID_AGENT_KEYS = set(AGENT_PROMPTS.keys())
```

- [ ] **Step 2: Commit**

```bash
git add src/ai_dev_system/debate/agents.py
git commit -m "feat(debate): add agent registry with 6 personas and moderator prompt"
```

---

## Task 6: generate_questions()

**Files:**
- Create: `src/ai_dev_system/debate/questions.py`
- Test: `tests/unit/debate/test_questions.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/debate/test_questions.py
import json
import pytest
from ai_dev_system.debate.questions import generate_questions
from ai_dev_system.debate.llm import StubDebateLLMClient
from ai_dev_system.debate.report import Question

SAMPLE_BRIEF = {
    "raw_idea": "Build a task manager",
    "problem": "Teams lose track of tasks",
    "target_users": "Small teams",
    "goal": "Track tasks efficiently",
    "constraints": {"hard": ["GDPR"], "soft": []},
    "assumptions": [],
    "scope": {"type": "new_feature", "complexity_hint": "medium"},
    "success_signals": ["tasks tracked"],
}


def test_generate_questions_returns_list_of_questions():
    client = StubDebateLLMClient()
    questions = generate_questions(SAMPLE_BRIEF, client)
    assert isinstance(questions, list)
    assert len(questions) >= 1
    assert all(isinstance(q, Question) for q in questions)


def test_generate_questions_valid_classifications():
    client = StubDebateLLMClient()
    questions = generate_questions(SAMPLE_BRIEF, client)
    for q in questions:
        assert q.classification in ("REQUIRED", "STRATEGIC", "OPTIONAL")


def test_generate_questions_valid_agent_keys():
    from ai_dev_system.debate.agents import VALID_AGENT_KEYS
    client = StubDebateLLMClient()
    questions = generate_questions(SAMPLE_BRIEF, client)
    for q in questions:
        assert q.agent_a in VALID_AGENT_KEYS
        assert q.agent_b in VALID_AGENT_KEYS


def test_generate_questions_unique_ids():
    client = StubDebateLLMClient()
    questions = generate_questions(SAMPLE_BRIEF, client)
    ids = [q.id for q in questions]
    assert len(ids) == len(set(ids))
```

- [ ] **Step 2: Run to confirm it fails**

```bash
pytest tests/unit/debate/test_questions.py -v
```

Expected: `ModuleNotFoundError: No module named 'ai_dev_system.debate.questions'`

- [ ] **Step 3: Implement questions.py**

```python
# src/ai_dev_system/debate/questions.py
import json
from ai_dev_system.debate.report import Question
from ai_dev_system.debate.agents import VALID_AGENT_KEYS

SYSTEM_PROMPT = (
    "You are an analyst. Generate clarifying questions needed to write a complete technical spec "
    "for the given project brief. Return ONLY a JSON array. Each element: "
    '{"id": "Q1", "text": "...", "classification": "REQUIRED"|"STRATEGIC"|"OPTIONAL", '
    '"domain": "security"|"backend"|"product"|"database"|"qa", '
    '"agent_a": "<AgentKey>", "agent_b": "<AgentKey>"}. '
    "Valid agent keys: SecuritySpecialist, BackendArchitect, DevOpsSpecialist, "
    "ProductManager, DatabaseSpecialist, QAEngineer. "
    "REQUIRED = must answer to ship. STRATEGIC = important but has defaults. OPTIONAL = nice to have."
)


def generate_questions(brief: dict, llm_client) -> list[Question]:
    """Single LLM call: brief → list[Question]."""
    response = llm_client.complete(
        system=SYSTEM_PROMPT,
        user=json.dumps(brief, ensure_ascii=False),
    )
    raw = json.loads(response)
    questions = []
    for item in raw:
        agent_a = item["agent_a"]
        agent_b = item["agent_b"]
        if agent_a not in VALID_AGENT_KEYS:
            agent_a = "BackendArchitect"
        if agent_b not in VALID_AGENT_KEYS:
            agent_b = "ProductManager"
        questions.append(Question(
            id=item["id"],
            text=item["text"],
            classification=item["classification"],
            domain=item.get("domain", "backend"),
            agent_a=agent_a,
            agent_b=agent_b,
        ))
    return questions
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/debate/test_questions.py -v
```

Expected: all 4 `PASSED`

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/debate/questions.py tests/unit/debate/test_questions.py
git commit -m "feat(debate): add generate_questions() with LLM + validation"
```

---

## Task 7: run_debate_round()

**Files:**
- Create: `src/ai_dev_system/debate/rounds.py`
- Test: `tests/unit/debate/test_rounds.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/debate/test_rounds.py
import pytest
from ai_dev_system.debate.rounds import run_debate_round
from ai_dev_system.debate.report import Question, RoundResult
from ai_dev_system.debate.llm import StubDebateLLMClient

QUESTION = Question(
    id="Q1", text="Use JWT?", classification="REQUIRED",
    domain="security", agent_a="SecuritySpecialist", agent_b="BackendArchitect"
)


def test_run_debate_round_returns_round_result():
    client = StubDebateLLMClient()
    result = run_debate_round(QUESTION, round_num=1, prev_moderator_summary=None, llm_client=client)
    assert isinstance(result, RoundResult)
    assert result.round_number == 1
    assert result.agent_a_position != ""
    assert result.agent_b_position != ""
    assert result.moderator_summary != ""


def test_run_debate_round_valid_status():
    client = StubDebateLLMClient()
    result = run_debate_round(QUESTION, round_num=1, prev_moderator_summary=None, llm_client=client)
    assert result.resolution_status in (
        "RESOLVED", "RESOLVED_WITH_CAVEAT", "ESCALATE_TO_HUMAN", "NEED_MORE_EVIDENCE"
    )


def test_run_debate_round_confidence_in_range():
    client = StubDebateLLMClient()
    result = run_debate_round(QUESTION, round_num=1, prev_moderator_summary=None, llm_client=client)
    assert 0.0 <= result.confidence <= 1.0


def test_run_debate_round_2_includes_prev_summary():
    """Round 2 passes previous summary — stub still returns valid result."""
    client = StubDebateLLMClient()
    result = run_debate_round(QUESTION, round_num=2,
                              prev_moderator_summary="JWT appears stronger.",
                              llm_client=client)
    assert result.round_number == 2
```

- [ ] **Step 2: Run to confirm it fails**

```bash
pytest tests/unit/debate/test_rounds.py -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement rounds.py**

```python
# src/ai_dev_system/debate/rounds.py
import json
from ai_dev_system.debate.report import Question, RoundResult
from ai_dev_system.debate.agents import AGENT_PROMPTS, MODERATOR_PROMPT

_AGENT_A_INSTRUCTION = "Đưa ra / điều chỉnh quan điểm của bạn về câu hỏi sau."
_AGENT_B_INSTRUCTION = "Phản biện và đưa ra quan điểm riêng của bạn."


def run_debate_round(
    question: Question,
    round_num: int,
    prev_moderator_summary: str | None,
    llm_client,
) -> RoundResult:
    """Three sequential LLM calls: Agent A → Agent B → Moderator."""
    prev_context = f"\n\nTóm tắt vòng trước: {prev_moderator_summary}" if prev_moderator_summary else ""

    # Call 1: Agent A
    agent_a_user = f"{question.text}{prev_context}\n\n{_AGENT_A_INSTRUCTION}"
    agent_a_position = llm_client.complete(
        system=AGENT_PROMPTS[question.agent_a],
        user=agent_a_user,
    )

    # Call 2: Agent B
    agent_b_user = (
        f"{question.text}\n\nQuan điểm của {question.agent_a}: {agent_a_position}"
        f"{prev_context}\n\n{_AGENT_B_INSTRUCTION}"
    )
    agent_b_position = llm_client.complete(
        system=AGENT_PROMPTS[question.agent_b],
        user=agent_b_user,
    )

    # Call 3: Moderator → JSON
    moderator_user = (
        f"Câu hỏi: {question.text}\n\n"
        f"{question.agent_a}: {agent_a_position}\n\n"
        f"{question.agent_b}: {agent_b_position}"
    )
    moderator_raw = llm_client.complete(system=MODERATOR_PROMPT, user=moderator_user)

    try:
        verdict = json.loads(moderator_raw)
    except json.JSONDecodeError:
        verdict = {
            "status": "NEED_MORE_EVIDENCE",
            "confidence": 0.0,
            "summary": moderator_raw,
            "caveat": "Moderator response was not valid JSON.",
        }

    return RoundResult(
        round_number=round_num,
        agent_a_position=agent_a_position,
        agent_b_position=agent_b_position,
        moderator_summary=verdict.get("summary", ""),
        resolution_status=verdict.get("status", "NEED_MORE_EVIDENCE"),
        confidence=float(verdict.get("confidence", 0.0)),
        caveat=verdict.get("caveat"),
    )
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/debate/test_rounds.py -v
```

Expected: all 4 `PASSED`

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/debate/rounds.py tests/unit/debate/test_rounds.py
git commit -m "feat(debate): add run_debate_round() with 3-call LLM orchestration"
```

---

## Task 8: run_debate() Engine

**Files:**
- Create: `src/ai_dev_system/debate/engine.py`
- Test: `tests/unit/debate/test_engine.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/debate/test_engine.py
from datetime import datetime
import pytest
from ai_dev_system.debate.engine import run_debate
from ai_dev_system.debate.report import Question, DebateReport
from ai_dev_system.debate.llm import StubDebateLLMClient

REQUIRED_Q = Question(
    id="Q1", text="Auth?", classification="REQUIRED",
    domain="security", agent_a="SecuritySpecialist", agent_b="BackendArchitect"
)
OPTIONAL_Q = Question(
    id="Q2", text="Color?", classification="OPTIONAL",
    domain="product", agent_a="ProductManager", agent_b="QAEngineer"
)
STRATEGIC_Q = Question(
    id="Q3", text="DB engine?", classification="STRATEGIC",
    domain="database", agent_a="DatabaseSpecialist", agent_b="BackendArchitect"
)


def test_run_debate_returns_debate_report():
    client = StubDebateLLMClient()
    report = run_debate([REQUIRED_Q], client, run_id="r1", brief={})
    assert isinstance(report, DebateReport)
    assert report.run_id == "r1"
    assert len(report.results) == 1


def test_optional_questions_auto_resolved_no_rounds():
    client = StubDebateLLMClient()
    report = run_debate([OPTIONAL_Q], client, run_id="r1", brief={})
    result = report.results[0]
    assert result.final.resolution_status == "RESOLVED"
    assert result.final.confidence == 1.0
    # auto_resolve uses round_number=1 with empty positions
    assert result.rounds[0].agent_a_position == ""


def test_stop_on_high_confidence():
    """Stub returns confidence=0.9 → should stop after 1 round."""
    client = StubDebateLLMClient()
    report = run_debate([REQUIRED_Q], client, run_id="r1", brief={})
    result = report.results[0]
    assert len(result.rounds) == 1  # stopped early


def test_all_three_classifications():
    client = StubDebateLLMClient()
    report = run_debate([REQUIRED_Q, OPTIONAL_Q, STRATEGIC_Q], client, run_id="r1", brief={})
    assert len(report.results) == 3


def test_generated_at_is_iso_utc():
    client = StubDebateLLMClient()
    report = run_debate([REQUIRED_Q], client, run_id="r1", brief={})
    # Should parse without error
    datetime.fromisoformat(report.generated_at.replace("Z", "+00:00"))
```

- [ ] **Step 2: Run to confirm it fails**

```bash
pytest tests/unit/debate/test_engine.py -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement engine.py**

```python
# src/ai_dev_system/debate/engine.py
from datetime import datetime, timezone
from ai_dev_system.debate.report import Question, QuestionDebateResult, DebateReport, auto_resolve
from ai_dev_system.debate.rounds import run_debate_round

MAX_ROUNDS = 5
CONFIDENCE_THRESHOLD = 0.8


def run_debate(
    questions: list[Question],
    llm_client,
    run_id: str,
    brief: dict,
) -> DebateReport:
    """Run debate for all questions. OPTIONAL questions are auto-resolved."""
    results: list[QuestionDebateResult] = []

    for q in questions:
        if q.classification == "OPTIONAL":
            results.append(auto_resolve(q))
            continue

        prev_summary = None
        rounds = []
        for round_num in range(1, MAX_ROUNDS + 1):
            result = run_debate_round(q, round_num, prev_summary, llm_client)
            rounds.append(result)
            if result.confidence >= CONFIDENCE_THRESHOLD:
                break
            prev_summary = result.moderator_summary

        results.append(QuestionDebateResult(question=q, rounds=rounds, final=rounds[-1]))

    return DebateReport(
        run_id=run_id,
        brief=brief,
        results=results,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/debate/test_engine.py -v
```

Expected: all 5 `PASSED`

- [ ] **Step 5: Run full debate unit suite**

```bash
pytest tests/unit/debate/ -v
```

Expected: all tests `PASSED`

- [ ] **Step 6: Commit**

```bash
git add src/ai_dev_system/debate/engine.py tests/unit/debate/test_engine.py
git commit -m "feat(debate): add run_debate() engine with confidence-based early stop"
```

---

## Task 9: Phase A Pipeline — run_debate_pipeline()

**Files:**
- Create: `src/ai_dev_system/debate_pipeline.py` (Phase A only for now, Phase B added in Task 15)
- Test: `tests/integration/test_debate_pipeline.py`

- [ ] **Step 1: Write the failing integration test**

```python
# tests/integration/test_debate_pipeline.py
import pytest
from ai_dev_system.debate_pipeline import run_debate_pipeline, DebatePipelineResult
from ai_dev_system.debate.llm import StubDebateLLMClient
from ai_dev_system.config import Config

RAW_IDEA = "Build a simple task manager for small teams."


def test_phase_a_returns_result(db_conn, tmp_storage, sample_project_id):
    config = Config(storage_root=tmp_storage, database_url="unused")
    client = StubDebateLLMClient()

    result = run_debate_pipeline(RAW_IDEA, config, db_conn, sample_project_id, client)

    assert isinstance(result, DebatePipelineResult)
    assert result.run_id is not None
    assert result.debate_report is not None
    assert result.artifact_id is not None


def test_phase_a_status_paused_at_gate_1(db_conn, tmp_storage, sample_project_id):
    config = Config(storage_root=tmp_storage, database_url="unused")
    client = StubDebateLLMClient()

    result = run_debate_pipeline(RAW_IDEA, config, db_conn, sample_project_id, client)

    row = db_conn.execute(
        "SELECT status FROM runs WHERE run_id = %s", (result.run_id,)
    ).fetchone()
    assert row["status"] == "PAUSED_AT_GATE_1"


def test_phase_a_debate_report_artifact_stored(db_conn, tmp_storage, sample_project_id):
    config = Config(storage_root=tmp_storage, database_url="unused")
    client = StubDebateLLMClient()

    result = run_debate_pipeline(RAW_IDEA, config, db_conn, sample_project_id, client)

    artifact = db_conn.execute(
        "SELECT artifact_type FROM artifacts WHERE artifact_id = %s", (result.artifact_id,)
    ).fetchone()
    assert artifact["artifact_type"] == "DEBATE_REPORT"
```

Note: `db_conn`, `tmp_storage`, and `sample_project_id` are pytest fixtures. Check `tests/conftest.py` for existing fixtures and reuse them. `sample_project_id` may need to be added if not present.

- [ ] **Step 2: Run to confirm it fails**

```bash
pytest tests/integration/test_debate_pipeline.py -v
```

Expected: `ModuleNotFoundError` or fixture errors

- [ ] **Step 3: Implement run_debate_pipeline() in debate_pipeline.py**

```python
# src/ai_dev_system/debate_pipeline.py
import json
import os
from dataclasses import dataclass

from ai_dev_system.config import Config
from ai_dev_system.normalize import normalize_idea
from ai_dev_system.debate.questions import generate_questions
from ai_dev_system.debate.engine import run_debate
from ai_dev_system.debate.report import DebateReport
from ai_dev_system.agents.base import PromotedOutput
from ai_dev_system.db.repos.runs import RunRepo
from ai_dev_system.db.repos.task_runs import TaskRunRepo
from ai_dev_system.db.repos.events import EventRepo
from ai_dev_system.storage.paths import build_temp_path
from ai_dev_system.storage.promote import promote_output


@dataclass
class DebatePipelineResult:
    run_id: str
    debate_report: DebateReport
    artifact_id: str


def run_debate_pipeline(
    raw_idea: str,
    config: Config,
    conn,
    project_id: str,
    llm_client,
) -> DebatePipelineResult:
    """Phase A: normalize → question gen → debate → DEBATE_REPORT artifact → PAUSED_AT_GATE_1."""
    run_repo = RunRepo(conn)
    task_run_repo = TaskRunRepo(conn)
    event_repo = EventRepo(conn)

    # Run created with status=RUNNING_PHASE_1A
    run_id = run_repo.create(project_id=project_id, pipeline_type="debate_pipeline")

    # Step 1: Normalize
    brief = normalize_idea(raw_idea)

    # Step 2: Generate questions
    task_run = task_run_repo.create_sync(run_id, task_type="generate_questions")
    task_run["input_artifact_ids"] = []
    event_repo.insert(run_id, "TASK_STARTED", "debate_pipeline", task_run["task_run_id"])
    questions = generate_questions(brief, llm_client)

    # Step 3: Run debate
    run_repo.update_status(run_id, "RUNNING_PHASE_1B")

    task_run = task_run_repo.create_sync(run_id, task_type="run_debate")
    task_run["input_artifact_ids"] = []
    event_repo.insert(run_id, "TASK_STARTED", "debate_pipeline", task_run["task_run_id"])
    debate_report = run_debate(questions, llm_client, run_id=run_id, brief=brief)

    # Step 4: Promote DEBATE_REPORT artifact
    temp_path = build_temp_path(
        config.storage_root, run_id,
        task_run["task_id"], task_run["attempt_number"]
    )
    os.makedirs(temp_path, exist_ok=True)
    report_dict = _debate_report_to_dict(debate_report)
    with open(os.path.join(temp_path, "debate_report.json"), "w", encoding="utf-8") as f:
        json.dump(report_dict, f, indent=2, ensure_ascii=False)

    promoted = PromotedOutput(
        name="debate_report",
        artifact_type="DEBATE_REPORT",
        description="AI debate report for Gate 1 review",
    )
    artifact_id = promote_output(conn, config, task_run, promoted, temp_path)

    # Step 5: Pause for Gate 1
    run_repo.update_status(run_id, "PAUSED_AT_GATE_1")

    return DebatePipelineResult(
        run_id=run_id,
        debate_report=debate_report,
        artifact_id=artifact_id,
    )


def _debate_report_to_dict(report: DebateReport) -> dict:
    """Serialize DebateReport to JSON-safe dict."""
    def round_to_dict(r):
        return {
            "round_number": r.round_number,
            "agent_a_position": r.agent_a_position,
            "agent_b_position": r.agent_b_position,
            "moderator_summary": r.moderator_summary,
            "resolution_status": r.resolution_status,
            "confidence": r.confidence,
            "caveat": r.caveat,
        }

    def qdr_to_dict(qdr):
        return {
            "question": {
                "id": qdr.question.id,
                "text": qdr.question.text,
                "classification": qdr.question.classification,
                "domain": qdr.question.domain,
                "agent_a": qdr.question.agent_a,
                "agent_b": qdr.question.agent_b,
            },
            "rounds": [round_to_dict(r) for r in qdr.rounds],
            "final": round_to_dict(qdr.final),
        }

    return {
        "run_id": report.run_id,
        "brief": report.brief,
        "results": [qdr_to_dict(r) for r in report.results],
        "generated_at": report.generated_at,
    }
```

- [ ] **Step 4: Run integration tests**

```bash
pytest tests/integration/test_debate_pipeline.py -v
```

Expected: all 3 `PASSED`

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/debate_pipeline.py tests/integration/test_debate_pipeline.py
git commit -m "feat(pipeline): add run_debate_pipeline() Phase A with DEBATE_REPORT artifact"
```

---

## Task 10: finalize_gate1() Bridge

**Files:**
- Create: `src/ai_dev_system/gate/gate1_bridge.py`
- Test: `tests/integration/test_finalize_gate1.py`

> Note: `src/ai_dev_system/gate/` already exists (contains `core.py`, `gate2.py`, `stub_gate2.py`, etc.). No `__init__.py` creation needed. `StubGate2IO` is importable from `gate.stub_gate2`.

- [ ] **Step 1: Write the failing integration test**

```python
# tests/integration/test_finalize_gate1.py
import pytest
from ai_dev_system.gate.gate1_bridge import finalize_gate1, Decision
from ai_dev_system.debate_pipeline import run_debate_pipeline
from ai_dev_system.debate.llm import StubDebateLLMClient
from ai_dev_system.config import Config

DECISIONS = [
    Decision(
        question_id="Q1",
        question_text="Use JWT?",
        classification="REQUIRED",
        resolution_type="FORCED_HUMAN",
        answer="Use JWT with short expiry.",
        options_considered=["JWT", "sessions"],
        rationale="Security team preference.",
    ),
    Decision(
        question_id="Q2",
        question_text="DB engine?",
        classification="STRATEGIC",
        resolution_type="CONSENSUS",
        answer="PostgreSQL",
        options_considered=["Postgres", "MySQL"],
        rationale="",
    ),
]


def test_finalize_gate1_creates_two_artifacts(db_conn, tmp_storage, sample_project_id):
    config = Config(storage_root=tmp_storage, database_url="unused")
    client = StubDebateLLMClient()

    phase_a = run_debate_pipeline("Build a task manager", config, db_conn, sample_project_id, client)

    aa_id, dl_id = finalize_gate1(phase_a.run_id, DECISIONS, tmp_storage, db_conn)

    assert aa_id is not None
    assert dl_id is not None
    assert aa_id != dl_id


def test_finalize_gate1_status_running_phase_1d(db_conn, tmp_storage, sample_project_id):
    config = Config(storage_root=tmp_storage, database_url="unused")
    client = StubDebateLLMClient()

    phase_a = run_debate_pipeline("Build a task manager", config, db_conn, sample_project_id, client)
    finalize_gate1(phase_a.run_id, DECISIONS, tmp_storage, db_conn)

    row = db_conn.execute(
        "SELECT status FROM runs WHERE run_id = %s", (phase_a.run_id,)
    ).fetchone()
    assert row["status"] == "RUNNING_PHASE_1D"


def test_finalize_gate1_artifact_types(db_conn, tmp_storage, sample_project_id):
    config = Config(storage_root=tmp_storage, database_url="unused")
    client = StubDebateLLMClient()

    phase_a = run_debate_pipeline("Build a task manager", config, db_conn, sample_project_id, client)
    aa_id, dl_id = finalize_gate1(phase_a.run_id, DECISIONS, tmp_storage, db_conn)

    aa_row = db_conn.execute(
        "SELECT artifact_type FROM artifacts WHERE artifact_id = %s", (aa_id,)
    ).fetchone()
    dl_row = db_conn.execute(
        "SELECT artifact_type FROM artifacts WHERE artifact_id = %s", (dl_id,)
    ).fetchone()
    assert aa_row["artifact_type"] == "APPROVED_ANSWERS"
    assert dl_row["artifact_type"] == "DECISION_LOG"
```

- [ ] **Step 2: Run to confirm it fails**

```bash
pytest tests/integration/test_finalize_gate1.py -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement gate1_bridge.py**

```python
# src/ai_dev_system/gate/gate1_bridge.py
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from ai_dev_system.config import Config
from ai_dev_system.agents.base import PromotedOutput
from ai_dev_system.db.repos.runs import RunRepo
from ai_dev_system.db.repos.task_runs import TaskRunRepo
from ai_dev_system.db.repos.events import EventRepo
from ai_dev_system.storage.paths import build_temp_path
from ai_dev_system.storage.promote import promote_output


@dataclass
class Decision:
    question_id: str
    question_text: str
    classification: str
    resolution_type: Literal["CONSENSUS", "FORCED_HUMAN", "OVERRIDE"]
    answer: str
    options_considered: list[str] = field(default_factory=list)
    rationale: str = ""


def finalize_gate1(
    run_id: str,
    decisions: list[Decision],
    storage_root: str,
    conn,
) -> tuple[str, str]:
    """Write APPROVED_ANSWERS + DECISION_LOG artifacts. Returns (aa_id, dl_id).
    Transitions run status to RUNNING_PHASE_1D.
    """
    config = Config(storage_root=storage_root, database_url="unused")
    task_run_repo = TaskRunRepo(conn)
    event_repo = EventRepo(conn)
    run_repo = RunRepo(conn)

    # Read debate_report_id for artifact lineage
    run_row = conn.execute(
        "SELECT current_artifacts FROM runs WHERE run_id = %s", (run_id,)
    ).fetchone()
    debate_report_id = run_row["current_artifacts"].get("debate_report_id")

    # --- Artifact 1: APPROVED_ANSWERS ---
    task_run_aa = task_run_repo.create_sync(run_id, task_type="gate1_approved_answers")
    task_run_aa["input_artifact_ids"] = [debate_report_id] if debate_report_id else []
    event_repo.insert(run_id, "TASK_STARTED", "gate1_skill", task_run_aa["task_run_id"])

    temp_aa = build_temp_path(
        config.storage_root, run_id,
        task_run_aa["task_id"], task_run_aa["attempt_number"]
    )
    os.makedirs(temp_aa, exist_ok=True)

    approved_answers = {d.question_id: d.answer for d in decisions}
    with open(os.path.join(temp_aa, "approved_answers.json"), "w", encoding="utf-8") as f:
        json.dump(approved_answers, f, indent=2, ensure_ascii=False)

    aa_id = promote_output(
        conn, config, task_run_aa,
        PromotedOutput("approved_answers", "APPROVED_ANSWERS", "Gate 1 approved answers"),
        temp_aa,
    )

    # --- Artifact 2: DECISION_LOG ---
    task_run_dl = task_run_repo.create_sync(run_id, task_type="gate1_decision_log")
    task_run_dl["input_artifact_ids"] = [debate_report_id] if debate_report_id else []
    event_repo.insert(run_id, "TASK_STARTED", "gate1_skill", task_run_dl["task_run_id"])

    temp_dl = build_temp_path(
        config.storage_root, run_id,
        task_run_dl["task_id"], task_run_dl["attempt_number"]
    )
    os.makedirs(temp_dl, exist_ok=True)

    decision_log = {
        "run_id": run_id,
        "confirmed_at": datetime.now(timezone.utc).isoformat(),
        "decisions": [
            {
                "question_id": d.question_id,
                "question_text": d.question_text,
                "classification": d.classification,
                "resolution_type": d.resolution_type,
                "answer": d.answer,
                "options_considered": d.options_considered,
                "rationale": d.rationale,
            }
            for d in decisions
        ],
    }
    with open(os.path.join(temp_dl, "decision_log.json"), "w", encoding="utf-8") as f:
        json.dump(decision_log, f, indent=2, ensure_ascii=False)

    dl_id = promote_output(
        conn, config, task_run_dl,
        PromotedOutput("decision_log", "DECISION_LOG", "Gate 1 decision log"),
        temp_dl,
    )

    # Transition: Gate 1 approved → Phase B ready
    run_repo.update_status(run_id, "RUNNING_PHASE_1D")

    return aa_id, dl_id
```

- [ ] **Step 4: Run integration tests**

```bash
pytest tests/integration/test_finalize_gate1.py -v
```

Expected: all 3 `PASSED`

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/gate/gate1_bridge.py tests/integration/test_finalize_gate1.py
git commit -m "feat(gate): add finalize_gate1() bridge — APPROVED_ANSWERS + DECISION_LOG artifacts"
```

---

## Task 11: Gate 1 Skill

**Files:**
- Create: `skills/review-debate.md`

This is a Claude Code skill (Markdown). It defines the state machine for `/review-debate`. No automated tests — this is a human-in-the-loop skill verified by manual use.

- [ ] **Step 1: Create the skills directory and write review-debate.md**

```markdown
# Review Debate

**Invoked via:** `/review-debate <run_id>`

You are the Gate 1 review skill. Your job: read the debate report for a run, collect human decisions on ESCALATE_TO_HUMAN questions, then call `finalize_gate1()` to write artifacts and advance the pipeline.

---

## Setup

On invocation, receive `run_id` from the argument. Query the DB for the DEBATE_REPORT artifact path:

```python
import json, psycopg
from ai_dev_system.db.connection import get_connection

conn = get_connection()
row = conn.execute(
    "SELECT current_artifacts FROM runs WHERE run_id = %s", (run_id,)
).fetchone()
debate_report_path = conn.execute(
    "SELECT content_ref FROM artifacts WHERE artifact_id = %s",
    (row["current_artifacts"]["debate_report_id"],)
).fetchone()["content_ref"]

with open(debate_report_path + "/debate_report.json") as f:
    report = json.load(f)
```

Separate results into:
- `forced`: items where `final.resolution_status == "ESCALATE_TO_HUMAN"`
- `consensus`: items where `final.resolution_status` is RESOLVED or RESOLVED_WITH_CAVEAT

---

## State Machine

### PRESENT

Render the debate report in a single message:

**Format:**
```
## Debate Report — Run <run_id>

### Cần quyết định của bạn (<N> câu)
**Q1 [REQUIRED · security]** Use JWT?
  - SecuritySpecialist: <agent_a_position>
  - BackendArchitect: <agent_b_position>
  - Moderator: <moderator_summary>
  *(Caveat: <caveat> nếu có)*

... (all ESCALATE_TO_HUMAN items)

---

### Đã resolved bởi AI (<M> câu)
- Q2 [STRATEGIC] DB engine → Postgres (confidence: 0.95)
- Q3 [REQUIRED] Auth method → JWT (confidence: 0.88)
...
```

Then transition to **COLLECT_FORCED**.

---

### COLLECT_FORCED

Track `forced_pending` = set of question IDs for all ESCALATE_TO_HUMAN items.

Accept any of these input patterns for each question:

| Input | Parsed as |
|-------|-----------|
| `"Q6 đồng ý moderator"` or `"Q6 approve moderator"` | `FORCED_HUMAN` with moderator summary as answer |
| `"Q6: dùng JWT"` or `"Q6 → dùng JWT"` | `OVERRIDE` with literal answer |
| `"Q6 chọn A"` or `"Q6 option A"` or `"Q6 agent A"` | `FORCED_HUMAN` with agent_a_position as answer |
| `"Q6 chọn B"` or `"Q6 option B"` or `"Q6 agent B"` | `FORCED_HUMAN` with agent_b_position as answer |
| `"approve all"` while forced_pending is non-empty | **Block** — reply: "Không thể approve all khi còn câu ESCALATE. Các câu sau cần quyết định riêng: Q6, Q9..." |
| Ambiguous | Ask clarifying question immediately. Do not record until confirmed. |

After parsing each response, confirm the recorded decision back to the user and list remaining pending items.

Append to **every response** while forced_pending is non-empty:
> ⚠️ Còn <N> câu chờ quyết định: <Q_IDs>

When forced_pending is empty → transition to **COLLECT_CONSENSUS**.

---

### COLLECT_CONSENSUS

Present a single prompt:

> "<M> câu còn lại đã được AI resolve. Approve tất cả, hay muốn xem/sửa câu nào?"

Accept:
- `"approve all"` → mark all consensus items as `CONSENSUS`
- `"approve all, Q4 dùng Vue"` → Q4=`OVERRIDE` với answer "dùng Vue", rest=`CONSENSUS`
- `"xem Q4"` → show full debate transcript for Q4, then re-prompt
- `"Q4 dùng Vue"` → Q4=`OVERRIDE`, then re-prompt for remaining

Transition to **CONFIRM** when all items have a decision.

---

### CONFIRM

Show a structured summary of all N decisions:

```
## Tóm tắt quyết định — <N> câu

✅ Q1 [REQUIRED] Use JWT? → "Use JWT with short expiry" (Consensus)
👤 Q6 [REQUIRED] Rate limiting? → "100 req/min per user" (Human decision)
✏️ Q4 [STRATEGIC] Frontend? → "dùng Vue" (Override)
...
```

Ask: "Xác nhận và ghi lại <N> quyết định này?"

- **Confirmed:** call `finalize_gate1()` (see below), then print run_id and artifact IDs.
- **Revise Q_N:** return to COLLECT_CONSENSUS for that question, then re-CONFIRM.

---

## Calling finalize_gate1()

After CONFIRM:

```python
from ai_dev_system.gate.gate1_bridge import finalize_gate1, Decision
from ai_dev_system.config import Config
import os

config = Config.from_env()
decisions = [
    Decision(
        question_id=q_id,
        question_text=...,
        classification=...,
        resolution_type="CONSENSUS" | "FORCED_HUMAN" | "OVERRIDE",
        answer=...,
        options_considered=[agent_a_pos, agent_b_pos],
        rationale=user_rationale_if_override,
    )
    for each decision
]

aa_id, dl_id = finalize_gate1(run_id, decisions, config.storage_root, conn)
print(f"Gate 1 complete. Run {run_id} → RUNNING_PHASE_1D")
print(f"  APPROVED_ANSWERS: {aa_id}")
print(f"  DECISION_LOG:     {dl_id}")
```

Phase B (`run_phase_b_pipeline`) can now be invoked with this `run_id`.
```

- [ ] **Step 2: Commit**

```bash
git add skills/review-debate.md
git commit -m "feat(skill): add Gate 1 review-debate skill with 4-state machine"
```

---

## Task 12: finalize_spec()

**Files:**
- Create: `src/ai_dev_system/finalize_spec.py`

`finalize_spec()` replaces `generate_spec_bundle()` for the debate pipeline. It takes `approved_answers` (dict from Gate 1) and produces a `SpecBundle` via a single LLM call.

- [ ] **Step 1: Write the test**

```python
# tests/unit/test_finalize_spec.py
import pytest
from pathlib import Path
from ai_dev_system.finalize_spec import finalize_spec
from ai_dev_system.spec_bundle import SpecBundle
from ai_dev_system.debate.llm import StubDebateLLMClient

APPROVED_ANSWERS = {
    "Q1": "Use JWT with short expiry (15 min access, 7 day refresh)",
    "Q2": "PostgreSQL with connection pooling",
    "Q3": "REST API with OpenAPI spec",
}


def test_finalize_spec_returns_spec_bundle(tmp_path):
    client = StubDebateLLMClient()
    bundle = finalize_spec(APPROVED_ANSWERS, "r1", client, output_dir=tmp_path)
    assert isinstance(bundle, SpecBundle)


def test_finalize_spec_writes_five_files(tmp_path):
    client = StubDebateLLMClient()
    bundle = finalize_spec(APPROVED_ANSWERS, "r1", client, output_dir=tmp_path)
    expected = {"proposal.md", "design.md", "functional.md", "non-functional.md", "acceptance-criteria.md"}
    assert set(bundle.files.keys()) == expected


def test_finalize_spec_files_nonempty(tmp_path):
    client = StubDebateLLMClient()
    bundle = finalize_spec(APPROVED_ANSWERS, "r1", client, output_dir=tmp_path)
    for name, path in bundle.files.items():
        assert path.exists(), f"{name} not written"
        assert path.stat().st_size > 0, f"{name} is empty"
```

- [ ] **Step 2: Run to confirm it fails**

```bash
pytest tests/unit/test_finalize_spec.py -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement finalize_spec.py**

```python
# src/ai_dev_system/finalize_spec.py
import json
from pathlib import Path
from ai_dev_system.spec_bundle import SpecBundle

SYSTEM_PROMPT = (
    "You are a technical writer generating a structured spec from approved design decisions. "
    "Given approved_answers (question_id → answer), write a complete spec in 5 sections. "
    "Return ONLY a JSON object with these exact keys: "
    '"proposal", "design", "functional", "non_functional", "acceptance_criteria". '
    "Each value is a Markdown string. Write coherent prose — not template substitution."
)

_FILE_MAP = {
    "proposal": "proposal.md",
    "design": "design.md",
    "functional": "functional.md",
    "non_functional": "non-functional.md",
    "acceptance_criteria": "acceptance-criteria.md",
}


def finalize_spec(
    approved_answers: dict,
    run_id: str,
    llm_client,
    output_dir: Path,
) -> SpecBundle:
    """Single LLM call: approved_answers → 5-file SpecBundle."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    response = llm_client.complete(
        system=SYSTEM_PROMPT,
        user=json.dumps({"run_id": run_id, "approved_answers": approved_answers}, ensure_ascii=False),
    )

    try:
        sections = json.loads(response)
    except json.JSONDecodeError:
        # Fallback: write raw response to proposal.md
        sections = {k: f"# {k}\n\n{response}" for k in _FILE_MAP}

    files: dict[str, Path] = {}
    for key, filename in _FILE_MAP.items():
        content = sections.get(key, f"# {filename}\n\n(Not generated)")
        path = output_dir / filename
        path.write_text(content, encoding="utf-8")
        files[filename] = path

    return SpecBundle(version=1, root_dir=output_dir, files=files)
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/unit/test_finalize_spec.py -v
```

Expected: all 3 `PASSED`

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/finalize_spec.py tests/unit/test_finalize_spec.py
git commit -m "feat: add finalize_spec() — LLM-driven 5-file spec bundle from approved answers"
```

---

## Task 13: Rule Registry

**Files:**
- Create: `src/ai_dev_system/rules/__init__.py`
- Create: `src/ai_dev_system/rules/registry.py`
- Create: `src/ai_dev_system/rules/definitions/tdd.yaml`
- Create: `src/ai_dev_system/rules/definitions/code-review.yaml`
- Create: `src/ai_dev_system/rules/definitions/security.yaml`
- Test: `tests/unit/rules/test_registry.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/rules/__init__.py` (empty), then:

```python
# tests/unit/rules/test_registry.py
import pytest
from pathlib import Path
from ai_dev_system.rules.registry import RuleRegistry, RuleMatch

RULES_DIR = Path(__file__).parents[3] / "src" / "ai_dev_system" / "rules" / "definitions"


def test_load_rules_finds_yaml_files():
    registry = RuleRegistry(rules_dir=RULES_DIR)
    assert len(registry.rules) >= 3


def test_match_code_task_returns_tdd_rule():
    registry = RuleRegistry(rules_dir=RULES_DIR)
    task = {"task_type": "code", "tags": []}
    match = registry.match_rules(task)
    assert isinstance(match, RuleMatch)
    assert any("tdd" in r.lower() for r in match.skill_rules)


def test_match_security_tag_returns_security_rule():
    registry = RuleRegistry(rules_dir=RULES_DIR)
    task = {"task_type": "review", "tags": ["security"]}
    match = registry.match_rules(task)
    assert any("security" in r.lower() for r in match.skill_rules + match.file_rules)


def test_match_unknown_task_returns_empty():
    registry = RuleRegistry(rules_dir=RULES_DIR)
    task = {"task_type": "planning", "tags": []}
    match = registry.match_rules(task)
    assert match.file_rules == []
    assert match.skill_rules == []


def test_rule_match_empty_tags_matches_any_type():
    """A rule with empty tags= matches all tasks of that task_type."""
    registry = RuleRegistry(rules_dir=RULES_DIR)
    task = {"task_type": "implementation", "tags": ["some-tag"]}
    match = registry.match_rules(task)
    assert isinstance(match, RuleMatch)
```

- [ ] **Step 2: Run to confirm it fails**

```bash
pytest tests/unit/rules/test_registry.py -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Write YAML definitions**

`src/ai_dev_system/rules/definitions/tdd.yaml`:
```yaml
name: tdd
applies_to:
  task_types: [code, implementation, test]
  tags: []
file_rules: []
skill_rules:
  - superpowers:test-driven-development
```

`src/ai_dev_system/rules/definitions/code-review.yaml`:
```yaml
name: code-review
applies_to:
  task_types: [review, code]
  tags: []
file_rules: []
skill_rules:
  - superpowers:requesting-code-review
```

`src/ai_dev_system/rules/definitions/security.yaml`:
```yaml
name: security
applies_to:
  task_types: [code, implementation, review]
  tags: [security, auth, authentication, authorization]
file_rules: []
skill_rules:
  - superpowers:systematic-debugging
```

- [ ] **Step 4: Implement registry.py**

```python
# src/ai_dev_system/rules/registry.py
from dataclasses import dataclass, field
from pathlib import Path
import yaml


@dataclass
class RuleMatch:
    file_rules: list[str] = field(default_factory=list)
    skill_rules: list[str] = field(default_factory=list)


class RuleRegistry:
    def __init__(self, rules_dir: Path | str):
        self.rules_dir = Path(rules_dir)
        self.rules = self._load_rules()

    def _load_rules(self) -> list[dict]:
        rules = []
        for yaml_file in sorted(self.rules_dir.glob("*.yaml")):
            with open(yaml_file, encoding="utf-8") as f:
                rules.append(yaml.safe_load(f))
        return rules

    def match_rules(self, task: dict) -> RuleMatch:
        """Return file_rules + skill_rules for this task.

        A rule matches if:
          - task.task_type is in rule.applies_to.task_types, OR
          - any task tag is in rule.applies_to.tags (non-empty tags only)
        Empty tags in rule = match all tasks of matching type.
        """
        task_type = task.get("task_type", "")
        task_tags = set(task.get("tags", []))

        file_rules: list[str] = []
        skill_rules: list[str] = []

        for rule in self.rules:
            applies = rule.get("applies_to", {})
            rule_types = set(applies.get("task_types", []))
            rule_tags = set(applies.get("tags", []))

            type_match = task_type in rule_types
            tag_match = bool(rule_tags and task_tags & rule_tags)

            if type_match or tag_match:
                file_rules.extend(rule.get("file_rules", []))
                skill_rules.extend(rule.get("skill_rules", []))

        return RuleMatch(file_rules=file_rules, skill_rules=skill_rules)
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/unit/rules/test_registry.py -v
```

Expected: all 5 `PASSED`

- [ ] **Step 6: Commit**

```bash
git add src/ai_dev_system/rules/ tests/unit/rules/
git commit -m "feat(rules): add RuleRegistry with YAML definitions (tdd, code-review, security)"
```

---

## Task 14: Rule Injection in worker.py

**Files:**
- Modify: `src/ai_dev_system/engine/worker.py`

Inject `RuleRegistry.match_rules()` before agent execution. Log `RULES_APPLIED` event. Print `skill_rules` to terminal.

- [ ] **Step 1: Read the current worker.py to find the agent execution call**

```bash
grep -n "agent.run\|execute_and_promote\|pickup_task" src/ai_dev_system/engine/worker.py | head -30
```

- [ ] **Step 2: Identify the insertion point**

Find where the agent is called inside `worker_loop`. It will look like:
```python
result = agent.run(task_id=..., output_path=..., ...)
```

- [ ] **Step 3: Add rule injection before agent.run()**

In `worker_loop()` (or `execute_and_promote()`), before `agent.run(...)`:

```python
from ai_dev_system.rules.registry import RuleRegistry
from pathlib import Path

# Build registry once at loop start (move to top of worker_loop or pass as arg)
_rules_dir = Path(__file__).parent.parent / "rules" / "definitions"
_rule_registry = RuleRegistry(rules_dir=_rules_dir)

# Before agent.run():
rule_match = _rule_registry.match_rules(task)
if rule_match.skill_rules or rule_match.file_rules:
    event_repo.insert(run_id, "RULES_APPLIED", "worker",
                      task_run_id=task["task_run_id"],
                      payload={"skill_rules": rule_match.skill_rules,
                               "file_rules": rule_match.file_rules})
    for skill in rule_match.skill_rules:
        print(f"[RULE] Apply skill: {skill}")
```

Note: read `worker.py` fully before editing to understand the exact structure. Place the registry instantiation outside the per-task loop for efficiency.

- [ ] **Step 4: Run existing worker tests to confirm no regressions**

```bash
pytest tests/unit/test_background_jobs.py tests/integration/test_worker_loop.py -v
```

Expected: all `PASSED`

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/engine/worker.py
git commit -m "feat(worker): inject RuleRegistry before agent execution, emit RULES_APPLIED event"
```

---

## Task 15: beads_sync()

**Files:**
- Create: `src/ai_dev_system/beads/__init__.py`
- Create: `src/ai_dev_system/beads/sync.py`
- Test: `tests/integration/test_beads_sync.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_beads_sync.py
import subprocess
from unittest.mock import patch, MagicMock
import pytest
from ai_dev_system.beads.sync import beads_sync

SAMPLE_GRAPH = {
    "tasks": [
        {"id": "T1", "objective": "Set up auth", "deps": []},
        {"id": "T2", "objective": "Build API", "deps": ["T1"]},
        {"id": "T3", "objective": "Write tests", "deps": ["T2"]},
    ]
}


def test_beads_sync_calls_bd_create_for_each_task(db_conn):
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr=b"")
        beads_sync("r1", SAMPLE_GRAPH, db_conn)
        create_calls = [c for c in mock_run.call_args_list
                        if c.args[0][1] == "create"]
        assert len(create_calls) == 3


def test_beads_sync_adds_deps(db_conn):
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stderr=b"")
        beads_sync("r1", SAMPLE_GRAPH, db_conn)
        dep_calls = [c for c in mock_run.call_args_list
                     if len(c.args[0]) > 1 and c.args[0][1] == "dep"]
        assert len(dep_calls) == 2  # T2→T1, T3→T2


def test_beads_sync_skips_when_bd_not_found(db_conn):
    """If bd is not in PATH, skip entirely — no exception raised."""
    with patch("subprocess.run", side_effect=FileNotFoundError("bd not found")):
        beads_sync("r1", SAMPLE_GRAPH, db_conn)  # should not raise


def test_beads_sync_logs_warning_on_nonzero_exit(db_conn):
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr=b"some error")
        beads_sync("r1", SAMPLE_GRAPH, db_conn)  # should not raise
        # Warning logged to events — check no exception raised
```

- [ ] **Step 2: Run to confirm it fails**

```bash
pytest tests/integration/test_beads_sync.py -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Implement sync.py**

```python
# src/ai_dev_system/beads/sync.py
import logging
import subprocess
from ai_dev_system.db.repos.events import EventRepo

logger = logging.getLogger(__name__)


def _topological_sort(tasks: list[dict]) -> list[dict]:
    """Simple Kahn's algorithm topological sort."""
    id_to_task = {t["id"]: t for t in tasks}
    in_degree = {t["id"]: 0 for t in tasks}
    for t in tasks:
        for dep in t.get("deps", []):
            if dep in in_degree:
                in_degree[t["id"]] += 1
    queue = [tid for tid, deg in in_degree.items() if deg == 0]
    result = []
    while queue:
        tid = queue.pop(0)
        result.append(id_to_task[tid])
        for t in tasks:
            if tid in t.get("deps", []):
                in_degree[t["id"]] -= 1
                if in_degree[t["id"]] == 0:
                    queue.append(t["id"])
    return result


def beads_sync(run_id: str, graph: dict, conn) -> None:
    """Sync task graph to Beads (bd CLI). Non-blocking: errors are logged, never raised."""
    event_repo = EventRepo(conn)
    tasks = _topological_sort(graph.get("tasks", []))

    def _run(cmd: list[str]) -> subprocess.CompletedProcess | None:
        try:
            return subprocess.run(cmd, capture_output=True)
        except FileNotFoundError:
            logger.warning("beads_sync: bd not found in PATH, skipping sync")
            return None

    for task in tasks:
        result = _run(["bd", "create", task["id"], "--title", task["objective"], "--status", "pending"])
        if result is None:
            return  # bd not available — skip all
        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace")
            if "already exists" not in stderr:
                logger.warning("beads_sync: bd create failed for %s: %s", task["id"], stderr)
                event_repo.insert(run_id, "BEADS_SYNC_WARNING", "system",
                                  payload={"task_id": task["id"], "stderr": stderr})

    for task in graph.get("tasks", []):
        for dep in task.get("deps", []):
            _run(["bd", "dep", "add", task["id"], dep])
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/integration/test_beads_sync.py -v
```

Expected: all 4 `PASSED`

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/beads/ tests/integration/test_beads_sync.py
git commit -m "feat(beads): add beads_sync() with graceful skip when bd absent"
```

---

## Task 16: Phase B Pipeline — run_phase_b_pipeline()

**Files:**
- Modify: `src/ai_dev_system/debate_pipeline.py` (add Phase B)

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_spec_pipeline_phase_b.py
import pytest
from unittest.mock import patch, MagicMock
from ai_dev_system.debate_pipeline import run_debate_pipeline, run_phase_b_pipeline
from ai_dev_system.debate.llm import StubDebateLLMClient
from ai_dev_system.gate.gate1_bridge import finalize_gate1, Decision
from ai_dev_system.gate.stub_gate2 import StubGate2IO
from ai_dev_system.config import Config

DECISIONS = [
    Decision(question_id="Q1", question_text="Auth?", classification="REQUIRED",
             resolution_type="CONSENSUS", answer="Use JWT", options_considered=[], rationale=""),
    Decision(question_id="Q2", question_text="DB?", classification="STRATEGIC",
             resolution_type="CONSENSUS", answer="PostgreSQL", options_considered=[], rationale=""),
]


def test_phase_b_runs_after_gate1(db_conn, tmp_storage, sample_project_id):
    config = Config(storage_root=tmp_storage, database_url="unused")
    client = StubDebateLLMClient()

    # Phase A
    phase_a = run_debate_pipeline("Build a task manager", config, db_conn, sample_project_id, client)

    # Gate 1
    finalize_gate1(phase_a.run_id, DECISIONS, tmp_storage, db_conn)

    # Phase B — conn_factory wraps the test connection
    with patch("subprocess.run") as mock_bd:
        mock_bd.return_value = MagicMock(returncode=0, stderr=b"")
        result = run_phase_b_pipeline(
            run_id=phase_a.run_id,
            config=config,
            conn_factory=lambda: db_conn,
            gate2_io=StubGate2IO(action="approve"),
            llm_client=client,
        )

    assert result.graph_artifact_id is not None
    # Confirm TASK_GRAPH_APPROVED artifact was stored
    artifact = db_conn.execute(
        "SELECT artifact_type FROM artifacts WHERE artifact_id = %s",
        (result.graph_artifact_id,)
    ).fetchone()
    assert artifact["artifact_type"] == "TASK_GRAPH_APPROVED"


def test_phase_b_asserts_running_phase_1d(db_conn, tmp_storage, sample_project_id):
    """Phase B should fail if run is not in RUNNING_PHASE_1D."""
    config = Config(storage_root=tmp_storage, database_url="unused")
    client = StubDebateLLMClient()

    phase_a = run_debate_pipeline("Build a task manager", config, db_conn, sample_project_id, client)
    # Do NOT call finalize_gate1 — run is still PAUSED_AT_GATE_1

    with pytest.raises(AssertionError, match="RUNNING_PHASE_1D"):
        run_phase_b_pipeline(
            run_id=phase_a.run_id,
            config=config,
            conn_factory=lambda: db_conn,
            gate2_io=StubGate2IO(action="approve"),
            llm_client=client,
        )
```

- [ ] **Step 2: Run to confirm it fails**

```bash
pytest tests/integration/test_spec_pipeline_phase_b.py -v
```

Expected: `ImportError` for `run_phase_b_pipeline`

- [ ] **Step 3: Add run_phase_b_pipeline() to debate_pipeline.py**

Add after `run_debate_pipeline()`:

```python
import json
from pathlib import Path
from ai_dev_system.finalize_spec import finalize_spec
from ai_dev_system.task_graph.generator import generate_task_graph
from ai_dev_system.gate.gate2 import run_gate_2
from ai_dev_system.beads.sync import beads_sync
from ai_dev_system.engine.runner import run_execution, ExecutionResult


@dataclass
class PhaseBResult:
    run_id: str
    graph_artifact_id: str
    execution_result: ExecutionResult | None = None


def run_phase_b_pipeline(
    run_id: str,
    config: Config,
    conn_factory,           # Callable[[], psycopg.Connection] — Phase B runs in a separate process
    gate2_io,
    llm_client,
    agent=None,
) -> PhaseBResult:
    """Phase B: approved_answers → finalize_spec → task_graph → Gate 2 → beads_sync → execution.

    Accepts conn_factory (not a live conn) because Phase B is invoked in a new process
    after the Gate 1 pause. In tests, pass `lambda: db_conn`.
    """
    conn = conn_factory()
    run_repo = RunRepo(conn)
    task_run_repo = TaskRunRepo(conn)
    event_repo = EventRepo(conn)

    # Guard: must be called after Gate 1
    row = conn.execute(
        "SELECT status, current_artifacts FROM runs WHERE run_id = %s", (run_id,)
    ).fetchone()
    assert row["status"] == "RUNNING_PHASE_1D", (
        f"Expected RUNNING_PHASE_1D, got {row['status']}"
    )

    # Load approved_answers from artifact
    aa_artifact_id = row["current_artifacts"]["approved_answers_id"]
    aa_artifact = conn.execute(
        "SELECT content_ref FROM artifacts WHERE artifact_id = %s", (aa_artifact_id,)
    ).fetchone()
    aa_path = os.path.join(aa_artifact["content_ref"], "approved_answers.json")
    with open(aa_path, encoding="utf-8") as f:
        approved_answers = json.load(f)

    # Step 1: finalize_spec
    task_run = task_run_repo.create_sync(run_id, task_type="finalize_spec")
    task_run["input_artifact_ids"] = [aa_artifact_id]
    event_repo.insert(run_id, "TASK_STARTED", "debate_pipeline", task_run["task_run_id"])

    temp_path = build_temp_path(
        config.storage_root, run_id, task_run["task_id"], task_run["attempt_number"]
    )
    bundle = finalize_spec(approved_answers, run_id, llm_client, output_dir=Path(temp_path))
    spec_content = {name: path.read_text(encoding="utf-8") for name, path in bundle.files.items()}

    promoted = PromotedOutput(name="spec_bundle", artifact_type="SPEC_BUNDLE",
                              description="5-file spec bundle from Gate 1 answers")
    spec_artifact_id = promote_output(conn, config, task_run, promoted, temp_path)

    # Refresh bundle root_dir after promotion
    spec_row = conn.execute(
        "SELECT content_ref FROM artifacts WHERE artifact_id = %s", (spec_artifact_id,)
    ).fetchone()
    bundle_root = Path(spec_row["content_ref"])
    spec_content = {
        name: (bundle_root / name).read_text(encoding="utf-8")
        for name in bundle.files
        if (bundle_root / name).exists()
    }

    # Step 2: generate_task_graph
    task_run_tg = task_run_repo.create_sync(run_id, task_type="generate_task_graph")
    task_run_tg["input_artifact_ids"] = [spec_artifact_id]
    event_repo.insert(run_id, "TASK_STARTED", "debate_pipeline", task_run_tg["task_run_id"])

    # generate_task_graph(spec_bundle_content, approved_brief, spec_artifact_id, llm=None)
    # approved_answers serves as approved_brief — same structural role, different source
    envelope = generate_task_graph(spec_content, approved_answers, spec_artifact_id, llm_client)
    temp_tg = _write_json_to_temp_debate(config, task_run_tg, envelope)
    promoted_tg = PromotedOutput(name="task_graph", artifact_type="TASK_GRAPH_GENERATED",
                                 description="Generated task graph")
    promote_output(conn, config, task_run_tg, promoted_tg, temp_tg)

    # Step 3: Gate 2
    task_run_g2 = task_run_repo.create_sync(run_id, task_type="task_graph_gate2")
    task_run_g2["input_artifact_ids"] = []
    event_repo.insert(run_id, "TASK_STARTED", "debate_pipeline", task_run_g2["task_run_id"])

    gate2_result = run_gate_2(envelope, gate2_io)
    if gate2_result.status == "rejected":
        task_run_repo.mark_failed(task_run_g2["task_run_id"], "EXECUTION_ERROR", "user_rejected")
        from ai_dev_system.pipeline import PipelineAborted
        raise PipelineAborted("User rejected task graph at Gate 2")

    temp_approved = _write_json_to_temp_debate(config, task_run_g2, gate2_result.graph)
    promoted_approved = PromotedOutput(name="task_graph_approved", artifact_type="TASK_GRAPH_APPROVED",
                                       description="Human-approved task graph")
    graph_artifact_id = promote_output(conn, config, task_run_g2, promoted_approved, temp_approved)

    # Step 4: Beads sync
    beads_sync(run_id, gate2_result.graph, conn)

    # Step 5: Execution (only if agent provided)
    execution_result = None
    if agent is not None:
        execution_result = run_execution(run_id, graph_artifact_id, config, agent)

    return PhaseBResult(
        run_id=run_id,
        graph_artifact_id=graph_artifact_id,
        execution_result=execution_result,
    )


def _write_json_to_temp_debate(config: Config, task_run: dict, data: dict) -> str:
    """Write dict as JSON to temp path. Returns temp_path directory."""
    temp_path = build_temp_path(config.storage_root, run_id=task_run["run_id"],
                                task_id=task_run["task_id"],
                                attempt_number=task_run["attempt_number"])
    os.makedirs(temp_path, exist_ok=True)
    with open(os.path.join(temp_path, f"{task_run['task_id']}.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return temp_path
```

Also add the new imports at the top of `debate_pipeline.py`.

- [ ] **Step 4: Run tests**

```bash
pytest tests/integration/test_spec_pipeline_phase_b.py -v
```

Expected: all `PASSED`

- [ ] **Step 5: Run the full test suite to check for regressions**

```bash
pytest tests/ -v --tb=short 2>&1 | tail -40
```

Expected: existing tests still pass, new tests pass

- [ ] **Step 6: Commit**

```bash
git add src/ai_dev_system/debate_pipeline.py tests/integration/test_spec_pipeline_phase_b.py
git commit -m "feat(pipeline): add run_phase_b_pipeline() — spec → task graph → gate2 → beads → execution"
```

---

## Final: Full Test Run

- [ ] **Run complete test suite**

```bash
pytest tests/ -v --tb=short
```

Expected: all tests pass (0 failures)

- [ ] **Final commit if any cleanup needed**

```bash
git add -p  # review any remaining changes
git commit -m "chore: cleanup and finalize debate engine integration"
```

---

## Summary

| Component | Files | Tests |
|-----------|-------|-------|
| v3 Migration | `migrations/v3-debate-engine.sql` | Manual (psql) |
| RunRepo.update_status | `db/repos/runs.py` | `tests/unit/test_run_repo.py` |
| Debate dataclasses | `debate/report.py` | `tests/unit/debate/test_report.py` |
| LLM Protocol + Stub | `debate/llm.py` | Implicit |
| generate_questions | `debate/questions.py` | `tests/unit/debate/test_questions.py` |
| Agent Registry | `debate/agents.py` | Implicit |
| run_debate_round | `debate/rounds.py` | `tests/unit/debate/test_rounds.py` |
| run_debate engine | `debate/engine.py` | `tests/unit/debate/test_engine.py` |
| Phase A pipeline | `debate_pipeline.py` | `tests/integration/test_debate_pipeline.py` |
| Gate 1 bridge | `gate/gate1_bridge.py` | `tests/integration/test_finalize_gate1.py` |
| Gate 1 Skill | `skills/review-debate.md` | Manual |
| finalize_spec | `finalize_spec.py` | `tests/unit/test_finalize_spec.py` |
| Rule Registry | `rules/registry.py` + YAMLs | `tests/unit/rules/test_registry.py` |
| Rule injection | `engine/worker.py` | Regression via worker tests |
| beads_sync | `beads/sync.py` | `tests/integration/test_beads_sync.py` |
| Phase B pipeline | `debate_pipeline.py` | `tests/integration/test_spec_pipeline_phase_b.py` |
