# Bot Clarifying-Questions in Telegram — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the repo-bound bot's agentic spec surfaces blocking findings, the bot asks the user a clarifying question in Telegram, merges the answer, and regenerates the spec — instead of silently waiting for "duyệt".

**Architecture:** The worker subprocess pre-generates the questions into `spec.clarify` (all LLM work stays off the daemon thread). A new `ClarifyWatcher`, swept in the gateway `post_poll_hook` next to `RunStatusWatcher`, pushes the question and appends it to the session. `dev_answer_clarify` re-runs the worker with the answer merged in; `dev_run_status` is the pull fallback. The loop is capped at 2 rounds.

**Tech Stack:** Python 3.12, SQLite, claude_agent_sdk `@tool`, claude_code LLM provider, pytest.

**Spec:** `docs/superpowers/specs/2026-06-30-bot-clarify-in-telegram-design.md`

**Suggested models:** T1,T3 = haiku (mechanical/transcription); T2,T4,T5,T6,T7 = sonnet (integration); final whole-branch review = opus.

## Global Constraints

- **No LLM on the daemon thread.** Question synthesis and re-spec run only inside the `single_task_worker` subprocess. `ClarifyWatcher` and the `dev_*` tools do JSON reads + spawns only — never call an LLM.
- **Blocking rule (exact):** a clarification is needed iff the spec has any `finding` with `severity == "error"` OR any facet with `status == "needs_human"`. Warnings never block.
- **Round cap = 2.** A pending record's `round` gates push (`< 2`); `dev_answer_clarify` increments it. At `round >= 2` the flow falls through to the plan path.
- **`clarify` key is always written** by the worker on the success path: `{"needed": bool, "questions": [str, ...]}`.
- Worker JSON is written with `json.dumps(payload, indent=2, ensure_ascii=False)` (Vietnamese must not be `\uXXXX`-escaped).
- Tools are invoked in tests as `tool.handler({...})` — `SdkMcpTool` has no `__call__`.
- One pending task per chat (the existing vertical slice; do not add multi-task state).
- **README test-count chore:** after adding tests, run the full suite; if `tests/unit/test_docs_reconciliation.py` fails, set the test count in `README.md` to the collected number it reports. Do this in the same commit as the tests.

## File Structure

| File | Responsibility | Task |
|------|----------------|------|
| `src/ai_dev_system/task_graph/clarify_questions.py` (new) | `find_blocking`, `synthesize_questions`, `format_questions` — pure + LLM-injected | T1 |
| `src/ai_dev_system/task_graph/single_task_worker.py` | write `spec.clarify` after building payload | T2 |
| `src/ai_dev_system/harness/tools/chat_task_store.py` | record gains `surface/chat_id/idea/phase/round`; `update`, `list_pending` | T3 |
| `src/ai_dev_system/gateway/clarify_watcher.py` (new) | `ClarifyWatcher` — push + session append, no LLM | T4 |
| `src/ai_dev_system/cli/commands/gateway.py` | construct `ClarifyWatcher`, run both sweeps in `post_poll_hook` | T5 |
| `src/ai_dev_system/harness/tools/dev_pipeline.py` | `dev_task_start` stores idea; `dev_run_status` clarify branch; new `dev_answer_clarify` | T6 |
| `src/ai_dev_system/assistant/factory.py` | per-chat system-prompt: repo binding + clarify routing | T7 |

---

### Task 1: clarify_questions module

**Files:**
- Create: `src/ai_dev_system/task_graph/clarify_questions.py`
- Test: `tests/unit/task_graph/test_clarify_questions.py`

**Interfaces:**
- Produces:
  - `find_blocking(spec: dict) -> list[dict]` — items `{"kind": "finding"|"facet", "key": str, "message": str}`
  - `synthesize_questions(blocking: list[dict], *, idea: str, llm) -> list[str]` — 1–3 questions; `llm` may be `None`; never raises
  - `format_questions(questions: list[str]) -> str` — Telegram message body

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/task_graph/test_clarify_questions.py
from ai_dev_system.task_graph.clarify_questions import (
    find_blocking, synthesize_questions, format_questions,
)


def test_find_blocking_selects_error_findings_and_needs_human_facets():
    spec = {
        "findings": [
            {"section": "business_rule", "severity": "error", "message": "GUID vs PK?"},
            {"section": "test_cases", "severity": "warning", "message": "minor"},
        ],
        "facets": {
            "security_rules": {"status": "needs_human", "content": "enumeration risk"},
            "input": {"status": "filled", "content": "ok"},
        },
    }
    blocking = find_blocking(spec)
    keys = {(b["kind"], b["key"]) for b in blocking}
    assert ("finding", "business_rule") in keys
    assert ("facet", "security_rules") in keys
    assert ("finding", "test_cases") not in keys      # warning excluded
    assert ("facet", "input") not in keys             # filled excluded


def test_find_blocking_empty_when_clean():
    assert find_blocking({"findings": [], "facets": {"input": {"status": "filled"}}}) == []
    assert find_blocking({}) == []                     # missing keys tolerated


