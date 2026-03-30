# Design Spec: Superpowers Verification Tổng Thể (Phase 4)

**Date:** 2026-03-30
**Status:** Approved
**Scope:** Phase 4 — Superpowers verification tổng thể (sau execution loop, trước Beads audit trail)
**Depends on:** execution-runner-design, debate-engine-gate1-redesign

---

## Overview

Phase 4 là bước kiểm tra cuối cùng sau khi toàn bộ task graph đã được execute. Gồm 2 lớp:

1. **Tổng hợp per-task results** — collect `done_definition` + `verification_steps` results từ DB
2. **Re-check acceptance criteria** — LLM judge từng criterion trong `acceptance-criteria.md`

Kết quả được trình bày cho user duyệt (Gate 3 nhẹ). Nếu có fail: sinh remediation tasks → tái sử dụng execution runner → loop lại (mặc định max 3 lần; human có thể extend tại PAUSED_AT_GATE_3B). Sau khi loop exhausted mà vẫn fail → escalate human (tiếp tục / skip criteria / abort).

**Phân biệt với per-task verification (Phase 3):**
- Per-task (trong execution loop): `SK->>SP: Quality verification` → fail → agent sửa → re-verify (max 3) → escalate — đã spec trong execution-runner-design
- Phase 4 (tổng thể): `SP->>U: Verification report` — check toàn bộ deliverable so với acceptance criteria

---

## Architecture: Split Pipeline

```
Phase 3 ends: run.status = SUCCESS
      ↓
Caller transitions: run.status = RUNNING_PHASE_V → calls run_phase_v_pipeline()
      ↓
[Python Phase V-A]  collect_evidence → llm_judge → VERIFICATION_REPORT artifact
                                                         ↓ run.status = PAUSED_AT_GATE_3

[Gate 3 Skill]      /review-verification → full report → collect decisions
                                                         ↓ finalize_gate3()

                          ┌──────────────────────────────┘
                          │
                    All pass/skip?
                     ├── YES → run.status = COMPLETED → Phase 5 (Beads)
                     └── NO  → generate RemediationGraph → run_execution() (reuse)
                                        ↓ attempt = count(VERIFICATION_REPORT artifacts)
                               attempt < 3 → RUNNING_PHASE_V → loop lại Phase V-A
                               attempt ≥ 3 → PAUSED_AT_GATE_3B (human: tiếp tục/skip/abort)
```

**Handoff từ Phase 3:**
Phase 3 (`run_execution()`) trả về khi `run.status = SUCCESS`. Caller (Phase B của debate_pipeline.py hoặc top-level dispatcher) phải:
1. Transition: `UPDATE runs SET status = 'RUNNING_PHASE_V' WHERE run_id = %s AND status = 'SUCCESS'`
2. Call: `run_phase_v_pipeline(run_id, spec_artifact_id, config, llm)`

`COMPLETED` (đã có trong `run_status` enum ở schema) là terminal state sau khi Phase 4 all-pass. `SUCCESS` là terminal state của Phase 3.

**Attempt counter source of truth:**
Đếm số `VERIFICATION_REPORT` artifacts có `status = 'ACTIVE' OR 'SUPERSEDED'` cho `run_id` trong bảng `artifacts`. Mỗi lần `run_verification()` chạy sẽ tạo một version mới (version = attempt number). `finalize_gate3()` đọc count này để quyết định RUNNING_PHASE_V hay PAUSED_AT_GATE_3B.

---

## Component 1: Verification Engine (Python)

### File Structure

```
src/ai_dev_system/verification/
├── __init__.py
├── collector.py      # collect_evidence()
├── judge.py          # VerificationLLMClient Protocol + StubVerificationLLMClient
├── report.py         # CriterionResult, VerificationReport dataclasses
└── pipeline.py       # run_verification() + run_phase_v_pipeline() — orchestrate
```

### Data Contracts

