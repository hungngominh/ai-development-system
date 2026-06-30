# Publish task spec + plan as readable files in the bound repo (two-gate flow)

**Date:** 2026-06-30
**Status:** Approved (design)
**Scope:** Single-task repo-bound bot flow only

## Problem

In the repo-bound Telegram bot, when a single-task plan is ready the bot says
"Plan sẵn sàng" and asks the user to approve ("duyệt"). But the spec and plan
live only in the system's `storage_root` (inside the container volume):

- Spec → `storage_root/task_specs/{spec_id}.json`
- Plan → `storage_root/task_specs/{spec_id}-plan.json`

The repo is only touched at **execution time** (branch + commits + PR), *after*
approval. A remote Telegram user who looks in the repo finds nothing and has no
link to read either artifact before approving — they cannot make an informed
decision. Additionally, today the plan is generated *together with* the spec and
there is only one approval gate, so the user can never review the spec on its own.

## Goal

Two changes:

1. **Two readable files in the repo.** Write a task-identified spec markdown and a
   task-identified plan markdown into the **bound repo** on the feature branch,
   commit + push each, and have the bot send a GitHub link so the Telegram user
   can read them.
2. **Two-gate flow.** Generate the spec → user approves the spec → **only then**
   generate the plan → user approves the plan → execute. The plan is no longer
   produced until the spec is approved.

Execution still adds code commits to the **same** branch, so the eventual PR
contains spec + plan + code together and is self-documenting.

Non-goals (v1): the full-project debate flow; branch cleanup on reject; rendering
inline content in chat (link only).

## File layout (merge-safe, task-named)

Two files per task on branch `ai-dev/task-{id8}`:

```
.ai-dev/tasks/task-{id8}-{slug}-spec.md
.ai-dev/tasks/task-{id8}-{slug}-plan.md
```

- `{id8}` = first 8 chars of `spec_id` (guarantees uniqueness)
- `{slug}` = kebab-cased task title (readability)

Each task's files are uniquely named, so they never collide even after several
PRs merge into the default branch. No generic `spec.md` / `plan.md`.

## Document structure

**`...-spec.md`**

```markdown
# {task title}

> Task `task-{id8}` · branch `ai-dev/task-{id8}` · cập nhật {iso-ts}

**Mục tiêu:** {objective / idea}

**Facets:** (table of the relevant facets)

**Acceptance criteria:**
- ...

**Clarify Q&A:** (only if any clarify questions were answered)
- Q: ... / A: ...

**Findings:** (only if self-review findings exist)
- ...
```

**`...-plan.md`**

```markdown
# Plan — {task title}

> Task `task-{id8}` · branch `ai-dev/task-{id8}` · TDD gate: on|off · cập nhật {iso-ts}

## {N} bước
1. **{step title}** — agent `{agent_type}`, phase `{phase}`
   - Done: {done_definition}
   - Deps: {deps or "—"}
2. ...
```

## Components

### `task_graph/repo_docs.py` (new)
- `spec_doc_relpath(spec_id, title) -> str` / `plan_doc_relpath(spec_id, title) -> str`
  Return the two `.ai-dev/tasks/...` paths. Pure, deterministic.
- `render_spec_md(spec) -> str` — renders the spec markdown. Pure, no IO.
- `render_plan_md(spec, plan) -> str` — renders the plan markdown. Pure, no IO.
- `publish_doc(repo_path, relpath, content, commit_msg) -> str | None`
  Ensures the feature branch exists, writes the file, commits, pushes, and returns
  the GitHub blob URL. Returns `None` (and logs) on push failure.
- `blob_url(remote_url, branch, relpath) -> str | None`
  Derives `https://github.com/{owner}/{repo}/blob/{branch}/{relpath}` from the
  origin remote (handles both `https://` and `git@github.com:` forms; returns
  `None` for non-GitHub remotes).

### Shared git helpers (extracted)
`single_task_executor` already has branch-checkout / push logic. Extract the
reusable pieces (ensure-branch, commit-paths, push, get-remote-url) into a small
shared helper so both `repo_docs.publish_doc` and the executor use one code path.
The executor's behavior is unchanged — it just checks out a branch that may
already exist.

### `single_task_worker` (gains `--mode {spec,plan}`)
- `--mode spec` (default, = current behavior + publish): generate the spec; if a
  blocking clarify is needed, stop (clarify gate handles it). Otherwise publish
  `...-spec.md` and record its URL in `{spec_id}.json` under `doc_url`.
- `--mode plan`: load the (approved) spec, run `plan_single_task`, publish
  `...-plan.md`, and record its URL in `{spec_id}-plan.json` under `doc_url`.

