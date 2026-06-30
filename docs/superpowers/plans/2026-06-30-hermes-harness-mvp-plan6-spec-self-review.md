# Plan 6 — Spec self-review critic (4 superpowers dimensions) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** After the system authors a spec, an LLM **critic** reviews it on the four superpowers self-review dimensions — **placeholder, internal-consistency, scope-decomposition, ambiguity** — on BOTH flows (new-project SpecBundle + single-task facet spec). Complementary to the existing grounding check (which covers traceability/measurable-AC/scope-leak/hallucination — a different axis).

**Architecture:** One critic module `spec/self_review.py` (`Finding` + `self_review(payload, kind, llm) -> list[Finding]`), called from two places: (1) **new-project** as Stage 3.5 of `run_spec_pipeline` — auto-repairable findings route through the existing `repair_section`, the rest attach to `SpecBundle.self_review_findings`; (2) **single-task** inside `spec_single_task` / `single_task_worker`, returning findings in the spec JSON. The critic is non-blocking (any LLM/parse failure → `[]`) and gated by `AI_DEV_SPEC_SELF_REVIEW` (default on; `=0` restores legacy). Findings are surfaced in the WebUI.

**Tech Stack:** Python stdlib, the existing spec pipeline (`spec/pipeline.py`, `spec/grounding.py`, `spec/repair.py`), `llm_client.complete(system, user)`, pytest with the existing MagicMock stub-LLM pattern.

## Global Constraints