```python
@dataclass
class CriterionResult:
    criterion_id: str                          # "AC-1", "AC-2" ...
    criterion_text: str                        # text từ acceptance-criteria.md
    verdict: Literal["PASS", "FAIL", "SKIP"]
    confidence: float                          # 0.0–1.0
    evidence: list[str]                        # task output excerpts dùng để judge
    reasoning: str                             # LLM giải thích tại sao pass/fail
    related_task_ids: list[str]

@dataclass
class VerificationReport:
    run_id: str
    attempt: int                               # 1, 2, 3 — đếm từ artifact count
    criteria: list[CriterionResult]
    overall: Literal["ALL_PASS", "HAS_FAIL"]
    task_summary: dict[str, TaskSummaryEntry]  # per-task done_definition results
    generated_at: str                          # ISO timestamp

@dataclass
class TaskSummaryEntry:
    task_id: str
    done_definition_met: bool
    output_artifact_id: str | None
    verification_step_results: list[str]       # output lines từ verification_steps
```

### Entry Point

```python
def run_phase_v_pipeline(
    run_id: str,
    spec_artifact_id: str,
    config: Config,
    llm: VerificationLLMClient,
) -> VerificationReport:
    """
    Phase V-A standalone entry point. Caller must have already set run.status = RUNNING_PHASE_V.

    Precondition: run.status = RUNNING_PHASE_V
    Postcondition: run.status = PAUSED_AT_GATE_3

    Returns:
        VerificationReport (also written as VERIFICATION_REPORT artifact)

    Note on artifact access: VERIFICATION_REPORT artifacts are looked up via direct
    artifact table query (run_id + artifact_type='VERIFICATION_REPORT' + status='ACTIVE'),
    NOT via runs.current_artifacts. The current_artifacts JSONB column does not include
    a 'verification_report_id' key — direct query is the access pattern throughout this module.
    """


def run_verification(
    run_id: str,
    spec_artifact_id: str,     # UUID của SPEC_BUNDLE artifact (chứa acceptance-criteria.md)
    config: Config,
    llm: VerificationLLMClient,
) -> VerificationReport:
    """
    Internal: collect evidence → LLM judge → ghi VERIFICATION_REPORT artifact.
    Called by run_phase_v_pipeline().
    """
```

### LLM Protocol (testable với stub)

```python
class VerificationLLMClient(Protocol):
    def judge_criterion(
        self,
        criterion_id: str,
        criterion_text: str,
        evidence: list[str],
    ) -> tuple[Literal["PASS", "FAIL"], float, str]:
        """Returns: (verdict, confidence, reasoning)"""
        ...
```

---

## Component 2: Gate 3 Bridge

### File

```
src/ai_dev_system/gate/gate3_bridge.py
```

### Entry Point

```python
@dataclass
class Gate3Decision:
    criterion_id: str
    action: Literal["SKIP", "ABORT"]  # chỉ fail criteria cần decision; pass là implicit

@dataclass
class Gate3Result:
    run_id: str
    has_remediation: bool
    remediation_graph: dict | None    # TaskGraph JSON nếu có fail → remediation
    aborted: bool

def finalize_gate3(
    run_id: str,
    decisions: list[Gate3Decision],   # chỉ chứa FAIL criteria cần user action
    storage_root: Path,
    conn: Connection,
) -> Gate3Result:
    """
    Đọc VERIFICATION_REPORT artifact hiện tại để lấy danh sách criteria.
    Pass criteria (không có trong decisions) → tự động pass.
    SKIP criteria → ghi nhận skip, không tạo remediation.
    ABORT → run.status = ABORTED.

    Remaining fail criteria (không SKIP, không ABORT):
      - Đếm attempt = số VERIFICATION_REPORT artifacts cho run_id
      - attempt < 3 → sinh RemediationGraph → run.status = RUNNING_PHASE_V
      - attempt ≥ 3 → run.status = PAUSED_AT_GATE_3B (human decides)

    All pass/skip → run.status = COMPLETED
    """
```

---

## Component 3: Gate 3 Skill

### File

```
skills/review-verification.md   # invoked via /review-verification
```

### State Machine