Putting all git IO in the worker keeps it **off the gateway/daemon thread**
(consistent with the existing "no heavy work on the daemon thread" rule).

## Flow & hook points (single-task path in `harness/tools/dev_pipeline.py`)

```
dev_task_start ─ spawn worker --mode spec ─▶ spec.md pushed (doc_url in spec json)
                                              │ (clarify? → clarify gate → re-run --mode spec)
                                              ▼
dev_run_status: spec ready, no clarify       set phase=awaiting_spec_approval
   ▶ "📄 Spec sẵn sàng: {spec_url}\nNhắn 'duyệt' để tạo plan."
                                              ▼
dev_answer_gate 'duyệt' (phase=awaiting_spec_approval)
   ▶ spawn worker --mode plan ; set phase=awaiting_plan_approval ; "Đang tạo plan…"
                                              ▼
dev_run_status: plan ready                   (plan json + doc_url present)
   ▶ "📋 Plan sẵn sàng ({N} bước): {plan_url}\nNhắn 'duyệt' để chạy và tạo PR."
                                              ▼
dev_answer_gate 'duyệt' (phase=awaiting_plan_approval)
   ▶ approve_plan + spawn single_task_executor   (existing behavior)
                                              ▼
dev_run_status: exec done → create PR         (existing behavior)
```

Concrete edits:
1. **`dev_run_status`** (`dev_pipeline.py:244-259`): replace the "auto-generate
   plan + 'Plan sẵn sàng'" block. When spec is ready and no clarify is pending and
   the plan does not yet exist → set `phase=awaiting_spec_approval`, return the
   spec link. When the plan exists → set `phase=awaiting_plan_approval`, return the
   plan link + step count. Read URLs from the JSON `doc_url` fields.
2. **`dev_answer_gate`** single-task branch (`dev_pipeline.py:349-377`): branch on
   `pending["phase"]`. `awaiting_spec_approval` + approve → spawn `--mode plan`,
   set `awaiting_plan_approval`. `awaiting_plan_approval` + approve → `approve_plan`
   + spawn executor (existing). Reject at either → clear.
3. **`dev_task_start`** (`dev_pipeline.py:602-629`): message text updated to
   "Đang tạo spec. Hỏi trạng thái rồi nhắn 'duyệt' để duyệt spec." (spec first).
4. **`dev_answer_clarify`** (`dev_pipeline.py:638-665`): unchanged spawn, but it
   re-runs `--mode spec`, which republishes `...-spec.md`; phase returns to
   `awaiting_spec_approval`.

## Edge cases

- **Push fails** (no network/token): `publish_doc` returns `None`; the worker
  records no URL; the bot sends the readiness message without a link (file is
  committed locally on the branch).
- **No repo bound:** single-task already requires a bound repo (`dev_task_start`
  guards on `_repo_path`), so this path always has one.
- **Title slug empty/non-ascii:** slug falls back to `task` when empty; non-ascii
  is reduced to a safe ascii kebab slug. `{id8}` guarantees uniqueness.
- **Reject/abandon:** the branch is left in place (cheap). Cleanup is out of scope.
- **Re-poll idempotency:** publishing happens in the worker (once per mode run),
  not in the polled `dev_run_status`; repeated polls just re-read `doc_url`.

## Decisions

- **Two files**, `...-spec.md` and `...-plan.md` (task-named, no generic names).
- **Two gates**: approve spec → generate plan → approve plan → execute.
- **Same feature branch** as execution (spec + plan + code in one PR).
- **Update = new commit** (not amend/force-push) for an audit trail.
- **Link only** in chat (no inline content) for v1.
- **All git IO in the worker subprocess**, not the gateway thread.

## Testing (TDD)

Unit (pure, no IO):
- `spec_doc_relpath` / `plan_doc_relpath` are unique per `spec_id`, include a
  readable slug, and handle empty/non-ascii titles safely.
- `render_spec_md` emits Mục tiêu / Acceptance / (conditional) Clarify Q&A and
  Findings from a sample spec dict.
- `render_plan_md` emits the step list, count, TDD gate, and deps from sample
  spec + plan dicts.
- `blob_url` derives correct URLs from `https://` and `git@github.com:` remotes;
  returns `None` for non-GitHub remotes.

Integration (temp git repo):
- `publish_doc` creates the branch, commits the file, returns a URL; a second call
  rewrites the file and adds an update commit (no force-push).

Wiring:
- `--mode spec` records `doc_url` in the spec json; `--mode plan` records it in the
  plan json.
- `dev_run_status` returns the spec link at `awaiting_spec_approval` and the plan
  link + step count at `awaiting_plan_approval`; `dev_answer_gate` routes the two
  approvals to the correct action based on phase.
