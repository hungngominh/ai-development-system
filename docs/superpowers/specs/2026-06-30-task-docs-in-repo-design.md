# Publish task spec + plan as a readable file in the bound repo

**Date:** 2026-06-30
**Status:** Approved (design)
**Scope:** Single-task repo-bound bot flow only

## Problem

In the repo-bound Telegram bot, when a single-task plan is ready the bot says
"Plan sẵn sàng" and asks the user to approve ("duyệt"). But the spec and plan
live only in the system's `storage_root` (inside the container volume):

- Spec → task spec JSON in `storage_root/task_specs/{spec_id}.json`
- Plan → `storage_root/task_specs/{spec_id}-plan.json`

The repo is only ever touched at **execution time** (branch + commits + PR),
*after* approval. So a remote Telegram user who looks in the repo finds nothing,
and has no link to read the plan before approving. They cannot make an informed
approval decision.

## Goal

When a single-task plan becomes ready, write a task-identified markdown document
into the **bound repo** on the feature branch, commit and push it, and have the
bot send a GitHub link. Keep the file updated (re-commit + push) when the spec or
plan changes (e.g. after a clarify round). Execution later adds code commits to
the **same** branch, so the eventual PR contains spec + plan + code together and
is self-documenting.

Non-goals (v1): the full-project debate flow; branch cleanup on reject; rendering
inline content in chat (link only).

## File layout (merge-safe, task-named)

A single document per task on branch `ai-dev/task-{id8}`:

```
.ai-dev/tasks/task-{id8}-{slug}.md
```

- `{id8}` = first 8 chars of `spec_id` (guarantees uniqueness)
- `{slug}` = kebab-cased task title (readability)
- One file per task with `## Spec` and `## Plan` sections.

Each task gets its own uniquely-named file, so files never collide even after
several PRs merge into the default branch. No generic `spec.md` / `plan.md`.

## Document structure

```markdown
# {task title}

> Task `task-{id8}` · branch `ai-dev/task-{id8}` · cập nhật {iso-ts}

## Spec
**Mục tiêu:** {objective / idea}

**Facets:** (table of the relevant facets)

**Acceptance criteria:**
- ...

**Clarify Q&A:** (only if any clarify questions were answered)
- Q: ... / A: ...

**Findings:** (only if self-review findings exist)
- ...

## Plan ({N} bước)
- **Branch:** ai-dev/task-{id8}
- **TDD gate:** on | off

1. **{step title}** — agent `{agent_type}`, phase `{phase}`
   - Done: {done_definition}
   - Deps: {deps or "—"}
2. ...
```

## Components

### `task_graph/repo_docs.py` (new)
- `task_doc_relpath(spec_id, title) -> str`
  Returns `.ai-dev/tasks/task-{id8}-{slug}.md`. Pure, deterministic.
- `render_task_doc(spec, plan) -> str`
  Renders the markdown from the spec dict and plan dict. Pure, no IO.
- `publish_task_docs(repo_path, spec, plan) -> str | None`
  Ensures the feature branch exists, writes the file, commits, pushes, and
  returns the GitHub blob URL. Returns `None` (and logs) when no repo is bound
  or the push fails.
- `blob_url(remote_url, branch, relpath) -> str | None`
  Derives a `https://github.com/{owner}/{repo}/blob/{branch}/{relpath}` URL from
  the origin remote (handles both `https://` and `git@github.com:` forms).

### Shared git helpers (extracted)
`single_task_executor` already has branch-checkout / push logic. Extract the
reusable pieces (ensure-branch, commit-paths, push, get-remote-url) into a small
shared helper module so both `repo_docs.publish_task_docs` and the executor use
the same code path. The executor's behavior is unchanged — it just checks out a
branch that may already exist.

## Flow & hook points

1. **Branch created early.** At plan-ready time (not execution time), ensure
   `ai-dev/task-{id8}` exists via the shared helper.
2. **`single_task_worker`** calls `publish_task_docs(repo_path, spec, plan)`
   immediately after the plan is generated, once the spec is final (no blocking
   clarify pending). Commit message: `docs(ai-dev): spec + plan for {title}`.
3. **Bot plan-ready message** (`harness/tools/dev_pipeline.py`) becomes:
   ```
   📋 Plan sẵn sàng ({N} bước).
   📄 Chi tiết: {blob url}
   Nhắn 'duyệt' để chạy và tạo PR.
   ```
4. **Updates.** When `dev_answer_clarify` re-specs and regenerates the plan, it
   calls `publish_task_docs` again → **new commit**
   `docs(ai-dev): update spec+plan after clarify` → push (no force-push; keeps an
   audit trail). The bot resends the link.
5. **Execution unchanged.** The executor checks out the now-existing same branch,
   adds code commits, pushes, and opens the PR — which now also contains the task
   doc.

## Edge cases

- **No repo bound** (pure-spec mode, `repo_path` is `None`/missing):
  `publish_task_docs` returns `None`; the bot keeps the current message with no
  link.
- **Push fails** (no network/token): log the error; still send the plan message;
  the file is committed locally. No link in that message.
- **Reject/abandon:** the branch is left in place (cheap). Cleanup is out of scope
  for v1.
- **Title slug empty/non-ascii:** slug falls back to `task` when empty; non-ascii
  is transliterated/stripped to a safe kebab-case ascii slug. The `{id8}` prefix
  guarantees uniqueness regardless.

## Decisions

- **One file per task**, named `task-{id8}-{slug}.md` (no generic names).
- **Same feature branch** as execution (spec + plan + code in one PR).
- **Update = new commit** (not amend/force-push) for an audit trail.
- **Link only** in chat (no inline content render) for v1.

## Testing (TDD)

Unit (pure, no IO):
- `task_doc_relpath` is unique per `spec_id` and includes a readable slug;
  empty/non-ascii titles produce a safe path.
- `render_task_doc` emits the expected `## Spec` / `## Plan` sections from sample
  spec + plan dicts, including step count, TDD gate, deps, and the
  clarify/findings sections only when present.
- `blob_url` derives correct URLs from both `https://` and `git@github.com:`
  origin remotes; returns `None` for non-GitHub remotes.

Integration (temp git repo):
- `publish_task_docs` creates the branch, commits the single file, returns a URL;
  a second call rewrites the file and adds an update commit (no force-push).
- Guard: missing/`None` repo_path returns `None` and writes nothing.

Wiring:
- The worker publishes after the plan is generated; the `dev_pipeline` plan-ready
  message includes the link when a URL is available and omits it cleanly when not.