```
[PRESENT] → (all pass?) → [CONFIRM_PASS] → [DONE: COMPLETED]
                ↓ (has fail)
           [COLLECT_FAILS] → [CONFIRM] → [DONE]
                                ↑           |
                                └───────────┘
                            (nếu user muốn sửa)

DONE: has remediation → RUNNING_PHASE_V → execution loop
DONE: abort → ABORTED
DONE: attempt ≥ 3 human extends → RUNNING_PHASE_V
```

### State 1: PRESENT

Skill trình bày full report (per-criterion, evidence, AI reasoning). Sau đó một message tóm tắt:

```
✅ Verification Report — Attempt 1/3

✅ PASS (5): AC-1, AC-2, AC-3, AC-5, AC-7
❌ FAIL (2): AC-4, AC-6

❌ AC-4: "User có thể search bài viết theo tag"
   Reasoning: Search API trả về 200 nhưng không filter theo tag
   Evidence: task-6 output — GET /posts?tag=python trả về tất cả posts
   Confidence: 0.92

❌ AC-6: "Coverage ≥ 80%"
   Reasoning: pytest-cov report = 71%
   Evidence: task-6 verification_steps output
   Confidence: 0.99

→ Xác nhận để tạo remediation tasks, hoặc "skip AC-4" / "abort".
```

**All pass fast path:** Nếu không có FAIL → bỏ qua COLLECT_FAILS, đến CONFIRM_PASS ngay:
```
✅ Tất cả 7 criteria PASS. Xác nhận hoàn thành?
```

**Nguyên tắc:** Trình bày toàn bộ picture ngay — không hỏi từng criterion một (nhất quán với Gate 1).

### State 2: COLLECT_FAILS

Chỉ cần với criteria FAIL:

| User nói | Parse thành |
|---|---|
| `"ok tạo remediation"` / `"fix hết"` | Tất cả FAIL → remediation (không có SKIP) |
| `"skip AC-4, fix AC-6"` | AC-4 → SKIP, AC-6 → remediation |
| `"abort"` | run → ABORTED |
| `"xem evidence AC-4"` | Show full evidence, không chuyển state |

### State 3: CONFIRM

```
📝 Xác nhận:
  AC-4 → ⏭️  Skip
  AC-6 → 🔧 Tạo remediation task

Tiếp tục? (remediation sẽ chạy lại execution loop)
```

- `"ok"` / `"xác nhận"` → gọi `finalize_gate3()`
- `"sửa"` → quay lại COLLECT_FAILS

### State PAUSED_AT_GATE_3B (attempt ≥ 3, vẫn fail)

```
⚠️ Đã thử 3 lần, vẫn còn criteria fail: AC-6

Bạn muốn:
  A. Tiếp tục — thêm 1 attempt nữa
  B. Skip criteria fail — đánh dấu skip, hoàn thành
  C. Abort — dừng toàn bộ run
```

Max 3 là **soft limit** — human có thể chọn A để extend.

### Nguyên tắc thiết kế

| Nguyên tắc | Lý do |
|---|---|
| Trình bày toàn bộ picture ngay | Nhất quán với Gate 1 — không interrupt liên tục |
| Batch input được phép | `"skip AC-4, fix AC-6"` trong 1 message |
| CONFIRM bắt buộc trước remediation | Safety net — remediation không undo được |
| `Gate3Decision` chỉ chứa FAIL decisions | PASS là implicit — skill không enumerate pass criteria |
| attempt ≥ 3 → PAUSED_AT_GATE_3B (soft) | Human có thể extend — nhất quán với escalation pattern |

### Edge Cases

1. **All pass** — Skill đến CONFIRM_PASS, gọi `finalize_gate3([])` (empty decisions) → COMPLETED
2. **attempt ≥ 3, vẫn fail** — Skill trình bày PAUSED_AT_GATE_3B: tiếp tục / skip / abort
3. **User abort** — `finalize_gate3()` set `run.status = ABORTED`

---

## DB Changes

### New `run_status` values

