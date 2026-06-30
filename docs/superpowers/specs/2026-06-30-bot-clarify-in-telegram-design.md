# Bot Asks Clarifying Questions in Telegram — Design

**Date:** 2026-06-30
**Builds on:** repo-bound bot chat→PR flow (`2026-06-30-repo-bound-bot-chat-to-pr-design.md`)

## Goal

When the repo-bound bot's agentic spec generation surfaces a **blocking ambiguity**
between the user's request and the actual codebase, the bot **asks the user a
clarifying question in Telegram**, folds the answer back into the task, and
regenerates a clean spec — instead of silently marking the spec `done` and waiting
for "duyệt" while the ambiguity sits unresolved in `findings`.

Motivating real case (2026-06-30): task "add OwnerId (GUID) to
`/SearchingRentalService/List`". The agentic spec found OwnerId is stored as a
numeric `long` PK (`SearchService_Version2.cs:537`); a true GUID lives only at
`UserLogin.ID_GUID`. The grounding step emitted **error-severity** `findings`
flagging the contradiction, plus a `needs_human` security facet (PK enumeration on
a public endpoint). Today those findings are computed and then ignored by the chat
layer. The user's principle: *"nó phải hỏi trong telegram chứ"* — the bot must ask,
in the user's own channel.

## Locked decisions

1. **Incorporation:** on answer, **regenerate the spec** by re-running
   `single_task_worker` with the answer merged into the task description (reuse, not
   patch). Clean, re-grounded spec; ~2–3 min/round.
2. **Trigger:** a clarification is needed when the spec has **any `finding` with
   `severity == "error"`** OR **any facet with `status == "needs_human"`**. Warnings
   do not block.
3. **Delivery:** **proactive push** — the bot messages the question the moment the
   spec finishes, without the user having to poll.
4. **Answer tool:** a **dedicated `dev_answer_clarify` tool** (not an overload of
   `dev_answer_gate`).

## Hard constraint

**All LLM calls stay in the worker subprocess, never in the gateway loop.** The
`ClarifyWatcher` runs inside the single-threaded daemon `post_poll_hook`; doing an
LLM call there would freeze message handling for every chat. Therefore the worker
**pre-generates** the clarifying questions; the watcher only reads JSON and sends.

## Flow (state machine)

```
dev_task_start ──> phase=generating  (worker subprocess runs)
       │
       ▼  worker writes <spec_id>.json including clarify={needed, questions[]}
   ┌───┴──────────────────────────────────────┐
 needed=false                              needed=true  (and round < 2)
   │                                            │
 dev_run_status: "📋 Plan sẵn sàng"        ClarifyWatcher: platform.reply(questions)
   → "duyệt" → executor → PR                 + append question to session history
                                              + phase=awaiting_clarify
                                                  │  user replies (free text)
                                                  ▼  dev_answer_clarify(text):
                                                     merged_idea = idea + Q&A
                                                     re-spawn single_task_worker (same spec_id)
                                                     phase=generating, round += 1
                                                  └────────── loop ──────────┘
```

`round >= 2` with still-blocking findings ⇒ stop asking, fall through to the plan
path with a one-line "còn điểm chưa rõ" note (no infinite loop).

## Detection rule (pure, testable)

New module `src/ai_dev_system/task_graph/clarify_questions.py`:

```python
def find_blocking(spec: dict) -> list[dict]:
    """Blocking items = findings[severity==error] + facets[status==needs_human].
    Returns a normalized list of {kind, key, message} (kind: 'finding'|'facet').
    Reads spec['findings'] (list of {section,dimension,severity,message,fix})
    and spec['facets'] (dict facet_key -> {status,content,reason})."""

def synthesize_questions(blocking: list[dict], *, idea: str, llm) -> list[str]:
    """1–3 concise Vietnamese questions covering the distinct decisions in
    `blocking` (collapse duplicates — e.g. 7 findings about one GUID/PK choice
    become one question). Returns [] if blocking is empty.
    On any LLM error, FALL BACK to the raw finding/facet messages (truncated) so
    the flow never breaks. llm is a completion client injected for tests."""
```

## Components

### 1. Worker integration — `single_task_worker.py` / `single_task.py`

After the spec dict is assembled (facets + findings) and **before** it is written to
`<storage_root>/task_specs/<spec_id>.json`:

```python
blocking = find_blocking(spec)
questions = synthesize_questions(blocking, idea=idea, llm=spec_llm) if blocking else []
spec["clarify"] = {"needed": bool(blocking), "questions": questions}
```

`clarify` is **always** written (so consumers can rely on the key). The worker is a
subprocess, so the extra LLM call is off the daemon thread. Re-runs (after an answer)
overwrite the same `<spec_id>.json`; a resolved task writes `clarify.needed=false`.

### 2. State — `ChatTaskStore` (`harness/tools/chat_task_store.py`)

Extend the per-(surface, chat_id) record. New fields (old records without them stay
valid — readers use `.get` with defaults):

| field | purpose |
|-------|---------|
| `surface`, `chat_id` | let `list_pending()` recover routing without un-mangling the filename |
| `idea` | original task text, needed to re-run with the merged answer |
| `phase` | `"generating"` → `"awaiting_clarify"` → `"generating"`; dedups push + marks "next msg is an answer" |
| `clarify_questions` | the questions last asked (for context / merge prompt) |
| `round` | clarification rounds taken; caps the loop |

New methods:
- `set_pending(..., idea, phase="generating", round=0)` — extend signature
  (keep `repo`, `base_branch`); store `surface`/`chat_id` in the body.
- `update(surface, chat_id, **fields)` — partial update (phase, round,
  clarify_questions) without rewriting unrelated fields.
- `list_pending() -> list[dict]` — scan `chat_tasks/*.json`, return records (each
  carrying its own `surface`/`chat_id`). One unreadable file is skipped, not fatal.

### 3. `ClarifyWatcher` — new `gateway/clarify_watcher.py`

Mirrors `RunStatusWatcher`'s shape (sweep + per-item try/except + push via
`platform.reply`). **No LLM, no plan generation — JSON read + send only.**

```python
class ClarifyWatcher:
    def __init__(self, chat_task_store, platforms_by_name, session_store, storage_root): ...
    def check_once(self) -> int:
        for rec in self._chat_task_store.list_pending():
            try: pushed += self._check(rec)
            except Exception: logger.exception(...)   # one bad record never kills the sweep
        return pushed

    def _check(self, rec) -> int:
        if rec.get("phase") == "awaiting_clarify": return 0      # already asked (dedup)
        if rec.get("round", 0) >= 2: return 0                     # cap reached
        spec = read <storage_root>/task_specs/<rec.spec_id>.json  # None if not yet written
        clarify = (spec or {}).get("clarify") or {}
        if not clarify.get("needed"): return 0
        platform = self._platforms_by_name.get(rec["surface"])
        if platform is None: return 0
        msg = _format_questions(clarify["questions"])
        platform.reply(int(rec["chat_id"]), msg)
        sid = self._session_store.load_or_create(rec["surface"], rec["chat_id"])
        self._session_store.append(sid, "assistant", msg)        # keep the convo coherent
        self._chat_task_store.update(rec["surface"], rec["chat_id"],
                                     phase="awaiting_clarify", clarify_questions=clarify["questions"])
        return 1
```

**Session coherence:** the pushed question is sent outside the assistant turn, so it
must be appended to the session history; otherwise the next user message arrives with
no record that the bot asked anything, and the assistant would mis-route the answer as
a new task. API: `sid = session_store.load_or_create(surface, chat_id)` then
`session_store.append(sid, "assistant", msg)` (`assistant/session.py:20,37`).

### 4. Gateway wiring

In `cli/commands/gateway.py` (lines 41–49: `platforms_by_name` is built, then
`RunStatusWatcher(conn_factory, link_store, platforms_by_name)`, then the daemon is
given `post_poll_hook=watcher.check_once`), also construct `ClarifyWatcher` and set
the hook to run **both** sweeps:

```python
clarify_watcher = ClarifyWatcher(chat_task_store, platforms_by_name, session_store, storage_root)
def _post_poll():
    watcher.check_once()
    clarify_watcher.check_once()
# ... post_poll_hook=_post_poll
```

`chat_task_store` and `session_store` are constructible from `config.storage_root` /
the existing conn at that site; `platforms_by_name` already exists at line 41.

### 5. `dev_answer_clarify` tool — `harness/tools/dev_pipeline.py`

New tool registered alongside the existing four:

```python
@tool("dev_answer_clarify",
      "Submit the user's answer to a clarifying question the bot asked about a "
      "pending coding task. Use this when a clarification is pending and the user "
      "replies with their decision.",
      {"answer": str})
async def dev_answer_clarify(args):
    pending = chat_task_store.get_pending(surface, chat_id)
    if not pending or pending.get("phase") != "awaiting_clarify":
        return text("Hiện không có câu hỏi nào đang chờ trả lời.")
    merged = pending["idea"] + "\n\n## Làm rõ\n" \
             + "\n".join(pending.get("clarify_questions", [])) \
             + f"\n\nNgười dùng trả lời: {args['answer']}"
    # re-spawn single_task_worker with the SAME spec_id and the merged idea
    argv = [..., "single_task_worker", "--id", pending["spec_id"], "--idea", merged,
            "--repo", pending["repo"], "--storage-root", sr, "--database-url", db]
    _spawn_worker(argv, ...)
    chat_task_store.update(surface, chat_id, phase="generating", idea=merged,
                           round=pending.get("round", 0) + 1)
    return text("✅ Đã nhận. Đang cập nhật spec theo câu trả lời của bạn…")
```

### 6. `dev_run_status` clarify branch — `harness/tools/dev_pipeline.py`

In the existing "spec ready" branch (currently `dev_pipeline.py:244`), check clarify
**before** materializing the plan:

```python
if spec_path.exists():
    spec = json.loads(...)
    clarify = spec.get("clarify") or {}
    if clarify.get("needed") and pending.get("round", 0) < 2:
        chat_task_store.update(surface, chat_id, phase="awaiting_clarify",
                               clarify_questions=clarify["questions"])  # dedup vs watcher
        return text(_format_questions(clarify["questions"]))
    # else: existing "📋 Plan sẵn sàng (N bước). Nhắn 'duyệt'." path
```

This is the **pull fallback**: if the user polls before/after the push, they still get
the question (and it sets `phase` so the watcher won't double-send).

### 7. System prompt — `assistant/factory.py` `for_chat`

Build a per-chat prompt suffix appended to `_SYSTEM_PROMPT` (this is the same
insertion point as the separate "bot is aware of its bound repo" improvement, folded
in here since it is one cohesive "the bot knows its situation" change):

- If repo-bound: name the repo + that coding requests go through `dev_task_start`.
- Routing rule: *"Nếu trong lượt trước bot đã hỏi người dùng một câu làm rõ, thì lời
  nhắn kế tiếp của người dùng là CÂU TRẢ LỜI — gọi `dev_answer_clarify` với nguyên văn,
  không tạo task mới."* The pushed question is in the session history (component 3), so
  the model sees the prior question and routes correctly; `dev_answer_clarify` also
  guards on `phase` as a backstop.

## Error handling

- Question synthesis LLM failure → fall back to raw finding/facet messages (flow
  never blocks).
- Re-spawn failure in `dev_answer_clarify` → report error, keep pending.
- Duplicate push → prevented by the `phase` transition.
- `ClarifyWatcher` per-record `try/except` → one bad record never kills the sweep
  (matches `RunStatusWatcher`).
- `round >= 2` → stop asking; fall through to plan with a "còn điểm chưa rõ" note.

## Testing

| Unit | Assertion |
|------|-----------|
| `find_blocking` | error findings + needs_human facets selected; warnings ignored |
| `synthesize_questions` | injected LLM → 1–3 questions; LLM raises → raw-message fallback |
| worker integration | spec written with `clarify.needed`/`questions` (inject LLM) |
| `ClarifyWatcher` | pending + spec.clarify.needed → `reply` called once; 2nd sweep → no double send; `round>=2` → no send |
| session append | push appends the question to session history |
| `dev_answer_clarify` | merges idea + Q&A, re-spawns worker argv (same spec_id), phase→generating, round+1; no pending → guidance |
| `dev_run_status` | clarify.needed → returns questions, sets awaiting; needed=false → plan-ready |
| round cap | round>=2 routes to plan, not another question |

Tools are invoked in tests as `tool.handler({...})` (SdkMcpTool has no `__call__`).
README test count bumped per added tests (`tests/unit/test_docs_reconciliation.py`).

## Out of scope (YAGNI)

- Multiple independent clarification threads per chat (one pending task per chat
  stands).
- Structured/button answers — free-text only.
- Clarifications for the new-project debate flow (run_links) — this slice is the
  repo-bound single-task path only.
- Editing/replacing an in-flight question before it is answered.
