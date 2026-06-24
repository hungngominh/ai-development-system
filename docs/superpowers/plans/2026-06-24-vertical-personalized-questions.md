# Vertical-Personalized Debate Questions — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make debate-question generation infer a project's product "vertical" and surface product/behavioral questions (psychology, daily-usage, retention, emotion), debated by new non-technical personas — on both the legacy and v2 paths.

**Architecture:** Add one resilient LLM step that infers a `ProjectProfile` (the "lens") from the brief/idea. Thread the lens into the legacy generator and the v2 inventory/materializer prompts. Add 3 product/behavioral domains, 4 fixed personas, and a WARN-level coverage check. The lens is additive: an **empty profile injects nothing**, so existing behavior (and the stub-based test suite) is unchanged.

**Tech Stack:** Python 3.12, stdlib `dataclasses`/`json`, `pyyaml` (already used by the agent loader), `pytest`.

## Global Constraints

- **Python 3.12**; run tests with `PYTHONUTF8=1 python -m pytest ...` (Windows console is cp1252; Vietnamese strings in prompts/agents need UTF-8).
- **Backward-compat invariant:** when `ProjectProfile.is_empty()` is true, every injection site MUST produce output byte-identical to today. The stub LLM (`StubDebateLLMClient`) returns a non-JSON default string for the profile prompt, so under stub the profile is always empty → existing golden tests stay green. Do not change this property.
- **Stub keyword avoidance:** the profile system prompt MUST NOT contain the substrings `question`, `generate`, `moderator`, `synthesis`, `finalize`, or `spec` (incl. inside words like "specific"/"specialist"). `StubDebateLLMClient.complete` routes on these substrings; avoiding them makes the stub fall through to its default string → resilient parse → empty profile.
- **Domain cap raised 12 → 15** (was Decision #23). Update every "12" reference touched by this plan (code + tests). The project owner has explicitly approved extending the registry.
- **Do NOT add to `feature_flags.FLAG_ORDER`.** That is a rigid linear chain (flag N requires N-1). Use a standalone kill-switch env var `AI_DEV_DISABLE_VERTICAL_PROFILE=1` inside `infer_project_profile` instead.
- **Agent `.md` format:** YAML frontmatter (`agent_key`, `domain`, `version`, `aliases`, `debate_role` ∈ {critic_first, advocate_first, neutral}, `typical_paired_with`) + markdown body. Filename = `snake_case(agent_key).md` under `references/agency-agents/`.
- **Prompt file format:** `SYSTEM\n<...>\nUSER\n<...>`, placeholders substituted with `str.replace` (NOT `.format`).
- **Branch first:** repo is on `master`. Before the first commit, create a feature branch: `git switch -c feat/vertical-personalized-questions`.

## File Structure

**New files**
- `src/ai_dev_system/debate/profile.py` — `ProjectProfile` dataclass, `infer_project_profile`, `PRODUCT_BEHAVIORAL_DOMAINS`, `vertical_relevance`.
- `src/ai_dev_system/debate/questions/prompts/profile.txt` — profile inference prompt.
- `references/agency-agents/behavioral_psychologist.md`
- `references/agency-agents/retention_growth_strategist.md`
- `references/agency-agents/ux_researcher.md`
- `references/agency-agents/market_analyst.md`
- `tests/unit/debate/test_profile.py`

**Modified files**
- `src/ai_dev_system/debate/domains.py` — +3 domains, +aliases, docstring 12→15.
- `src/ai_dev_system/debate/agents/legacy.py` — +4 keys in `AGENT_PROMPTS` (so `VALID_AGENT_KEYS` includes them).
- `src/ai_dev_system/debate/questions/legacy.py` — `generate_questions(..., profile=None)` + lens block.
- `src/ai_dev_system/debate/questions/inventory.py` + `prompts/inventory.txt` — `run(..., profile=None)` + `{project_profile}`.
- `src/ai_dev_system/debate/questions/materializer.py` + `prompts/materializer.txt` — `run(..., profile=None)` + `{project_profile}`.
- `src/ai_dev_system/debate/questions/pipeline.py` — `run_pipeline(..., profile=None)` threads to stages.
- `src/ai_dev_system/debate/questions/coverage.py` + `models.py` — C5 personalization (WARN).
- `src/ai_dev_system/debate_pipeline.py` — infer profile, stamp `brief["_project_profile"]`, thread into `_question_path`.
- Tests: `test_domains.py`, `agents/test_registry.py`, `test_debate_pipeline_dispatch.py` (count assertions).

---

## Task 1: `ProjectProfile` model + resilient inference

**Files:**
- Create: `src/ai_dev_system/debate/profile.py`
- Create: `src/ai_dev_system/debate/questions/prompts/profile.txt`
- Test: `tests/unit/debate/test_profile.py`

**Interfaces:**
- Produces:
  - `ProjectProfile(vertical: str, primary_personas: list[str], key_dimensions: list[str], emotional_stakes: list[str])` with `@classmethod empty()`, `is_empty() -> bool` (True iff `not key_dimensions`), `to_dict() -> dict`.
  - `infer_project_profile(brief: dict, llm_client) -> ProjectProfile` — never raises; returns `empty()` on any error, non-dict, or when `AI_DEV_DISABLE_VERTICAL_PROFILE=1`.
  - `PRODUCT_BEHAVIORAL_DOMAINS: frozenset[str] = frozenset({"psychology", "growth", "research", "product", "design"})`
  - `vertical_relevance(questions, profile) -> float` — fraction of questions whose `.domain` ∈ `PRODUCT_BEHAVIORAL_DOMAINS` (0.0 when no questions or empty profile).

- [ ] **Step 1: Write the prompt file**

Create `src/ai_dev_system/debate/questions/prompts/profile.txt` (note: avoids the stub trigger substrings):

```
SYSTEM
You classify a software product's market vertical and the human-behavior
themes that matter for it. Read the project brief and return ONLY a JSON
object with this shape:

{
  "vertical": "<short product category label>",
  "primary_personas": ["<real human user type>", ...],
  "key_dimensions": ["<product/behavioral axis>", ...],
  "emotional_stakes": ["<emotional risk or payoff>", ...]
}

Guidance:
- vertical: e.g. "couples relationship app", "used-car marketplace".
- key_dimensions: the product / behavioral axes that matter for THIS vertical
  — e.g. user psychology, daily-usage habit, retention drivers, emotional
  safety, buyer behavior. These are NOT engineering topics.
- Use empty arrays when unknown. Output ONLY the JSON object, no prose.

USER
{brief_json}
```

- [ ] **Step 2: Write the failing test**

Create `tests/unit/debate/test_profile.py`:

```python
import json

from ai_dev_system.debate.profile import (
    ProjectProfile,
    infer_project_profile,
    vertical_relevance,
    PRODUCT_BEHAVIORAL_DOMAINS,
)
from ai_dev_system.debate.llm import StubDebateLLMClient
from ai_dev_system.debate.report import Question


class _FakeLLM:
    def __init__(self, response: str):
        self._response = response
    def complete(self, system: str, user: str) -> str:
        return self._response


def _q(domain: str) -> Question:
    return Question(id="Q1", text="t", classification="REQUIRED",
                    domain=domain, agent_a="ProductManager", agent_b="BackendArchitect")


def test_empty_profile_is_empty():
    p = ProjectProfile.empty()
    assert p.is_empty() is True
    assert p.key_dimensions == []


def test_valid_json_parses_into_profile():
    payload = json.dumps({
        "vertical": "couples relationship app",
        "primary_personas": ["long-distance couples"],
        "key_dimensions": ["couple psychology", "daily-usage habit", "retention"],
        "emotional_stakes": ["breakup anxiety"],
    })
    p = infer_project_profile({"idea": "an app for couples"}, _FakeLLM(payload))
    assert p.is_empty() is False
    assert p.vertical == "couples relationship app"
    assert "retention" in p.key_dimensions


def test_non_json_response_yields_empty_profile():
    p = infer_project_profile({"idea": "x"}, _FakeLLM("not json at all"))
    assert p.is_empty() is True


def test_json_that_is_not_an_object_yields_empty_profile():
    p = infer_project_profile({"idea": "x"}, _FakeLLM("[1, 2, 3]"))
    assert p.is_empty() is True


def test_stub_llm_yields_empty_profile():
    # critical backward-compat guarantee: under the stub, profile is always empty
    p = infer_project_profile({"idea": "x"}, StubDebateLLMClient())
    assert p.is_empty() is True


def test_kill_switch_env_yields_empty_profile(monkeypatch):
    monkeypatch.setenv("AI_DEV_DISABLE_VERTICAL_PROFILE", "1")
    payload = json.dumps({"vertical": "x", "key_dimensions": ["a"],
                          "primary_personas": [], "emotional_stakes": []})
    p = infer_project_profile({"idea": "x"}, _FakeLLM(payload))
    assert p.is_empty() is True


def test_vertical_relevance_fraction():
    profile = ProjectProfile("v", [], ["d"], [])
    qs = [_q("psychology"), _q("backend"), _q("growth"), _q("security")]
    assert vertical_relevance(qs, profile) == 0.5


def test_vertical_relevance_zero_when_profile_empty():
    qs = [_q("psychology")]
    assert vertical_relevance(qs, ProjectProfile.empty()) == 0.0
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `PYTHONUTF8=1 python -m pytest tests/unit/debate/test_profile.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ai_dev_system.debate.profile'`.

- [ ] **Step 4: Write the implementation**

Create `src/ai_dev_system/debate/profile.py`:

```python
"""ProjectProfile — the vertical/persona "lens" for question personalization.

Inferred once per run from the brief/idea, then injected into question
generation so the output spans product/behavioral dimensions, not just
technical ones. Inference is *resilient*: any failure (bad JSON, non-dict,
stub LLM, kill-switch) yields an empty profile, which injects nothing —
preserving today's behavior and the stub-based test suite.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from ai_dev_system.debate.questions._prompt_utils import split_prompt

PROMPT_PATH = Path(__file__).parent / "questions" / "prompts" / "profile.txt"

# Domains that count as "product / behavioral" for personalization checks.
PRODUCT_BEHAVIORAL_DOMAINS: frozenset[str] = frozenset(
    {"psychology", "growth", "research", "product", "design"}
)

_KILL_SWITCH_ENV = "AI_DEV_DISABLE_VERTICAL_PROFILE"


@dataclass
class ProjectProfile:
    vertical: str = ""
    primary_personas: list[str] = field(default_factory=list)
    key_dimensions: list[str] = field(default_factory=list)
    emotional_stakes: list[str] = field(default_factory=list)

    @classmethod
    def empty(cls) -> "ProjectProfile":
        return cls()

    def is_empty(self) -> bool:
        return not self.key_dimensions

    def to_dict(self) -> dict:
        return {
            "vertical": self.vertical,
            "primary_personas": list(self.primary_personas),
            "key_dimensions": list(self.key_dimensions),
            "emotional_stakes": list(self.emotional_stakes),
        }


def infer_project_profile(brief: dict, llm_client) -> ProjectProfile:
    """Infer a ProjectProfile from the brief. Never raises."""
    if os.environ.get(_KILL_SWITCH_ENV) == "1":
        return ProjectProfile.empty()
    try:
        system, user_template = split_prompt(PROMPT_PATH.read_text(encoding="utf-8"))
        user = user_template.replace(
            "{brief_json}", json.dumps(brief, ensure_ascii=False, default=str)
        )
        raw = llm_client.complete(system=system, user=user)
        data = json.loads(raw)
    except Exception:
        return ProjectProfile.empty()
    if not isinstance(data, dict):
        return ProjectProfile.empty()
    return ProjectProfile(
        vertical=str(data.get("vertical") or ""),
        primary_personas=[str(x) for x in (data.get("primary_personas") or [])],
        key_dimensions=[str(x) for x in (data.get("key_dimensions") or [])],
        emotional_stakes=[str(x) for x in (data.get("emotional_stakes") or [])],
    )


def vertical_relevance(questions, profile: ProjectProfile) -> float:
    """Fraction of questions whose domain is product/behavioral. 0.0 when
    there are no questions or the profile is empty."""
    if profile.is_empty() or not questions:
        return 0.0
    hits = sum(1 for q in questions if q.domain in PRODUCT_BEHAVIORAL_DOMAINS)
    return hits / len(questions)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `PYTHONUTF8=1 python -m pytest tests/unit/debate/test_profile.py -q`
Expected: PASS (8 passed).

- [ ] **Step 6: Commit**

```bash
git switch -c feat/vertical-personalized-questions   # first commit only; skip if branch exists
git add src/ai_dev_system/debate/profile.py src/ai_dev_system/debate/questions/prompts/profile.txt tests/unit/debate/test_profile.py
git commit -m "feat: ProjectProfile + resilient vertical-lens inference"
```

---

## Task 2: Add 3 product/behavioral domains

**Files:**
- Modify: `src/ai_dev_system/debate/domains.py`
- Modify: `src/ai_dev_system/debate/questions/prompts/inventory.txt` (domain list text)
- Test: `tests/unit/debate/test_domains.py` (update cap + add cases)

**Interfaces:**
- Produces: `DOMAINS` now length 15, adding `"psychology"`, `"growth"`, `"research"`; `resolve_domain` recognizes new aliases.

- [ ] **Step 1: Update the failing test**

In `tests/unit/debate/test_domains.py`, change the cap assertion (line ~41) and add new cases:

```python
def test_domain_count_is_fifteen():
    assert len(DOMAINS) == 15


def test_product_behavioral_domains_present():
    for d in ("psychology", "growth", "research"):
        assert d in DOMAINS


def test_new_aliases_resolve():
    from ai_dev_system.debate.domains import resolve_domain
    assert resolve_domain("behavior") == ("psychology", True)
    assert resolve_domain("retention") == ("growth", True)
    assert resolve_domain("user-research") == ("research", True)
```

(Delete or replace the old `assert len(DOMAINS) == 12` so it no longer asserts 12.)

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 python -m pytest tests/unit/debate/test_domains.py -q`
Expected: FAIL — `len(DOMAINS)` is 12, new domains absent.

- [ ] **Step 3: Implement the domain additions**

In `src/ai_dev_system/debate/domains.py`, extend the `DOMAINS` tuple (after `"legal"`):

```python
    "legal",
    "psychology",
    "growth",
    "research",
)
```

Add to `DOMAIN_ALIASES`:

```python
    "behavior": "psychology",
    "behavioral": "psychology",
    "emotion": "psychology",
    "emotional": "psychology",
    "habit": "psychology",
    "retention": "growth",
    "churn": "growth",
    "monetization": "growth",
    "engagement": "growth",
    "acquisition": "growth",
    "user-research": "research",
    "user_research": "research",
    "market": "research",
    "market-research": "research",
    "discovery": "research",
```

Update the module docstring: change "Canonical 12-domain registry" → "Canonical 15-domain registry" and note "extended from 12 by the vertical-personalization work (psychology/growth/research)."

In `src/ai_dev_system/debate/questions/prompts/inventory.txt`, update rule 4's list to include the new domains:

```
4. `domain_hints` must be from the canonical domain registry:
   backend, frontend, mobile, data, ml, security, infra, devops, qa,
   product, design, legal, psychology, growth, research.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 python -m pytest tests/unit/debate/test_domains.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/debate/domains.py src/ai_dev_system/debate/questions/prompts/inventory.txt tests/unit/debate/test_domains.py
git commit -m "feat: add psychology/growth/research domains (cap 12->15)"
```

---

## Task 3: Four product/behavioral personas + register keys

**Files:**
- Modify: `src/ai_dev_system/debate/agents/legacy.py` (add 4 keys to `AGENT_PROMPTS`)
- Create: `references/agency-agents/behavioral_psychologist.md`, `retention_growth_strategist.md`, `ux_researcher.md`, `market_analyst.md`
- Test: `tests/unit/debate/agents/test_registry.py` (update count invariant + add pairing test)

**Interfaces:**
- Consumes: `AgentRegistry`, `pair_suggestion`, `VALID_AGENT_KEYS` (Task-independent existing API).
- Produces: agent keys `BehavioralPsychologist` (domain psychology), `RetentionGrowthStrategist` (growth), `UXResearcher` (research), `MarketAnalyst` (research) — present in both `VALID_AGENT_KEYS` and the `.md` registry.

- [ ] **Step 1: Update/extend the failing tests**

In `tests/unit/debate/agents/test_registry.py`, the existing invariant `assert len(reg) == len(DOMAINS)` (line ~258) no longer holds (16 agents vs 15 domains). Change it to:

```python
    # Agents may exceed domains (research has 2: UXResearcher + MarketAnalyst).
    assert len(reg) >= len(DOMAINS)
```

Add a new test:

```python
def test_product_behavioral_personas_registered_and_pairable():
    from ai_dev_system.debate.agents import AgentRegistry, VALID_AGENT_KEYS
    for key in ("BehavioralPsychologist", "RetentionGrowthStrategist",
                "UXResearcher", "MarketAnalyst"):
        assert key in VALID_AGENT_KEYS
    reg = AgentRegistry.from_directory()
    assert "BehavioralPsychologist" in reg
    # a psychology-domain decision should pair the psychologist with a partner
    partner = reg.pair_suggestion("BehavioralPsychologist", ["growth"])
    assert partner != "BehavioralPsychologist"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 python -m pytest tests/unit/debate/agents/test_registry.py -q`
Expected: FAIL — new keys not in `VALID_AGENT_KEYS`; `.md` files missing.

- [ ] **Step 3: Add legacy fallback prompts (registers the keys)**

In `src/ai_dev_system/debate/agents/legacy.py`, add four entries to `AGENT_PROMPTS` (so `VALID_AGENT_KEYS = set(AGENT_PROMPTS.keys())` includes them):

```python
    "BehavioralPsychologist": (
        "You are a Behavioral Psychologist. Your lens: user psychology, motivation, "
        "habit formation, emotional safety, and trust. Evaluate proposals for how real "
        "people will feel and behave. Argue concisely (max 150 words). Acknowledge trade-offs. "
        "Do not repeat the other agent's points."
    ),
    "RetentionGrowthStrategist": (
        "You are a Retention & Growth Strategist. Your lens: activation, habit loops, "
        "retention/churn drivers, and monetization. Evaluate proposals for long-term "
        "engagement and growth. Argue concisely (max 150 words). Acknowledge trade-offs. "
        "Do not repeat the other agent's points."
    ),
    "UXResearcher": (
        "You are a UX Researcher. Your lens: real user behavior, jobs-to-be-done, usability "
        "evidence, and research-backed assumptions. Challenge claims that lack user evidence. "
        "Argue concisely (max 150 words). Acknowledge trade-offs. Do not repeat the other agent's points."
    ),
    "MarketAnalyst": (
        "You are a Market Analyst. Your lens: target segment, competitive landscape, buyer "
        "behavior, and willingness-to-pay. Evaluate proposals for market fit and differentiation. "
        "Argue concisely (max 150 words). Acknowledge trade-offs. Do not repeat the other agent's points."
    ),
```

- [ ] **Step 4: Create the dense persona `.md` files**

Create `references/agency-agents/behavioral_psychologist.md`:

```markdown
---
agent_key: BehavioralPsychologist
domain: psychology
version: 1
aliases: [psychologist, behavior, behavioral, ux_psych]
debate_role: critic_first
typical_paired_with: [ProductManager, RetentionGrowthStrategist, UXResearcher]
---

# Identity
Bạn là nhà tâm lý học hành vi, chuyên thiết kế sản phẩm số quanh động lực, cảm
xúc và sự tin tưởng của người dùng thật. Bạn đọc được vì sao người ta dùng — hoặc
bỏ — một sản phẩm.

# Mission
Trong debate này, bạn bảo vệ góc nhìn tâm lý & hành vi người dùng: động lực, hình
thành thói quen, an toàn cảm xúc, và sự tin tưởng. Bạn điều chỉnh trọng tâm theo
vertical của dự án (vd app cặp đôi → tâm lý gắn kết & an toàn cảm xúc; marketplace
→ tâm lý ra quyết định & rủi ro cảm nhận).

# Lens
1. **Động lực** — vì sao người dùng hành động; phần thưởng nội tại vs ngoại tại
2. **Thói quen** — trigger → action → reward → investment; tần suất dùng
3. **An toàn cảm xúc** — rủi ro xấu hổ/lo âu/xung đột; quyền kiểm soát của người dùng
4. **Tin tưởng** — minh bạch, riêng tư cảm nhận, hệ quả của sai sót
5. **Hành vi theo vertical** — chuẩn mực & kỳ vọng đặc thù ngành

# Workflow trong 1 debate round
1. Đọc question + previous round summary (nếu có)
2. Chọn 1-2 lens tâm lý relevant nhất với question
3. Nêu quan điểm có cấu trúc: hành vi dự đoán (1 câu) → rủi ro/động lực tâm lý
   (1-2 câu) → đề xuất thiết kế giảm ma sát cảm xúc (1 câu) → trade-off (1 câu)
4. KHÔNG lặp lại điểm agent kia đã nêu

# Deliverable
Position statement, tối đa 200 từ, cấu trúc như Workflow.

# What you DO NOT do
- Không khẳng định hành vi mà không nêu cơ chế tâm lý
- Không bỏ qua chi phí kỹ thuật/sản phẩm khi đề xuất
- Không "người dùng sẽ thích" chung chung — nêu rõ động lực cụ thể

# Tone
Thấu cảm, dựa trên cơ chế hành vi, nhưng thẳng thắn về rủi ro cảm xúc.
```

Create `references/agency-agents/retention_growth_strategist.md`:

```markdown
---
agent_key: RetentionGrowthStrategist
domain: growth
version: 1
aliases: [growth, retention, lifecycle, growth_pm]
debate_role: advocate_first
typical_paired_with: [ProductManager, BehavioralPsychologist, MarketAnalyst]
---

# Identity
Bạn là chuyên gia Retention & Growth, đã vận hành vòng đời người dùng cho nhiều
sản phẩm. Bạn ám ảnh với việc người dùng quay lại ngày 2, tuần 4, tháng 3.

# Mission
Bảo vệ góc nhìn activation, habit loop, retention/churn và monetization. Điều
chỉnh theo vertical (app cặp đôi → nghi thức dùng chung hằng ngày & cột mốc cảm
xúc; marketplace → tần suất giao dịch & lý do quay lại).

# Lens
1. **Activation** — khoảnh khắc "aha" đầu tiên đến nhanh thế nào
2. **Habit loop** — trigger/action/reward/investment giữ người dùng quay lại
3. **Retention & churn** — vì sao rời bỏ; tín hiệu cảnh báo sớm
4. **Monetization** — đường tới doanh thu không phá trải nghiệm
5. **Vòng lan truyền** — referral/network effect nếu có

# Workflow trong 1 debate round
1. Đọc question + previous round summary (nếu có)
2. Chọn 1-2 lens growth relevant nhất
3. Nêu quan điểm: tác động retention/growth (1 câu) → cơ chế giữ chân đề xuất
   (1-2 câu) → metric theo dõi (1 câu) → trade-off (1 câu)
4. KHÔNG lặp lại điểm agent kia

# Deliverable
Position statement, tối đa 200 từ, cấu trúc như Workflow.

# What you DO NOT do
- Không đề xuất growth hack phá lòng tin/an toàn người dùng
- Không bỏ qua chi phí xây dựng
- Không nêu metric mà không gắn hành vi người dùng

# Tone
Định hướng số liệu, tập trung vòng đời, nhưng tôn trọng trải nghiệm dài hạn.
```

Create `references/agency-agents/ux_researcher.md`:

```markdown
---
agent_key: UXResearcher
domain: research
version: 1
aliases: [ux_research, user_research, researcher, discovery]
debate_role: critic_first
typical_paired_with: [ProductManager, BehavioralPsychologist, BackendArchitect]
---

# Identity
Bạn là UX Researcher, người luôn hỏi "bằng chứng đâu?". Bạn phân biệt điều nhóm
build *tin* người dùng muốn với điều nghiên cứu *cho thấy* họ làm.

# Mission
Bảo vệ tính đúng đắn dựa trên bằng chứng người dùng: jobs-to-be-done, hành vi
thực, và giả định cần kiểm chứng. Điều chỉnh theo vertical.

# Lens
1. **Jobs-to-be-done** — người dùng đang "thuê" sản phẩm để làm gì
2. **Bằng chứng** — claim này dựa trên interview/data/anecdote hay phỏng đoán
3. **Giả định rủi ro** — giả định nào sai thì cả hướng đi sụp
4. **Khả năng dùng** — ma sát, mô hình nhận thức, khả năng học
5. **Phương pháp kiểm chứng** — cách rẻ nhất để test giả định trước khi build

# Workflow trong 1 debate round
1. Đọc question + previous round summary (nếu có)
2. Xác định giả định người dùng ẩn trong question
3. Nêu quan điểm: giả định rủi ro nhất (1 câu) → bằng chứng đang thiếu (1-2 câu)
   → cách kiểm chứng rẻ (1 câu) → trade-off (1 câu)
4. KHÔNG lặp lại điểm agent kia

# Deliverable
Position statement, tối đa 200 từ, cấu trúc như Workflow.

# What you DO NOT do
- Không chấp nhận "người dùng muốn X" mà không hỏi nguồn
- Không yêu cầu nghiên cứu vô tận — đề xuất test nhỏ, nhanh
- Không bỏ qua ràng buộc kỹ thuật/thời gian

# Tone
Hoài nghi lành mạnh, đòi bằng chứng, nhưng thực dụng về tốc độ học.
```

Create `references/agency-agents/market_analyst.md`:

```markdown
---
agent_key: MarketAnalyst
domain: research
version: 1
aliases: [market, market_research, competitive, biz_analyst]
debate_role: advocate_first
typical_paired_with: [ProductManager, RetentionGrowthStrategist]
---

# Identity
Bạn là Market Analyst, nhìn sản phẩm qua lăng kính phân khúc, cạnh tranh và sẵn
lòng chi trả. Bạn biết một tính năng tốt vẫn thất bại nếu sai thị trường.

# Mission
Bảo vệ market fit: phân khúc mục tiêu, bối cảnh cạnh tranh, hành vi người mua, và
khác biệt hóa. Điều chỉnh theo vertical.

# Lens
1. **Phân khúc** — ai trả tiền, ai dùng, lớn cỡ nào
2. **Cạnh tranh** — đối thủ đang giải bài này thế nào; khoảng trống ở đâu
3. **Hành vi người mua** — đường ra quyết định, rào cản, kích hoạt mua
4. **Willingness-to-pay** — định giá khả thi, mô hình doanh thu
5. **Khác biệt hóa** — vì sao chọn ta thay vì lựa chọn hiện tại

# Workflow trong 1 debate round
1. Đọc question + previous round summary (nếu có)
2. Chọn 1-2 lens thị trường relevant nhất
3. Nêu quan điểm: tác động market fit (1 câu) → rủi ro cạnh tranh/phân khúc
   (1-2 câu) → khác biệt đề xuất (1 câu) → trade-off (1 câu)
4. KHÔNG lặp lại điểm agent kia

# Deliverable
Position statement, tối đa 200 từ, cấu trúc như Workflow.

# What you DO NOT do
- Không giả định thị trường mà không nêu phân khúc cụ thể
- Không bỏ qua chi phí/khả thi kỹ thuật
- Không copy đối thủ mà không nêu khác biệt

# Tone
Nhạy thị trường, định hướng phân khúc, thực tế về cạnh tranh.
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `PYTHONUTF8=1 python -m pytest tests/unit/debate/agents/test_registry.py -q`
Expected: PASS (registry now loads 16 agents; new keys valid; pairing works).

- [ ] **Step 6: Commit**

```bash
git add src/ai_dev_system/debate/agents/legacy.py references/agency-agents/ tests/unit/debate/agents/test_registry.py
git commit -m "feat: add 4 product/behavioral personas (psychology/growth/research)"
```

---

## Task 4: Inject the lens into legacy `generate_questions`

This is the path the webui/`start` command actually uses — highest-visibility change.

**Files:**
- Modify: `src/ai_dev_system/debate/questions/legacy.py`
- Test: `tests/unit/debate/questions/test_legacy_questions.py` (create if absent)

**Interfaces:**
- Consumes: `ProjectProfile` from Task 1.
- Produces: `generate_questions(brief: dict, llm_client, profile: ProjectProfile | None = None) -> list[Question]`. When `profile` is None or empty, the system prompt is byte-identical to today.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/debate/questions/test_legacy_questions.py`:

```python
import json

from ai_dev_system.debate.questions.legacy import (
    generate_questions, SYSTEM_PROMPT,
)
from ai_dev_system.debate.profile import ProjectProfile


class _CaptureLLM:
    """Captures the system prompt; returns a fixed valid question array."""
    def __init__(self):
        self.system_seen = None
    def complete(self, system: str, user: str) -> str:
        self.system_seen = system
        return json.dumps([{
            "id": "Q1", "text": "How do we drive daily emotional engagement?",
            "classification": "REQUIRED", "domain": "psychology",
            "agent_a": "BehavioralPsychologist", "agent_b": "RetentionGrowthStrategist",
        }])


def test_empty_profile_leaves_system_prompt_unchanged():
    llm = _CaptureLLM()
    generate_questions({"idea": "x"}, llm, profile=ProjectProfile.empty())
    assert llm.system_seen == SYSTEM_PROMPT  # byte-identical to legacy


def test_no_profile_arg_leaves_system_prompt_unchanged():
    llm = _CaptureLLM()
    generate_questions({"idea": "x"}, llm)
    assert llm.system_seen == SYSTEM_PROMPT


def test_profile_injects_dimensions_and_new_agent_keys():
    llm = _CaptureLLM()
    profile = ProjectProfile(
        vertical="couples relationship app",
        primary_personas=["long-distance couples"],
        key_dimensions=["couple psychology", "retention"],
        emotional_stakes=["breakup anxiety"],
    )
    qs = generate_questions({"idea": "x"}, llm, profile=profile)
    assert "couple psychology" in llm.system_seen
    assert "BehavioralPsychologist" in llm.system_seen
    # the new persona keys must survive validation (be accepted, not defaulted)
    assert qs[0].agent_a == "BehavioralPsychologist"
    assert qs[0].domain == "psychology"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 python -m pytest tests/unit/debate/questions/test_legacy_questions.py -q`
Expected: FAIL — `generate_questions()` got an unexpected keyword argument `profile`.

- [ ] **Step 3: Implement the lens injection**

In `src/ai_dev_system/debate/questions/legacy.py`, add a lens-block builder and the `profile` parameter:

```python
def _lens_block(profile) -> str:
    dims = "; ".join(profile.key_dimensions)
    personas = ", ".join(profile.primary_personas) or "the stated users"
    return (
        "\n\nPROJECT PROFILE (personalization lens):\n"
        f"- vertical: {profile.vertical}\n"
        f"- primary users: {personas}\n"
        f"- key product/behavioral dimensions: {dims}\n"
        "In ADDITION to technical questions, generate clarifying questions across "
        "these product/behavioral dimensions (user psychology, daily-usage behavior, "
        "retention/emotion as relevant). Tag such questions with domain one of "
        "psychology, growth, research, product, design. For them, set agent_a/agent_b "
        "from these personas where fitting: BehavioralPsychologist, "
        "RetentionGrowthStrategist, UXResearcher, MarketAnalyst, ProductManager, UXDesigner."
    )


def generate_questions(brief: dict, llm_client, profile=None) -> list[Question]:
    use_brief_v2 = brief.get("brief_version") == 2
    system = SYSTEM_PROMPT_BRIEF_V2 if use_brief_v2 else SYSTEM_PROMPT
    if profile is not None and not profile.is_empty():
        system = system + _lens_block(profile)

    response = llm_client.complete(
        system=system,
        user=json.dumps(brief, ensure_ascii=False),
    )
    # ... (rest of the existing body unchanged) ...
```

Keep the rest of the function (parsing loop) exactly as-is.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONUTF8=1 python -m pytest tests/unit/debate/questions/test_legacy_questions.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/debate/questions/legacy.py tests/unit/debate/questions/test_legacy_questions.py
git commit -m "feat: inject vertical lens into legacy question generator"
```

---

## Task 5: Wire profile inference into the pipeline (closes the loop)

After this task the webui/`start` path is personalized end-to-end.

**Files:**
- Modify: `src/ai_dev_system/debate_pipeline.py`
- Test: `tests/unit/test_debate_pipeline_profile.py` (create)

**Interfaces:**
- Consumes: `infer_project_profile` (Task 1), `generate_questions(..., profile=...)` (Task 4).
- Produces: `_question_path(flags, brief_v1, brief_v2, llm_client, profile=None)`; `run_debate_pipeline` infers the profile once, stamps `brief["_project_profile"]`, and threads `profile` into `_question_path`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_debate_pipeline_profile.py`:

```python
from ai_dev_system.debate_pipeline import _question_path
from ai_dev_system.feature_flags import FeatureFlags
from ai_dev_system.debate.profile import ProjectProfile


class _CaptureLLM:
    def __init__(self):
        self.system_seen = None
    def complete(self, system: str, user: str) -> str:
        self.system_seen = system
        import json
        return json.dumps([{
            "id": "Q1", "text": "t", "classification": "REQUIRED",
            "domain": "psychology", "agent_a": "BehavioralPsychologist",
            "agent_b": "ProductManager",
        }])


def test_question_path_threads_profile_into_legacy():
    llm = _CaptureLLM()
    profile = ProjectProfile(vertical="couples app", primary_personas=[],
                             key_dimensions=["couple psychology"], emotional_stakes=[])
    flags = FeatureFlags()  # all off → legacy path
    questions, decisions, digest = _question_path(
        flags, {"idea": "x"}, None, llm, profile=profile,
    )
    assert decisions is None  # legacy path
    assert "couple psychology" in llm.system_seen  # lens reached the generator


def test_question_path_without_profile_is_legacy_default():
    llm = _CaptureLLM()
    flags = FeatureFlags()
    _question_path(flags, {"idea": "x"}, None, llm)  # profile defaults to None
    # no lens block appended
    assert "PROJECT PROFILE" not in (llm.system_seen or "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 python -m pytest tests/unit/test_debate_pipeline_profile.py -q`
Expected: FAIL — `_question_path()` got an unexpected keyword argument `profile`.

- [ ] **Step 3: Implement the wiring**

In `src/ai_dev_system/debate_pipeline.py`:

(a) Add the import near the other debate imports:

```python
from ai_dev_system.debate.profile import infer_project_profile
```

(b) Change `_question_path` signature and body to accept and forward `profile`:

```python
def _question_path(
    flags: FeatureFlags,
    brief_v1: dict,
    brief_v2: dict | None,
    llm_client,
    profile=None,
):
    if flags.use_question_pipeline_v2 and brief_v2 is not None:
        digest = build_brief_digest(brief_v2)
        result = run_question_pipeline_v2(brief_v2, digest, llm_client)  # profile added in Task 6
        return result.questions_final, result.decisions, digest

    if flags.use_question_pipeline_v2 and brief_v2 is None:
        warnings.warn(
            "use_question_pipeline_v2=true but no brief_v2 supplied; "
            "falling back to legacy generate_questions.",
            stacklevel=2,
        )

    questions = generate_questions(brief_v1, llm_client, profile=profile)
    return questions, None, None
```

NOTE: this task threads `profile` into the **legacy branch only** (the path the webui/`start` command uses). The v2 branch call deliberately does NOT pass `profile` yet — `run_pipeline` does not accept it until Task 6, which also updates this v2 branch. Keeping it out here means the suite stays green after this task.

(c) In `run_debate_pipeline`, after the `brief = {**brief, "_flags": ...}` line (~181), infer + stamp the profile, then pass it into `_question_path` (~192):

```python
    # Infer the vertical lens once; stamp it onto the brief so it travels with
    # the DebateReport artifact (inspectable at Gate 1 + reusable by Spec 2).
    profile = infer_project_profile(brief_v2 or brief, llm_client)
    brief = {**brief, "_project_profile": profile.to_dict()}

    ...
    questions, decisions, digest = _question_path(
        active_flags, brief, brief_v2, llm_client, profile=profile,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONUTF8=1 python -m pytest tests/unit/test_debate_pipeline_profile.py -q`
Expected: PASS.

- [ ] **Step 5: Run the broader pipeline tests for regressions**

Run: `PYTHONUTF8=1 python -m pytest tests/unit/test_debate_pipeline_dispatch.py -q`
Expected: PASS — but the registry-size assertion (line ~197 `== 12`) will FAIL until you update it. Change it to `== 16`:

```python
    assert len(kwargs["registry"]) == 16
```

Re-run; Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ai_dev_system/debate_pipeline.py tests/unit/test_debate_pipeline_profile.py tests/unit/test_debate_pipeline_dispatch.py
git commit -m "feat: infer + thread vertical profile through debate pipeline"
```

---

## Task 6: Inject the lens into v2 inventory + materializer

**Files:**
- Modify: `src/ai_dev_system/debate/questions/inventory.py` + `prompts/inventory.txt`
- Modify: `src/ai_dev_system/debate/questions/materializer.py` + `prompts/materializer.txt`
- Modify: `src/ai_dev_system/debate/questions/pipeline.py`
- Test: `tests/unit/debate/questions/test_lens_injection_v2.py` (create)

**Interfaces:**
- Consumes: `ProjectProfile`.
- Produces:
  - `inventory.run(brief_v2, llm_client, profile=None)`
  - `materializer.run(decisions, brief_digest, llm_client, *, mode="fresh", profile=None)`
  - `pipeline.run_pipeline(brief_v2, brief_digest, llm_client, profile=None)`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/debate/questions/test_lens_injection_v2.py`:

```python
import json

from ai_dev_system.debate.questions import inventory, materializer
from ai_dev_system.debate.questions.models import Decision
from ai_dev_system.debate.profile import ProjectProfile


class _CaptureLLM:
    def __init__(self, response):
        self.system_seen = None
        self.user_seen = None
        self._response = response
    def complete(self, system, user):
        self.system_seen = system
        self.user_seen = user
        return self._response


_INV_RESPONSE = json.dumps([
    {"id": f"d{i}", "summary": "s", "classification": "REQUIRED",
     "domain_hints": ["psychology"], "blocks_what": [], "has_safe_default": False,
     "brief_field_refs": ["scope_in"]}
    for i in range(8)
])


def test_inventory_injects_profile_into_user_prompt():
    llm = _CaptureLLM(_INV_RESPONSE)
    profile = ProjectProfile("couples app", [], ["couple psychology"], [])
    inventory.run({"scope_in": []}, llm, profile=profile)
    assert "couple psychology" in llm.user_seen


def test_inventory_empty_profile_no_profile_text():
    llm = _CaptureLLM(_INV_RESPONSE)
    inventory.run({"scope_in": []}, llm, profile=ProjectProfile.empty())
    assert "PROJECT PROFILE" not in llm.user_seen


def test_materializer_injects_profile():
    resp = json.dumps([{"text": "q?", "domain": "psychology",
                        "agent_a": "BehavioralPsychologist", "agent_b": "ProductManager",
                        "source_decision_id": "d1"}])
    llm = _CaptureLLM(resp)
    profile = ProjectProfile("couples app", [], ["couple psychology"], [])
    decisions = [Decision(id="d1", summary="s", classification="REQUIRED",
                          domain_hints=["psychology"], blocks_what=["f"], has_safe_default=False)]
    materializer.run(decisions, "digest", llm, profile=profile)
    assert "couple psychology" in llm.user_seen
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 python -m pytest tests/unit/debate/questions/test_lens_injection_v2.py -q`
Expected: FAIL — `run()` got an unexpected keyword argument `profile`.

- [ ] **Step 3: Add a shared profile-block helper**

Add to `src/ai_dev_system/debate/profile.py`:

```python
def profile_prompt_block(profile: "ProjectProfile") -> str:
    """Render an injectable PROJECT PROFILE block, or '' when empty."""
    if profile is None or profile.is_empty():
        return ""
    dims = "; ".join(profile.key_dimensions)
    personas = ", ".join(profile.primary_personas) or "the stated users"
    return (
        "PROJECT PROFILE (personalization lens):\n"
        f"- vertical: {profile.vertical}\n"
        f"- primary users: {personas}\n"
        f"- key product/behavioral dimensions: {dims}\n"
        "ALSO surface product/behavioral items across these dimensions; tag them "
        "with domain one of psychology, growth, research, product, design.\n"
    )
```

Add a unit test for it in `tests/unit/debate/test_profile.py`:

```python
def test_profile_prompt_block_empty_is_blank():
    from ai_dev_system.debate.profile import profile_prompt_block
    assert profile_prompt_block(ProjectProfile.empty()) == ""
```

- [ ] **Step 4: Update inventory**

In `prompts/inventory.txt`, add the placeholder at the top of the USER section and a rule in SYSTEM. New USER section:

```
USER
{project_profile}
{brief_v2_json}
```

Add to SYSTEM (after rule 5):

```
6. If a PROJECT PROFILE block is present, ALSO enumerate product/behavioral
   decisions across its key_dimensions (user psychology, daily-usage behavior,
   retention, emotion as relevant), tagged with domain psychology/growth/
   research/product/design. These count toward the 8–25 total.
```

In `src/ai_dev_system/debate/questions/inventory.py`, update `run` and the user render:

```python
from ai_dev_system.debate.profile import profile_prompt_block

def run(brief_v2: dict, llm_client, profile=None) -> list[Decision]:
    system, user_template = _split_prompt(load_prompt())
    brief_json = json.dumps(brief_v2, ensure_ascii=False, indent=2)
    user = (
        user_template
        .replace("{project_profile}", profile_prompt_block(profile))
        .replace("{brief_v2_json}", brief_json)
    )
    # ... retry loop unchanged, but on retry rebuild from `user` as today ...
```

(The existing retry loop appends to `user`; keep it.)

- [ ] **Step 5: Update materializer**

In `prompts/materializer.txt`, add the placeholder to the USER section:

```
USER
{project_profile}
DECISIONS:
{decisions_json}

BRIEF DIGEST:
{brief_digest}
```

Add to SYSTEM (after rule 5):

```
6. For product/behavioral questions (domain psychology/growth/research/product/
   design), pair agent_a/agent_b from: BehavioralPsychologist,
   RetentionGrowthStrategist, UXResearcher, MarketAnalyst, ProductManager, UXDesigner.
```

In `src/ai_dev_system/debate/questions/materializer.py`, thread `profile` through `run`, `_materialize_batch`, `_materialize_per_decision`, and `_render_user`:

```python
from ai_dev_system.debate.profile import profile_prompt_block

def _render_user(user_template, decisions, brief_digest, profile=None):
    payload = [_decision_to_payload(d) for d in decisions]
    decisions_json = json.dumps(payload, ensure_ascii=False, indent=2)
    return (
        user_template
        .replace("{project_profile}", profile_prompt_block(profile))
        .replace("{decisions_json}", decisions_json)
        .replace("{brief_digest}", brief_digest)
    )
```

Add `profile=None` to `run`, `_materialize_batch`, `_materialize_per_decision` signatures and pass it into every `_render_user(...)` call.

- [ ] **Step 6: Thread through the orchestrator**

In `src/ai_dev_system/debate/questions/pipeline.py`, add `profile=None` to `run_pipeline` and forward it to **inventory + materializer only** (coverage gets it in Task 7, which also accepts the param):

```python
def run_pipeline(brief_v2, brief_digest, llm_client, profile=None):
    decisions = inventory.run(brief_v2, llm_client, profile=profile)
    draft = materializer.run(decisions, brief_digest, llm_client, mode="fresh", profile=profile)
    refined, iterations = critic.run(draft, brief_digest, llm_client)
    report = coverage.run(refined, decisions, brief_v2)  # profile added in Task 7
    # ... retrigger block: materializer.run(missing_decisions, ..., mode="retrigger", profile=profile)
    #     coverage.run(refined, decisions, brief_v2) stays unchanged until Task 7 ...
```

Also update `_question_path` in `src/ai_dev_system/debate_pipeline.py` to now pass `profile` into the v2 branch (the line left as `# profile added in Task 6` in Task 5):

```python
        result = run_question_pipeline_v2(brief_v2, digest, llm_client, profile=profile)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `PYTHONUTF8=1 python -m pytest tests/unit/debate/questions/test_lens_injection_v2.py tests/unit/debate/test_profile.py -q`
Expected: PASS.

Run the existing v2 pipeline/inventory/materializer suites for regressions:
Run: `PYTHONUTF8=1 python -m pytest tests/unit/debate/questions -q`
Expected: PASS (empty-profile renders `{project_profile}` → "" so prompts to the stub are unchanged in routing).

- [ ] **Step 8: Commit**

```bash
git add src/ai_dev_system/debate/profile.py src/ai_dev_system/debate/questions/inventory.py src/ai_dev_system/debate/questions/materializer.py src/ai_dev_system/debate/questions/pipeline.py src/ai_dev_system/debate/questions/prompts/ tests/unit/debate/
git commit -m "feat: inject vertical lens into v2 inventory + materializer"
```

---

## Task 7: C5 personalization coverage (WARN)

**Files:**
- Modify: `src/ai_dev_system/debate/questions/models.py` (literal)
- Modify: `src/ai_dev_system/debate/questions/coverage.py`
- Test: `tests/unit/debate/questions/test_coverage.py` (add cases)

**Interfaces:**
- Consumes: `ProjectProfile`, `PRODUCT_BEHAVIORAL_DOMAINS`.
- Produces: `coverage.run(questions, decisions, brief_v2, profile=None)`; new check `C5_personalization` (status `pass`/`warn`, never `fail`).

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/debate/questions/test_coverage.py`:

```python
from ai_dev_system.debate.profile import ProjectProfile
from ai_dev_system.debate.questions import coverage
from ai_dev_system.debate.questions.models import Decision
from ai_dev_system.debate.report import Question


def _q(domain):
    return Question(id="Q", text="t", classification="REQUIRED", domain=domain,
                    agent_a="ProductManager", agent_b="BackendArchitect",
                    source_decision_id="d1")


def _c5(report):
    return next(c for c in report.checks if c.name == "C5_personalization")


def test_c5_warns_when_profile_set_but_no_product_questions():
    profile = ProjectProfile("couples app", [], ["couple psychology"], [])
    decisions = [Decision(id="d1", summary="s", classification="REQUIRED")]
    report = coverage.run([_q("backend")], decisions, {}, profile=profile)
    assert _c5(report).status == "warn"
    assert report.is_pass() is True  # WARN never blocks


def test_c5_passes_when_product_question_present():
    profile = ProjectProfile("couples app", [], ["couple psychology"], [])
    decisions = [Decision(id="d1", summary="s", classification="REQUIRED")]
    report = coverage.run([_q("psychology")], decisions, {}, profile=profile)
    assert _c5(report).status == "pass"


def test_c5_passes_when_profile_empty():
    decisions = [Decision(id="d1", summary="s", classification="REQUIRED")]
    report = coverage.run([_q("backend")], decisions, {}, profile=ProjectProfile.empty())
    assert _c5(report).status == "pass"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 python -m pytest tests/unit/debate/questions/test_coverage.py -q`
Expected: FAIL — `run()` got an unexpected keyword argument `profile`; no `C5_personalization` check.

- [ ] **Step 3: Implement C5**

In `src/ai_dev_system/debate/questions/models.py`, extend the literal:

```python
CoverageCheckName = Literal[
    "C1_decision_coverage",
    "C2_domain_balance",
    "C3_classification_sanity",
    "C4_question_count",
    "C5_personalization",
]
```

In `src/ai_dev_system/debate/questions/coverage.py`:

```python
from ai_dev_system.debate.profile import PRODUCT_BEHAVIORAL_DOMAINS


def check_c5_personalization(questions, profile) -> CoverageCheck:
    """WARN when a vertical profile is present but no question lands in a
    product/behavioral domain — personalization was likely dropped."""
    if profile is None or profile.is_empty():
        return CoverageCheck(name="C5_personalization", status="pass",
                             detail={"reason": "no profile"})
    product = [q for q in questions if q.domain in PRODUCT_BEHAVIORAL_DOMAINS]
    return CoverageCheck(
        name="C5_personalization",
        status="pass" if product else "warn",
        detail={"product_question_count": len(product), "total": len(questions)},
    )
```

Update `run` to accept `profile` and append C5:

```python
def run(questions, decisions, brief_v2, profile=None) -> CoverageReport:
    _ = brief_v2
    checks = [
        check_c1_decision_coverage(questions, decisions),
        check_c2_domain_balance(questions, decisions),
        check_c3_classification_sanity(questions),
        check_c4_question_count(questions, decisions),
        check_c5_personalization(questions, profile),
    ]
    # ... rest unchanged (c1 = checks[0]; build CoverageReport) ...
```

`BLOCKING_CHECKS` stays `("C1_decision_coverage", "C4_question_count")` — C5 never blocks.

Finally, now that `coverage.run` accepts `profile`, update the two call sites in `src/ai_dev_system/debate/questions/pipeline.py` to forward it:

```python
    report = coverage.run(refined, decisions, brief_v2, profile=profile)
    ...
    # inside the C1-retrigger block, the re-check:
    report = coverage.run(refined, decisions, brief_v2, profile=profile)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONUTF8=1 python -m pytest tests/unit/debate/questions/test_coverage.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/debate/questions/models.py src/ai_dev_system/debate/questions/coverage.py src/ai_dev_system/debate/questions/pipeline.py tests/unit/debate/questions/test_coverage.py
git commit -m "feat: C5 personalization coverage check (WARN)"
```

---

## Task 8: Full-suite regression + `vertical_relevance` integration note

**Files:**
- Test: run the whole suite.

- [ ] **Step 1: Run the full unit suite**

Run: `PYTHONUTF8=1 python -m pytest tests/unit -q`
Expected: PASS. If any pre-existing test asserts a domain count of 12 or registry size 12 that this plan did not touch, update it to the new values (15 domains, 16 agents) — search with:

Run: `PYTHONUTF8=1 python -m pytest tests/unit -q -k "domain or registry or dispatch"`

- [ ] **Step 2: Run integration tests**

Run: `PYTHONUTF8=1 python -m pytest tests/integration -q`
Expected: PASS (these exercise the pipeline; the empty-profile-under-stub invariant keeps them green).

- [ ] **Step 3: Commit any test count fixes**

```bash
git add -A
git commit -m "test: update domain/agent count assertions for 15 domains / 16 agents"
```

- [ ] **Step 4: Follow-up note (NOT implemented here)**

`vertical_relevance(questions, profile)` (Task 1) is the metric primitive. Wiring it into the `ai-dev eval` golden dataset (a couples golden idea + a `vertical_relevance` column) requires exploring `src/ai_dev_system/eval/` and is intentionally deferred to a focused follow-up so this plan stays a single reviewable slice. Record this as a TODO in the project tracker; do not leave a stub in code.

---

## Manual verification (after all tasks)

1. Restart the webui (`PYTHONUTF8=1 python -m ai_dev_system.webui`) with a **real** Claude Max client (not stub) and start a couples-app project.
2. Confirm the generated questions include product/behavioral ones (psychology/growth/research domains) and that personas like `BehavioralPsychologist` appear in the debate.
3. Open `debate_report.json` and confirm `brief._project_profile` is populated with a sensible `vertical` + `key_dimensions`.
4. Start a stub-mode project and confirm questions are unchanged from before (empty profile → no lens).

## Self-Review

- [ ] **Spec coverage:** Profile inference (T1), 3 domains (T2), 4 personas (T3), legacy lens (T4), pipeline wiring (T5), v2 lens (T6), C5 coverage (T7), metric primitive + regression (T8). Spec §4.1–§4.6 all mapped. Eval golden dataset wiring explicitly deferred (T8 step 4).
- [ ] **Placeholder scan:** none — every step has concrete code/commands.
- [ ] **Type consistency:** `ProjectProfile` fields, `infer_project_profile`, `profile_prompt_block`, `PRODUCT_BEHAVIORAL_DOMAINS`, `vertical_relevance`, and the `profile=None` kwarg names are identical across T1/T4/T5/T6/T7.
```