```sql
-- v4-verification.sql
-- Safe to run after control-layer-schema.sql, v3-debate-engine.sql
ALTER TYPE run_status ADD VALUE IF NOT EXISTS 'RUNNING_PHASE_V';
ALTER TYPE run_status ADD VALUE IF NOT EXISTS 'PAUSED_AT_GATE_3';
ALTER TYPE run_status ADD VALUE IF NOT EXISTS 'PAUSED_AT_GATE_3B';
-- Note: 'COMPLETED' already exists in run_status enum (control-layer-schema.sql line 28)
-- Note: 'SUCCESS' is the Phase 3 terminal state; 'COMPLETED' is the Phase 4 terminal state
```

### New `artifact_type` value

```sql
ALTER TYPE artifact_type ADD VALUE IF NOT EXISTS 'VERIFICATION_REPORT';
```

### New `event_type` values

```sql
ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'VERIFICATION_STARTED';
ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'VERIFICATION_COMPLETED';
ALTER TYPE event_type ADD VALUE IF NOT EXISTS 'REMEDIATION_CREATED';
```

---

## Testing Strategy

### Unit Tests

```
tests/unit/verification/
├── test_report.py         # CriterionResult, VerificationReport, TaskSummaryEntry construction
├── test_collector.py      # collect_evidence() với mock DB
├── test_judge.py          # judge_criterion() với StubVerificationLLMClient (keyed by criterion_id)
└── test_pipeline.py       # run_verification() end-to-end với stub LLM + mock DB
```

### Integration Tests

```
tests/integration/
├── test_verification_pipeline.py   # collect → judge → VERIFICATION_REPORT artifact ghi đúng
├── test_finalize_gate3.py          # all-pass → COMPLETED; has-fail → RemediationGraph; all-skip → COMPLETED
├── test_remediation_loop.py        # attempt counter tăng đúng; attempt ≥ 3 → PAUSED_AT_GATE_3B
└── test_verification_allpass.py    # all-pass fast path: empty decisions → COMPLETED (no COLLECT_FAILS state)
```

### Stub LLM

```python
class StubVerificationLLMClient:
    """Returns configurable verdicts per criterion_id — deterministic cho tests."""
    def __init__(self, verdicts: dict[str, tuple[str, float, str]]):
        self.verdicts = verdicts  # {"AC-1": ("PASS", 0.95, "looks good")}

    def judge_criterion(
        self,
        criterion_id: str,
        criterion_text: str,
        evidence: list[str],
    ) -> tuple[str, float, str]:
        return self.verdicts.get(criterion_id, ("PASS", 1.0, "stub default"))
```

---

## File Map

### New files

| File | Responsibility |
|------|---------------|
| `src/ai_dev_system/verification/__init__.py` | Package marker |
| `src/ai_dev_system/verification/report.py` | CriterionResult, VerificationReport, TaskSummaryEntry dataclasses |
| `src/ai_dev_system/verification/collector.py` | collect_evidence() |
| `src/ai_dev_system/verification/judge.py` | VerificationLLMClient Protocol + StubVerificationLLMClient |
| `src/ai_dev_system/verification/pipeline.py` | run_phase_v_pipeline() + run_verification() |
| `src/ai_dev_system/gate/gate3_bridge.py` | finalize_gate3() + Gate3Decision, Gate3Result |
| `skills/review-verification.md` | Gate 3 Skill — /review-verification |
| `docs/schema/migrations/v4-verification.sql` | New run_status + artifact_type + event_type values |
| `tests/unit/verification/test_report.py` | Dataclass tests |
| `tests/unit/verification/test_collector.py` | Evidence collection tests |
| `tests/unit/verification/test_judge.py` | LLM judge tests (keyed by criterion_id) |
| `tests/unit/verification/test_pipeline.py` | Pipeline orchestration tests |
| `tests/integration/test_verification_pipeline.py` | Phase V-A end-to-end |
| `tests/integration/test_finalize_gate3.py` | Gate 3 bridge: pass/fail/skip/abort paths |
| `tests/integration/test_remediation_loop.py` | Remediation loop + attempt counter |
| `tests/integration/test_verification_allpass.py` | All-pass fast path |

### Modified files

| File | Change |
|------|--------|
| `src/ai_dev_system/debate_pipeline.py` | `run_phase_b_pipeline()` transitions `SUCCESS → RUNNING_PHASE_V` then calls `run_phase_v_pipeline()` as final step |