def test_synthesize_questions_uses_llm_json_array():
    class FakeLLM:
        def complete(self, *, system, user):
            return '```json\n["OwnerId nên là GUID thật hay ID số?"]\n```'
    qs = synthesize_questions(
        [{"kind": "finding", "key": "business_rule", "message": "GUID vs PK"}],
        idea="add OwnerId", llm=FakeLLM(),
    )
    assert qs == ["OwnerId nên là GUID thật hay ID số?"]


def test_synthesize_questions_falls_back_on_llm_error():
    class BoomLLM:
        def complete(self, *, system, user):
            raise RuntimeError("llm down")
    qs = synthesize_questions(
        [{"kind": "finding", "key": "business_rule", "message": "GUID vs PK contradiction"}],
        idea="x", llm=BoomLLM(),
    )
    assert qs and "GUID vs PK contradiction" in qs[0]


def test_synthesize_questions_fallback_when_llm_none():
    qs = synthesize_questions(
        [{"kind": "facet", "key": "security_rules", "message": "enumeration risk"}],
        idea="x", llm=None,
    )
    assert qs == ["enumeration risk"]


def test_synthesize_questions_empty_for_no_blocking():
    assert synthesize_questions([], idea="x", llm=None) == []


def test_format_questions_numbers_and_frames():
    out = format_questions(["A?", "B?"])
    assert "1. A?" in out and "2. B?" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/task_graph/test_clarify_questions.py -v`
Expected: FAIL with `ModuleNotFoundError: ai_dev_system.task_graph.clarify_questions`

- [ ] **Step 3: Write the implementation**

```python
# src/ai_dev_system/task_graph/clarify_questions.py
"""Turn blocking spec findings into user-facing clarifying questions.

find_blocking: pure selection of error findings + needs_human facets.
synthesize_questions: collapse them into 1-3 Vietnamese questions via an injected
LLM, falling back to the raw messages on any failure (or when llm is None).
format_questions: render the Telegram message body.

No module here touches the network directly; the worker injects the LLM client so
this stays unit-testable and so the gateway can import format_questions cheaply.
"""
from __future__ import annotations

import json


def find_blocking(spec: dict) -> list[dict]:
    out: list[dict] = []
    for f in spec.get("findings") or []:
        if isinstance(f, dict) and f.get("severity") == "error":
            out.append({"kind": "finding", "key": f.get("section", "") or "",
                        "message": (f.get("message") or "").strip()})
    for key, facet in (spec.get("facets") or {}).items():
        if isinstance(facet, dict) and facet.get("status") == "needs_human":
            msg = (facet.get("content") or facet.get("reason") or "").strip()
            out.append({"kind": "facet", "key": key, "message": msg})
    return out


def _strip_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        nl = t.find("\n")
        if nl != -1:
            close = t.rfind("```")
            return t[nl + 1:close].strip() if close > nl else t[nl + 1:].strip()
    return t


_SYNTH_SYSTEM = (
    "Bạn là trợ lý kỹ thuật. Dưới đây là các điểm CHẶN mà hệ thống phát hiện khi "
    "đối chiếu yêu cầu với codebase thật. Hãy gộp chúng thành 1-3 CÂU HỎI ngắn gọn, "
    "rõ ràng bằng tiếng Việt để hỏi người yêu cầu — mỗi câu là một quyết định họ cần "
    "chốt. KHÔNG giải thích. Trả về DUY NHẤT một mảng JSON các chuỗi câu hỏi."
)


def synthesize_questions(blocking: list[dict], *, idea: str, llm) -> list[str]:
    if not blocking:
        return []
    if llm is not None:
        try:
            user = (
                "Yêu cầu của người dùng:\n" + (idea or "") + "\n\nCác điểm chặn:\n"
                + "\n".join(f"- [{b['kind']}/{b['key']}] {b['message']}" for b in blocking)
            )
            raw = llm.complete(system=_SYNTH_SYSTEM, user=user)
            parsed = json.loads(_strip_fence(raw))
            qs = [str(q).strip() for q in parsed if str(q).strip()]
            if qs:
                return qs[:3]
        except Exception:  # noqa: BLE001 — any failure → raw fallback below
            pass
    return [b["message"][:300] for b in blocking if b["message"]][:3]


def format_questions(questions: list[str]) -> str:
    lines = ["🤔 Mình cần bạn làm rõ vài điểm trước khi tiếp tục:"]
    for i, q in enumerate(questions, 1):
        lines.append(f"{i}. {q}")
    lines.append("\nBạn trả lời thẳng trong tin nhắn tiếp theo nhé.")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/task_graph/test_clarify_questions.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit** (bump README count if `test_docs_reconciliation` fails)

```bash
git add src/ai_dev_system/task_graph/clarify_questions.py tests/unit/task_graph/test_clarify_questions.py README.md
git commit -m "feat(clarify): find_blocking + synthesize_questions + format_questions"
```

---

### Task 2: worker writes spec.clarify

**Files:**
- Modify: `src/ai_dev_system/task_graph/single_task_worker.py` (the `run_worker` success path, after the `payload`/`findings` block at lines 111-117)
- Test: `tests/unit/task_graph/test_single_task_worker_clarify.py`