- **Complementary, not duplicative:** the critic covers placeholder / internal-consistency / scope-decomposition / ambiguity. Do NOT re-implement grounding's traceability/measurable-AC checks.
- **Non-blocking:** any LLM error or malformed JSON → `self_review` returns `[]`. A critic failure must NEVER break spec generation (mirror `llm_grounding_check`'s defensive style).
- **Env kill-switch:** `AI_DEV_SPEC_SELF_REVIEW` — default enabled; `0`/`false`/`no`/`off` disables (restores exact legacy behavior). Mirror the `task_graph/facets.py` `AI_DEV_DISABLE_TASK_FACETS` kill-switch pattern (a plain `os.environ.get` check, NOT the FeatureFlags FLAG_ORDER chain).
- **LLM step:** add `"critic": ("sonnet", "medium")` to `STEP_PROFILES` (llm_factory.py); overridable via `AI_DEV_MODEL_CRITIC`/`AI_DEV_EFFORT_CRITIC`. The critic uses `llm_client.complete(system, user) -> str`.
- **`Finding` is new** (no existing type): `Finding(section, dimension, severity, message, fix)`. Do NOT reuse `GroundingViolation`.
- **Acceptance (spec design doc line 320), must be tested:** (a) placeholder/contradiction/ambiguity findings route auto-repairable items into `repair_section` and surface the rest as gate metadata (new-project); (b) `spec_single_task`/`dev_singletask_spec` returns findings and a **scope** finding flags an over-large "single task"; (c) `AI_DEV_SPEC_SELF_REVIEW=0` restores legacy behavior (no critic call).
- **README test-count chore**; **stdlib only**; **UTF-8**.

---

### Task 1: `spec/self_review.py` — critic module + `Finding` + env gate + STEP_PROFILES

**Files:**
- Create: `src/ai_dev_system/spec/self_review.py`
- Modify: `src/ai_dev_system/llm_factory.py` (add `"critic"` step)
- Test: `tests/unit/spec/test_self_review.py`

**Interfaces (produced; consumed by Tasks 2-3):**
- `Finding` dataclass: `section: str`, `dimension: str` (`"placeholder"|"internal_consistency"|"scope_decomposition"|"ambiguity"`), `severity: str` (`"error"|"warning"`), `message: str`, `fix: str = ""`.
- `self_review_enabled() -> bool` — reads `AI_DEV_SPEC_SELF_REVIEW` (default True; falsy strings disable).
- `self_review(payload: dict, kind: str, llm_client) -> list[Finding]` — `kind` in `{"project","single_task"}`. Builds the dimension-aware critic prompt, calls `llm_client.complete(system, user)`, parses JSON `{"findings":[{section,dimension,severity,message,fix}]}` into `list[Finding]`. Returns `[]` if disabled, on any exception, or on malformed JSON. For `kind="project"` the payload is `{section: content}` for the 5 sections; for `kind="single_task"` it is the facets dict (and the critic is told to weigh the **scope** dimension: is this truly one atomic task?).
- `AUTO_REPAIR_DIMENSIONS = {"placeholder", "ambiguity"}` — module constant used by Task 2 to decide which section-scoped error findings are auto-repairable.

- [ ] **Step 1: Write the failing tests** — `tests/unit/spec/test_self_review.py`:

```python
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from ai_dev_system.spec.self_review import (
    Finding, self_review, self_review_enabled,
)


def _critic_stub(findings: list[dict]) -> MagicMock:
    c = MagicMock()
    c.complete.return_value = json.dumps({"findings": findings})
    return c


def test_disabled_returns_empty(monkeypatch):
    monkeypatch.setenv("AI_DEV_SPEC_SELF_REVIEW", "0")
    llm = _critic_stub([{"section": "proposal", "dimension": "placeholder",
                         "severity": "error", "message": "TBD left in", "fix": "fill it"}])
    assert self_review({"proposal": "TBD"}, "project", llm) == []
    llm.complete.assert_not_called()


def test_parses_findings(monkeypatch):
    monkeypatch.delenv("AI_DEV_SPEC_SELF_REVIEW", raising=False)
    llm = _critic_stub([
        {"section": "proposal", "dimension": "placeholder", "severity": "error",
         "message": "TBD", "fix": "fill"},
        {"section": "global", "dimension": "scope_decomposition", "severity": "warning",
         "message": "two features", "fix": "split"},
    ])
    out = self_review({"proposal": "..."}, "project", llm)
    assert len(out) == 2
    assert out[0] == Finding(section="proposal", dimension="placeholder",
                             severity="error", message="TBD", fix="fill")
    assert out[1].dimension == "scope_decomposition"


def test_malformed_json_returns_empty(monkeypatch):
    monkeypatch.delenv("AI_DEV_SPEC_SELF_REVIEW", raising=False)
    c = MagicMock(); c.complete.return_value = "not json at all"
    assert self_review({"proposal": "..."}, "project", c) == []


def test_llm_failure_returns_empty(monkeypatch):
    monkeypatch.delenv("AI_DEV_SPEC_SELF_REVIEW", raising=False)
    c = MagicMock(); c.complete.side_effect = RuntimeError("down")
    assert self_review({"proposal": "..."}, "project", c) == []


def test_single_task_kind_passes_facets(monkeypatch):
    monkeypatch.delenv("AI_DEV_SPEC_SELF_REVIEW", raising=False)
    llm = _critic_stub([{"section": "global", "dimension": "scope_decomposition",
                         "severity": "error", "message": "3 tasks hiding as one", "fix": "split"}])
    out = self_review({"input": {"status": "filled", "content": "..."}}, "single_task", llm)
    assert len(out) == 1 and out[0].dimension == "scope_decomposition"
    # the critic was actually called with the facets payload
    assert llm.complete.called


def test_enabled_default(monkeypatch):
    monkeypatch.delenv("AI_DEV_SPEC_SELF_REVIEW", raising=False)
    assert self_review_enabled() is True
    monkeypatch.setenv("AI_DEV_SPEC_SELF_REVIEW", "off")
    assert self_review_enabled() is False
```

- [ ] **Step 2: Run → RED** (`ModuleNotFoundError`).

- [ ] **Step 3: Implement** `src/ai_dev_system/spec/self_review.py`:

```python
"""Spec self-review critic — reviews an authored spec on the four superpowers
self-review dimensions (placeholder / internal-consistency / scope-decomposition /
ambiguity). Complementary to spec/grounding.py (traceability axis). Non-blocking:
any failure yields no findings and never breaks spec generation."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

_ENV = "AI_DEV_SPEC_SELF_REVIEW"
_DIMENSIONS = ("placeholder", "internal_consistency", "scope_decomposition", "ambiguity")
AUTO_REPAIR_DIMENSIONS = {"placeholder", "ambiguity"}


@dataclass
class Finding:
    section: str
    dimension: str
    severity: str
    message: str
    fix: str = ""


def self_review_enabled() -> bool:
    return os.environ.get(_ENV, "1").strip().lower() not in {"0", "false", "no", "off", ""}


_SYSTEM = (
    "You are a meticulous spec critic. Review the authored spec on EXACTLY these four "
    "dimensions: placeholder (TBD/TODO/'to be decided'/vague authored content), "
    "internal_consistency (contradictions across sections/facets), scope_decomposition "
    "(does this fit ONE implementation plan / one task graph, or must it be split? for a "
    "single task: is it truly ONE atomic task?), ambiguity (a requirement readable two ways). "
    "Do NOT check traceability or measurability — another tool does that. "
    'Return STRICT JSON: {"findings":[{"section":str,"dimension":str,"severity":"error"|"warning",'
    '"message":str,"fix":str}]}. section is the spec section name or "global" for cross-section. '
    "Empty findings list if the spec is clean. No prose outside the JSON."
)


def self_review(payload: dict, kind: str, llm_client) -> list[Finding]:
    if not self_review_enabled():
        return []
    try:
        user = (
            f"KIND: {kind}\n"
            f"DIMENSIONS: {', '.join(_DIMENSIONS)}\n"
            f"SPEC PAYLOAD (JSON):\n{json.dumps(payload, ensure_ascii=False)[:24000]}"
        )
        raw = llm_client.complete(system=_SYSTEM, user=user)
        data = json.loads(raw)
        out: list[Finding] = []
        for f in data.get("findings", []):
            try:
                out.append(Finding(
                    section=str(f["section"]), dimension=str(f["dimension"]),
                    severity=str(f.get("severity", "warning")),
                    message=str(f["message"]), fix=str(f.get("fix", "")),
                ))
            except (KeyError, TypeError):
                continue  # skip malformed individual findings
        return out
    except Exception:  # noqa: BLE001 - critic must never break spec generation
        return []
```

In `src/ai_dev_system/llm_factory.py` `STEP_PROFILES`, add (next to `"spec"`):
```python
    "critic": ("sonnet", "medium"),
```

- [ ] **Step 4: Run → GREEN** (6 passed).
- [ ] **Step 5:** README bump (+6); full suite; commit:
```bash
git add src/ai_dev_system/spec/self_review.py src/ai_dev_system/llm_factory.py tests/unit/spec/test_self_review.py README.md
git commit -m "feat(spec): self-review critic module (4 dimensions, env-gated, non-blocking)"
```

---

### Task 2: New-project caller — Stage 3.5 in `run_spec_pipeline` (+ auto-repair routing)

**Files:**
- Modify: `src/ai_dev_system/spec/pipeline.py`
- Modify: `src/ai_dev_system/spec_bundle.py` (add `self_review_findings` field)
- Test: `tests/unit/spec/test_spec_pipeline_self_review.py`

**Interfaces:**
- `SpecBundle` gains `self_review_findings: list = field(default_factory=list)` (list of `Finding`; surfaced as gate metadata).
- In `run_spec_pipeline`, between the grounding/repair loop end (~line 136) and the degraded-warning block (~line 139): if `self_review_enabled()`, build `drafts_payload = {section: draft.content for section, draft in drafts.items()}`, call `findings = self_review(drafts_payload, "project", critic_llm)`. Route: for each finding with `severity=="error"` AND `dimension in AUTO_REPAIR_DIMENSIONS` AND `section` is one of the 5 real sections AND `repair_budget > 0` → synthesize a `GroundingViolation(rule=f"self_review:{dimension}", message=message, severity="error")` and call `repair_section(...)` for that section (reuse the existing repair call shape + decrement budget), then re-store the repaired draft. Attach ALL findings (including repaired ones, marked, and the surfaced rest) to `bundle.self_review_findings`.
- The `critic_llm`: reuse the pipeline's `llm_client` (simplest — same client the generators use) OR accept an optional `critic_llm` param defaulting to `llm_client`. Use `llm_client` to avoid threading a new param through all callers.

- [ ] **Step 1: Write failing tests** — `tests/unit/spec/test_spec_pipeline_self_review.py`. Reuse the existing `run_spec_pipeline` test harness (see `tests/unit/spec/test_pipeline*.py` or `test_grounding_llm.py` for how a stub llm + brief + answers + tmp_path drive the pipeline). Patch `ai_dev_system.spec.pipeline.self_review` to return scripted findings:
  - a `placeholder`/`error`/section=`proposal` finding → assert `repair_section` was invoked for `proposal` (patch/spy it) and the finding is in `bundle.self_review_findings`.
  - a `scope_decomposition`/`warning`/section=`global` finding → assert it is surfaced in `bundle.self_review_findings` and `repair_section` was NOT called for it.
  - `AI_DEV_SPEC_SELF_REVIEW=0` → `self_review` not called, `bundle.self_review_findings == []` (legacy behavior).
- [ ] **Step 2: RED. Step 3: Implement.** **Step 4: GREEN** + full suite. **Step 5: Commit**
```bash
git add src/ai_dev_system/spec/pipeline.py src/ai_dev_system/spec_bundle.py tests/unit/spec/test_spec_pipeline_self_review.py README.md
git commit -m "feat(spec): Stage 3.5 self-review critic in run_spec_pipeline (+auto-repair routing)"
```

---

### Task 3: Single-task caller — `spec_single_task` + worker returns findings

**Files:**
- Modify: `src/ai_dev_system/task_graph/single_task.py`
- Modify: `src/ai_dev_system/task_graph/single_task_worker.py`
- Test: `tests/unit/task_graph/test_single_task_self_review.py`

**Interfaces:**
- `spec_single_task(...)` gains a step: after `facets` are built, if `self_review_enabled()`, call `findings = self_review(facets, "single_task", llm)` and include `"findings": [f.__dict__ for f in findings]` in the returned dict (`{"task","facets","findings"}`). Pass the same `llm` it already has (the `make_llm_client("spec")` client from the worker; the agentic/repo path may have `llm=None` — in that case build `make_llm_client("critic")` lazily, or skip the critic when `llm is None` and document it).
- `single_task_worker.run_worker`: include `payload["findings"] = result.get("findings", [])` in the JSON it writes, so `/task-spec` and `dev_singletask_spec` can read them.

- [ ] **Step 1: Write failing tests** — `tests/unit/task_graph/test_single_task_self_review.py`: with a stub `llm` (MagicMock, scripted critic JSON), call `spec_single_task("some idea", llm)` (text path, no repo) and assert the returned dict has `findings` containing a `scope_decomposition` finding that flags an over-large task. Add a worker test (or extend an existing one) asserting the written JSON includes `findings`. `AI_DEV_SPEC_SELF_REVIEW=0` → no `findings` (or empty). Reuse the single-task spec test harness/stub.
- [ ] **Step 2: RED. Step 3: Implement. Step 4: GREEN** + full suite. **Step 5: Commit**
```bash
git add src/ai_dev_system/task_graph/single_task.py src/ai_dev_system/task_graph/single_task_worker.py tests/unit/task_graph/test_single_task_self_review.py README.md
git commit -m "feat(single-task): spec_single_task returns self-review critic findings"
```

---

### Task 4: Surface findings in the WebUI (both flows)

**Files:**
- Modify: `src/ai_dev_system/webui.py`
- Test: `tests/unit/test_webui_self_review.py`

**Changes:**
- `_task_spec_page` (single-task): if the spec JSON has `findings`, render a "Spec self-review" card listing each finding (`dimension · severity · section`: message; fix). Empty/absent → no card.
- New-project: wherever the spec/Gate surfaces (the run/gate page that shows the SpecBundle), render `self_review_findings` similarly if present. (If the new-project spec findings aren't easily reachable in the webui yet, persist a small `self_review.json` next to the spec bundle in Task 2 and read it here — keep it simple; the single-task surface is the must-have.)

- [ ] **Step 1: Write failing tests** — `tests/unit/test_webui_self_review.py`: seed a task spec JSON with a `findings` list; assert `_task_spec_page(spec_id)` HTML contains the dimension + message; seed one with no findings → no self-review card. (Mirror `tests/unit/test_webui_task_plan.py` fixture style: monkeypatch `webui._config`, write the spec JSON under `storage_root/task_specs/`.)
- [ ] **Step 2: RED. Step 3: Implement** (HTML-escape all finding text). **Step 4: GREEN** + full suite. **Step 5: Commit**
```bash
git add src/ai_dev_system/webui.py tests/unit/test_webui_self_review.py README.md
git commit -m "feat(webui): show spec self-review findings on the task-spec page"
```

---

## Acceptance (whole plan)
Maps to spec design doc line 320:
- (a) **new-project:** placeholder/ambiguity error findings route into `repair_section`; the rest (scope/consistency) attach to `SpecBundle.self_review_findings` (Task 2).
- (b) **single-task:** `spec_single_task` returns `findings`; a scope finding flags an over-large "single task" (Task 3).
- (c) `AI_DEV_SPEC_SELF_REVIEW=0` restores legacy behavior on both flows (Tasks 1-3).
- Findings visible to the operator (Task 4).

## Risk + Self-Review (plan author)
- **Lower risk than 5.2** — additive, non-blocking, env-gated; the legacy path is preserved exactly when disabled, and a critic failure yields `[]`.
- **Highest risk = Task 2** (touching `run_spec_pipeline` + the repair budget). Mitigation: the critic block sits AFTER the grounding/repair loop, reuses `repair_section` unchanged, and is fully skipped when `self_review_enabled()` is false — so existing `run_spec_pipeline` tests stay green with the flag defaulting on only if the critic is patched/stubbed in those tests; **the implementer must confirm existing spec-pipeline tests still pass** (they pass a stub llm whose `.complete` returns section markdown — `self_review` will call it and get non-JSON → `[]`, harmless). Flag this: existing pipeline tests' stub returns markdown, so the critic gets markdown, fails JSON parse, returns `[]` — no behavior change. Verify.
- **No new third-party dep; critic reuses `llm_client.complete`.** ✓
- **Complementary to grounding** (different dimensions); not duplicative. ✓