**Interfaces:**
- Consumes: `find_blocking`, `synthesize_questions` (Task 1); `make_llm_client("spec")` (existing)
- Produces: `<storage_root>/task_specs/<id>.json` now carries `clarify: {"needed": bool, "questions": [...]}`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/task_graph/test_single_task_worker_clarify.py
import json
from pathlib import Path

import ai_dev_system.task_graph.single_task_worker as w


def _patch_common(monkeypatch, result):
    monkeypatch.setattr(w, "spec_single_task", lambda *a, **k: result)
    # agentic path passes repo → worker builds no llm for facets, but DOES build one
    # for synthesis; make that a no-op so we exercise the fallback deterministically.
    monkeypatch.setattr(w, "make_llm_client", lambda step: None)
    monkeypatch.setattr(w, "_record_run_row", lambda *a, **k: None)


def test_worker_writes_clarify_needed_true(tmp_path, monkeypatch):
    result = {
        "task": {"title": "t"},
        "facets": {"security_rules": {"status": "needs_human", "content": "enum risk"}},
        "findings": [{"section": "business_rule", "severity": "error", "message": "GUID vs PK"}],
    }
    _patch_common(monkeypatch, result)
    w.run_worker("abc", "add OwnerId", "/repo", storage_root=str(tmp_path), database_url="sqlite://")
    spec = json.loads((tmp_path / "task_specs" / "abc.json").read_text(encoding="utf-8"))
    assert spec["clarify"]["needed"] is True
    assert spec["clarify"]["questions"]            # non-empty (fallback used)


def test_worker_writes_clarify_needed_false_when_clean(tmp_path, monkeypatch):
    result = {"task": {"title": "t"},
              "facets": {"input": {"status": "filled", "content": "ok"}},
              "findings": []}
    _patch_common(monkeypatch, result)
    w.run_worker("def", "x", "/repo", storage_root=str(tmp_path), database_url="sqlite://")
    spec = json.loads((tmp_path / "task_specs" / "def.json").read_text(encoding="utf-8"))
    assert spec["clarify"] == {"needed": False, "questions": []}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/task_graph/test_single_task_worker_clarify.py -v`
Expected: FAIL — `KeyError: 'clarify'`

- [ ] **Step 3: Implement** — insert clarify computation into `run_worker` after the `findings` handling (current line 117), still inside the `try`, before `_spec_log(log_path, "Hoàn thành ✓")`.

Add this import near the top (with the other `ai_dev_system.task_graph` imports):
```python
from ai_dev_system.task_graph.clarify_questions import find_blocking, synthesize_questions
```

Insert after the `if _findings: payload["findings"] = _findings` block:
```python
        # Pre-generate clarifying questions for blocking findings so the gateway
        # ClarifyWatcher can push them WITHOUT any LLM call on the daemon thread.
        blocking = find_blocking(payload)
        if blocking:
            try:
                synth_llm = make_llm_client("spec")
            except Exception:  # noqa: BLE001
                synth_llm = None
            questions = synthesize_questions(blocking, idea=idea, llm=synth_llm)
            _spec_log(log_path, f"Cần làm rõ: {len(questions)} câu hỏi (blocking={len(blocking)})")
        else:
            questions = []
        payload["clarify"] = {"needed": bool(blocking), "questions": questions}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/task_graph/test_single_task_worker_clarify.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/task_graph/single_task_worker.py tests/unit/task_graph/test_single_task_worker_clarify.py README.md
git commit -m "feat(clarify): worker writes spec.clarify with pre-generated questions"
```

---

### Task 3: ChatTaskStore — new fields, update, list_pending

**Files:**
- Modify: `src/ai_dev_system/harness/tools/chat_task_store.py`
- Test: `tests/unit/harness/tools/test_chat_task_store_clarify.py`

**Interfaces:**
- Produces:
  - `set_pending(surface, chat_id, *, spec_id, repo, base_branch, idea="", phase="generating", round=0)` — record body also stores `surface`, `chat_id`, `pr_url=None`, `clarify_questions=[]`
  - `update(surface, chat_id, **fields) -> None` — partial merge into the existing record (no-op if missing)
  - `list_pending() -> list[dict]` — every record dict under `chat_tasks/`, each carrying `surface`/`chat_id`; unreadable files skipped

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/harness/tools/test_chat_task_store_clarify.py
from ai_dev_system.harness.tools.chat_task_store import ChatTaskStore


def test_set_pending_stores_new_fields(tmp_path):
    s = ChatTaskStore(str(tmp_path))
    s.set_pending("Sigo", "5913", spec_id="ab", repo="/r", base_branch="main",
                  idea="add OwnerId")
    rec = s.get_pending("Sigo", "5913")
    assert rec["idea"] == "add OwnerId"
    assert rec["phase"] == "generating"
    assert rec["round"] == 0
    assert rec["surface"] == "Sigo" and rec["chat_id"] == "5913"


def test_update_partial(tmp_path):
    s = ChatTaskStore(str(tmp_path))
    s.set_pending("Sigo", "5913", spec_id="ab", repo="/r", base_branch="main", idea="x")
    s.update("Sigo", "5913", phase="awaiting_clarify", clarify_questions=["Q?"])
    rec = s.get_pending("Sigo", "5913")
    assert rec["phase"] == "awaiting_clarify"
    assert rec["clarify_questions"] == ["Q?"]
    assert rec["spec_id"] == "ab"                 # untouched fields preserved


def test_update_missing_is_noop(tmp_path):
    s = ChatTaskStore(str(tmp_path))
    s.update("nope", "0", phase="x")              # must not raise
    assert s.get_pending("nope", "0") is None


def test_list_pending_returns_all_with_routing(tmp_path):
    s = ChatTaskStore(str(tmp_path))
    s.set_pending("A", "1", spec_id="a", repo="/r", base_branch="m", idea="i1")
    s.set_pending("B", "2", spec_id="b", repo="/r", base_branch="m", idea="i2")
    recs = {(r["surface"], r["chat_id"]) for r in s.list_pending()}
    assert recs == {("A", "1"), ("B", "2")}


def test_list_pending_skips_corrupt(tmp_path):
    s = ChatTaskStore(str(tmp_path))
    s.set_pending("A", "1", spec_id="a", repo="/r", base_branch="m", idea="i")
    (tmp_path / "chat_tasks" / "broken__x.json").write_text("{not json", encoding="utf-8")
    recs = s.list_pending()
    assert len(recs) == 1 and recs[0]["surface"] == "A"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/harness/tools/test_chat_task_store_clarify.py -v`
Expected: FAIL — `set_pending() got an unexpected keyword argument 'idea'`

- [ ] **Step 3: Implement** — replace `set_pending` and add `update` + `list_pending`. Keep `get_pending`, `set_pr_url`, `clear`, `_safe`, `_path` unchanged.

```python
    def set_pending(self, surface, chat_id, *, spec_id, repo, base_branch,
                    idea="", phase="generating", round=0) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path(surface, chat_id).write_text(
            json.dumps({"spec_id": spec_id, "repo": repo, "base_branch": base_branch,
                        "pr_url": None, "surface": str(surface), "chat_id": str(chat_id),
                        "idea": idea, "phase": phase, "round": round,
                        "clarify_questions": []}),
            encoding="utf-8",
        )

    def update(self, surface, chat_id, **fields) -> None:
        cur = self.get_pending(surface, chat_id)
        if cur is None:
            return
        cur.update(fields)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path(surface, chat_id).write_text(json.dumps(cur), encoding="utf-8")

    def list_pending(self) -> list:
        out = []
        if not self._dir.exists():
            return out
        for p in sorted(self._dir.glob("*.json")):
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except Exception:  # noqa: BLE001 — one corrupt file never breaks the sweep
                continue
        return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/harness/tools/test_chat_task_store_clarify.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/harness/tools/chat_task_store.py tests/unit/harness/tools/test_chat_task_store_clarify.py README.md
git commit -m "feat(clarify): ChatTaskStore gains idea/phase/round + update + list_pending"
```

---

### Task 4: ClarifyWatcher

**Files:**
- Create: `src/ai_dev_system/gateway/clarify_watcher.py`
- Test: `tests/unit/gateway/test_clarify_watcher.py`

**Interfaces:**
- Consumes: `ChatTaskStore.list_pending`/`update` (T3); `format_questions` (T1); `SessionStore.load_or_create`/`append`; `platform.reply(chat_id:int, text:str)`
- Produces: `ClarifyWatcher(chat_task_store, platforms_by_name, session_store, storage_root)` with `check_once() -> int`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/gateway/test_clarify_watcher.py
import json
from pathlib import Path

from ai_dev_system.gateway.clarify_watcher import ClarifyWatcher
from ai_dev_system.harness.tools.chat_task_store import ChatTaskStore


class FakePlatform:
    def __init__(self): self.sent = []
    def reply(self, chat_id, text): self.sent.append((chat_id, text))


class FakeSessions:
    def __init__(self): self.appended = []
    def load_or_create(self, surface, chat_id): return f"sid-{surface}-{chat_id}"
    def append(self, sid, role, content): self.appended.append((sid, role, content))


def _write_spec(root, spec_id, clarify):
    d = Path(root) / "task_specs"; d.mkdir(parents=True, exist_ok=True)
    (d / f"{spec_id}.json").write_text(json.dumps({"clarify": clarify}), encoding="utf-8")


def _store(tmp_path, **over):
    s = ChatTaskStore(str(tmp_path))
    s.set_pending("Sigo", "5913", spec_id="ab", repo="/r", base_branch="main", idea="add X")
    if over:
        s.update("Sigo", "5913", **over)
    return s


def test_pushes_question_once_and_marks_awaiting(tmp_path):
    s = _store(tmp_path)
    _write_spec(tmp_path, "ab", {"needed": True, "questions": ["GUID hay PK?"]})
    plat = FakePlatform(); sess = FakeSessions()
    w = ClarifyWatcher(s, {"Sigo": plat}, sess, str(tmp_path))

    assert w.check_once() == 1
    assert plat.sent and "GUID hay PK?" in plat.sent[0][1]
    assert sess.appended and sess.appended[0][1] == "assistant"
    assert s.get_pending("Sigo", "5913")["phase"] == "awaiting_clarify"

    assert w.check_once() == 0                    # dedup: already awaiting
    assert len(plat.sent) == 1


def test_no_push_when_not_needed(tmp_path):
    s = _store(tmp_path)
    _write_spec(tmp_path, "ab", {"needed": False, "questions": []})
    plat = FakePlatform()
    w = ClarifyWatcher(s, {"Sigo": plat}, FakeSessions(), str(tmp_path))
    assert w.check_once() == 0 and plat.sent == []


def test_no_push_when_round_cap_reached(tmp_path):
    s = _store(tmp_path, round=2)
    _write_spec(tmp_path, "ab", {"needed": True, "questions": ["Q?"]})
    plat = FakePlatform()
    w = ClarifyWatcher(s, {"Sigo": plat}, FakeSessions(), str(tmp_path))
    assert w.check_once() == 0 and plat.sent == []


def test_no_spec_file_yet_is_silent(tmp_path):
    s = _store(tmp_path)                          # worker still running, no spec json
    plat = FakePlatform()
    w = ClarifyWatcher(s, {"Sigo": plat}, FakeSessions(), str(tmp_path))
    assert w.check_once() == 0 and plat.sent == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/gateway/test_clarify_watcher.py -v`
Expected: FAIL — `ModuleNotFoundError: ai_dev_system.gateway.clarify_watcher`

- [ ] **Step 3: Implement**

```python
# src/ai_dev_system/gateway/clarify_watcher.py
"""ClarifyWatcher — swept once per daemon poll loop, alongside RunStatusWatcher.

For each pending single-task chat record whose spec finished with blocking
findings, push the pre-generated questions to the chat ONCE and flip the record to
phase='awaiting_clarify'. Reads JSON + sends only — never calls an LLM (it runs on
the single-threaded daemon loop). One bad record never kills the sweep.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from ai_dev_system.task_graph.clarify_questions import format_questions

logger = logging.getLogger(__name__)

_ROUND_CAP = 2


class ClarifyWatcher:
    def __init__(self, chat_task_store, platforms_by_name: dict, session_store,
                 storage_root: str) -> None:
        self._store = chat_task_store
        self._platforms = platforms_by_name
        self._sessions = session_store
        self._specs_dir = Path(storage_root) / "task_specs"

    def check_once(self) -> int:
        pushed = 0
        for rec in self._store.list_pending():
            try:
                pushed += self._check(rec)
            except Exception:  # noqa: BLE001 — one bad record never kills the sweep
                logger.exception("clarify: error on record %s", rec.get("spec_id"))
        return pushed

    def _check(self, rec: dict) -> int:
        if rec.get("phase") == "awaiting_clarify":
            return 0
        if rec.get("round", 0) >= _ROUND_CAP:
            return 0
        spec_path = self._specs_dir / f"{rec.get('spec_id')}.json"
        if not spec_path.exists():
            return 0
        try:
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return 0
        clarify = spec.get("clarify") or {}
        if not clarify.get("needed"):
            return 0
        surface, chat_id = rec.get("surface"), rec.get("chat_id")
        platform = self._platforms.get(surface)
        if platform is None:
            return 0
        questions = clarify.get("questions") or []
        msg = format_questions(questions)
        platform.reply(int(chat_id), msg)
        sid = self._sessions.load_or_create(surface, chat_id)
        self._sessions.append(sid, "assistant", msg)
        self._store.update(surface, chat_id, phase="awaiting_clarify",
                           clarify_questions=questions)
        return 1
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/gateway/test_clarify_watcher.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/gateway/clarify_watcher.py tests/unit/gateway/test_clarify_watcher.py README.md
git commit -m "feat(clarify): ClarifyWatcher pushes blocking questions to chat"
```

---

### Task 5: wire ClarifyWatcher into the gateway

**Files:**
- Modify: `src/ai_dev_system/cli/commands/gateway.py` (`build_gateway`, lines 41-50)
- Test: `tests/unit/cli/test_gateway_clarify_wiring.py`

**Interfaces:**
- Consumes: `ClarifyWatcher` (T4), `ChatTaskStore` (T3); existing `cfg`, `conn_factory`, `platforms_by_name`

- [ ] **Step 1: Write the failing test** (asserts the post_poll_hook sweeps the clarify store — i.e. a pending+blocking record is pushed after one daemon iteration)

```python
# tests/unit/cli/test_gateway_clarify_wiring.py
import json
from pathlib import Path

from ai_dev_system.cli.commands.gateway import build_gateway, _ensure_schema
from ai_dev_system.harness.tools.chat_task_store import ChatTaskStore


def test_post_poll_hook_pushes_clarify(tmp_path, monkeypatch):
    db = tmp_path / "c.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("AI_DEV_TELEGRAM_BOTS",
                       json.dumps([{"label": "Sigo", "token": "1:x", "chat_ids": [5913]}]))
    _ensure_schema(f"sqlite:///{db}")

    sent = []
    def fake_sender(token, chat_id, text, *, transport=None): sent.append((chat_id, text))

    from ai_dev_system.config import Config
    cfg = Config.from_env()
    daemon = build_gateway(cfg, sender=fake_sender)

    store = ChatTaskStore(str(cfg.storage_root))
    store.set_pending("Sigo", "5913", spec_id="ab", repo="/r", base_branch="main", idea="x")
    specs = Path(cfg.storage_root) / "task_specs"; specs.mkdir(parents=True, exist_ok=True)
    (specs / "ab.json").write_text(
        json.dumps({"clarify": {"needed": True, "questions": ["GUID hay PK?"]}}), encoding="utf-8")

    daemon._post_poll_hook()                       # run one sweep directly
    assert sent and "GUID hay PK?" in sent[0][1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/cli/test_gateway_clarify_wiring.py -v`
Expected: FAIL — clarify not pushed (only RunStatusWatcher runs); `sent` empty.

- [ ] **Step 3: Implement** — in `build_gateway`, add imports, hoist `session_store`, construct `ClarifyWatcher`, run both sweeps.

Add to the import block (after the existing `SessionStore` import):
```python
    from ai_dev_system.gateway.clarify_watcher import ClarifyWatcher
    from ai_dev_system.harness.tools.chat_task_store import ChatTaskStore
```

Replace lines 41-50 (`platforms_by_name = ...` through the `return GatewayDaemon(...)`) with:
```python
    platforms_by_name = {p.name: p for p in registry.adapters()}
    session_store = SessionStore(conn_factory)

    watcher = RunStatusWatcher(conn_factory, link_store, platforms_by_name)
    clarify_watcher = ClarifyWatcher(
        ChatTaskStore(cfg.storage_root), platforms_by_name, session_store,
        str(cfg.storage_root),
    )

    def _post_poll():
        watcher.check_once()
        clarify_watcher.check_once()

    return GatewayDaemon(
        factory=factory, platforms=registry.adapters(), home=assistant_home(),
        session_store=session_store,
        poll_timeout=poll_timeout,
        post_poll_hook=_post_poll,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/cli/test_gateway_clarify_wiring.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/cli/commands/gateway.py tests/unit/cli/test_gateway_clarify_wiring.py README.md
git commit -m "feat(clarify): wire ClarifyWatcher into the gateway post_poll_hook"
```

---

### Task 6: dev_pipeline — store idea, clarify branch, dev_answer_clarify

**Files:**
- Modify: `src/ai_dev_system/harness/tools/dev_pipeline.py` (`dev_task_start` ~line 617; `dev_run_status` spec-ready branch ~line 244; add `dev_answer_clarify`; extend the returned tool list ~line 623)
- Test: `tests/unit/harness/tools/test_dev_pipeline_clarify.py`

**Interfaces:**
- Consumes: `ChatTaskStore.set_pending(..., idea=)`/`get_pending`/`update` (T3); `format_questions` (T1)
- Produces: `dev_answer_clarify` tool (handler arg `{"answer": str}`); `make_dev_pipeline_tools` returns 5 tools

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/harness/tools/test_dev_pipeline_clarify.py
import asyncio, json
from pathlib import Path

from ai_dev_system.harness.tools.dev_pipeline import make_dev_pipeline_tools
from ai_dev_system.harness.tools.chat_task_store import ChatTaskStore


class Cfg:
    def __init__(self, root):
        self.storage_root = root
        self.database_url = "sqlite://"
        from ai_dev_system.config import TelegramBotConfig
        self.telegram_bots = (TelegramBotConfig(label="Sigo", token="1:x",
                              allowed_chat_ids=(5913,), repo_path="/repos/Sigo",
                              base_branch="main"),)


def _tools(tmp_path, spawned):
    store = ChatTaskStore(str(tmp_path))
    tools = make_dev_pipeline_tools(
        surface="Sigo", chat_id="5913", conn_factory=lambda: None, config=Cfg(str(tmp_path)),
        link_store=None, spawn_task_worker=lambda argv, **k: spawned.append(argv),
        spawn_executor=lambda *a, **k: None, create_pr=lambda *a, **k: {},
        make_spec_id=lambda: "specid", chat_task_store=store,
    )
    return {t.name: t for t in tools}, store


def _write_spec(tmp_path, clarify):
    d = Path(tmp_path) / "task_specs"; d.mkdir(parents=True, exist_ok=True)
    (d / "specid.json").write_text(
        json.dumps({"facets": {}, "clarify": clarify}), encoding="utf-8")


def test_task_start_stores_idea(tmp_path):
    spawned = []
    tools, store = _tools(tmp_path, spawned)
    asyncio.run(tools["dev_task_start"].handler({"task_description": "add OwnerId"}))
    assert store.get_pending("Sigo", "5913")["idea"] == "add OwnerId"


def test_run_status_shows_questions_when_blocking(tmp_path):
    spawned = []
    tools, store = _tools(tmp_path, spawned)
    store.set_pending("Sigo", "5913", spec_id="specid", repo="/r", base_branch="main", idea="x")
    _write_spec(tmp_path, {"needed": True, "questions": ["GUID hay PK?"]})
    out = asyncio.run(tools["dev_run_status"].handler({}))
    assert "GUID hay PK?" in out["content"][0]["text"]
    assert store.get_pending("Sigo", "5913")["phase"] == "awaiting_clarify"


def test_run_status_plan_ready_when_clean(tmp_path, monkeypatch):
    spawned = []
    tools, store = _tools(tmp_path, spawned)
    store.set_pending("Sigo", "5913", spec_id="specid", repo="/r", base_branch="main", idea="x")
    _write_spec(tmp_path, {"needed": False, "questions": []})
    import ai_dev_system.task_graph.single_task_plan as sp
    monkeypatch.setattr(sp, "load_plan", lambda *a, **k: {"graph": {"tasks": [1, 2]}})
    out = asyncio.run(tools["dev_run_status"].handler({}))
    assert "Plan sẵn sàng" in out["content"][0]["text"]


def test_answer_clarify_merges_and_respawns(tmp_path):
    spawned = []
    tools, store = _tools(tmp_path, spawned)
    store.set_pending("Sigo", "5913", spec_id="specid", repo="/r", base_branch="main",
                      idea="add OwnerId")
    store.update("Sigo", "5913", phase="awaiting_clarify", clarify_questions=["GUID hay PK?"])
    out = asyncio.run(tools["dev_answer_clarify"].handler({"answer": "GUID thật"}))
    assert spawned, "worker re-spawned"
    argv = spawned[-1]
    merged = argv[argv.index("--idea") + 1]
    assert "add OwnerId" in merged and "GUID thật" in merged
    rec = store.get_pending("Sigo", "5913")
    assert rec["phase"] == "generating" and rec["round"] == 1


def test_answer_clarify_noop_when_not_awaiting(tmp_path):
    spawned = []
    tools, store = _tools(tmp_path, spawned)
    store.set_pending("Sigo", "5913", spec_id="specid", repo="/r", base_branch="main", idea="x")
    out = asyncio.run(tools["dev_answer_clarify"].handler({"answer": "hi"}))
    assert not spawned
    assert "không có câu hỏi" in out["content"][0]["text"].lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/harness/tools/test_dev_pipeline_clarify.py -v`
Expected: FAIL — `KeyError: 'dev_answer_clarify'` / idea not stored.

- [ ] **Step 3: Implement** three edits in `dev_pipeline.py`.

(a) Add import near the top (with the other `ai_dev_system` imports):
```python
from ai_dev_system.task_graph.clarify_questions import format_questions
```

(b) `dev_task_start` — pass `idea` to `set_pending` (replace the `set_pending` call ~line 617):
```python
        chat_task_store.set_pending(surface, chat_id, spec_id=spec_id,
                                    repo=_repo_path, base_branch=_base_branch,
                                    idea=task_description)
```

(c) `dev_run_status` — in the "Spec ready?" branch, replace the body starting at `if spec_path.exists():` (current line 244) with a clarify check BEFORE the plan summary:
```python
            # 2. Spec ready? Clarify gate first, else materialize + summarize the plan.
            if spec_path.exists():
                spec = json.loads(spec_path.read_text(encoding="utf-8"))
                clarify = spec.get("clarify") or {}
                if clarify.get("needed") and pending.get("round", 0) < 2:
                    chat_task_store.update(surface, chat_id, phase="awaiting_clarify",
                                           clarify_questions=clarify.get("questions") or [])
                    return {"content": [{"type": "text", "text":
                        format_questions(clarify.get("questions") or [])}]}
                plan = load_plan(sr, spec_id) or plan_single_task(spec, spec_id, storage_root=sr)
                steps = (plan.get("graph") or {}).get("tasks") or plan.get("graph") or []
                n = len(steps) if isinstance(steps, list) else 0
                return {"content": [{"type": "text", "text":
                    f"📋 Plan sẵn sàng ({n} bước). Nhắn 'duyệt' để chạy."}]}
```

(d) Add the `dev_answer_clarify` tool just before `return [dev_newproject_start, ...]` (line 623), and add it to the returned list:
```python
    @tool(
        "dev_answer_clarify",
        "Submit the user's answer to a clarifying question the bot asked about a "
        "pending coding task. Use this when a clarification is pending (the bot just "
        "asked) and the user replies with their decision — do NOT start a new task.",
        {"answer": str},
    )
    async def dev_answer_clarify(args: dict[str, Any]) -> dict[str, Any]:
        pending = chat_task_store.get_pending(surface, chat_id)
        if not pending or pending.get("phase") != "awaiting_clarify":
            return {"content": [{"type": "text", "text":
                "Hiện không có câu hỏi nào đang chờ trả lời."}]}
        questions = pending.get("clarify_questions") or []
        merged = (pending.get("idea", "") + "\n\n## Làm rõ\n"
                  + "\n".join(questions)
                  + f"\n\nNgười dùng trả lời: {args['answer']}")
        spec_id = pending["spec_id"]
        log_dir = Path(config.storage_root) / "ui_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        argv = [
            sys.executable, "-m", "ai_dev_system.task_graph.single_task_worker",
            "--id", spec_id, "--idea", merged, "--repo", pending["repo"],
            "--storage-root", str(config.storage_root),
            "--database-url", str(config.database_url),
        ]
        try:
            with open(log_dir / f"task_{spec_id[:8]}.log", "a", encoding="utf-8",
                      errors="replace") as logf:
                _spawn_worker(argv, stdout=logf, stderr=subprocess.STDOUT, cwd=str(_REPO_ROOT))
        except Exception as exc:  # pragma: no cover
            return {"content": [{"type": "text", "text": f"spawn error: {exc}"}]}
        chat_task_store.update(surface, chat_id, phase="generating", idea=merged,
                               round=pending.get("round", 0) + 1)
        return {"content": [{"type": "text", "text":
            "✅ Đã nhận. Đang cập nhật spec theo câu trả lời của bạn…"}]}

    return [dev_newproject_start, dev_run_status, dev_answer_gate, dev_task_start,
            dev_answer_clarify]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/harness/tools/test_dev_pipeline_clarify.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/harness/tools/dev_pipeline.py tests/unit/harness/tools/test_dev_pipeline_clarify.py README.md
git commit -m "feat(clarify): dev_answer_clarify + dev_run_status clarify gate + store idea"
```

---

### Task 7: system-prompt routing in the assistant

**Files:**
- Modify: `src/ai_dev_system/assistant/factory.py` (`AssistantFactory.for_chat` and `_build_chat_runtime` already resolve `surface`; add a per-chat prompt suffix)
- Test: `tests/unit/assistant/test_factory_clarify_prompt.py`

**Interfaces:**
- Consumes: `config.telegram_bots` (resolve repo by `label == surface`)
- Produces: the per-chat `Assistant.base_prompt` includes the bound-repo line + the clarify-routing rule

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/assistant/test_factory_clarify_prompt.py
from ai_dev_system.assistant.factory import build_clarify_prompt_suffix
from ai_dev_system.config import TelegramBotConfig


def test_suffix_names_repo_and_routing_when_bound():
    bots = (TelegramBotConfig(label="Sigo", token="t", repo_path="/repos/Sigo",
                              base_branch="main"),)
    s = build_clarify_prompt_suffix("Sigo", bots)
    assert "Sigo" in s
    assert "dev_task_start" in s
    assert "dev_answer_clarify" in s


def test_suffix_generic_when_not_bound():
    s = build_clarify_prompt_suffix("Sigo", ())
    assert "dev_answer_clarify" in s              # routing rule still present
    assert "/repos" not in s                       # no bound-repo claim
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/assistant/test_factory_clarify_prompt.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_clarify_prompt_suffix'`

- [ ] **Step 3: Implement** — add the helper and call it in `for_chat`.

Add at module scope in `factory.py` (after `_SYSTEM_PROMPT`):
```python
def build_clarify_prompt_suffix(surface: str, telegram_bots) -> str:
    """Per-chat awareness: which repo this bot serves + how to route a clarify answer."""
    repo_path = ""
    base_branch = ""
    for b in telegram_bots or ():
        if getattr(b, "label", None) == surface:
            repo_path = getattr(b, "repo_path", "") or ""
            base_branch = getattr(b, "base_branch", "") or ""
            break
    parts = []
    if repo_path:
        parts.append(
            f"Bạn được gắn với repo '{surface}' (nhánh nền '{base_branch or 'main'}'). "
            "Khi người dùng yêu cầu sửa/thêm code, dùng tool dev_task_start; hỏi tiến độ "
            "dùng dev_run_status; duyệt plan dùng dev_answer_gate."
        )
    parts.append(
        "QUAN TRỌNG: nếu lượt trước bạn (bot) đã hỏi người dùng một câu LÀM RÕ, thì tin "
        "nhắn kế tiếp của họ là CÂU TRẢ LỜI — gọi tool dev_answer_clarify với nguyên văn, "
        "KHÔNG tạo task mới."
    )
    return "\n\n".join(parts)
```

In `for_chat`, compute the effective base prompt and pass it to the `Assistant` (replace the `base_prompt=self._base_prompt` argument):
```python
        suffix = build_clarify_prompt_suffix(
            surface, getattr(self._config, "telegram_bots", ()) if self._config else ()
        )
        effective_prompt = self._base_prompt + ("\n\n" + suffix if suffix else "")
        return Assistant(
            runtime=runtime, memory_store=self._memory_store,
            session_store=self._session_store, budget=self._budget,
            base_prompt=effective_prompt, session_id=session_id,
            window=self._window, cap_usd=self._cap_usd,
        )
```

(Note: `self._config` is set only when `link_store` is provided; the `getattr(..., ())` guard keeps REPL/test callers — where `_config` is None — on the generic suffix.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/assistant/test_factory_clarify_prompt.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/assistant/factory.py tests/unit/assistant/test_factory_clarify_prompt.py README.md
git commit -m "feat(clarify): per-chat prompt — repo binding + clarify-answer routing"
```

---

## Final verification (after all tasks)

- [ ] Run the full suite: `python -m pytest -q` → all pass, `test_docs_reconciliation` green.
- [ ] Manual smoke (user-run, needs live bot): send a task whose request contradicts the codebase → bot pushes a question → reply → bot regenerates spec → "Plan sẵn sàng" → "duyệt" → PR. Watch `round` does not exceed 2.

## Out of scope (YAGNI)

- Multiple concurrent clarification threads per chat.
- Structured/button answers (free-text only).
- Clarifications for the new-project debate (run_links) flow.
- Editing a question before it is answered.
