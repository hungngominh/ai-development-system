# Compose/deploy + docs for per-project data (SP-4) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update Docker deploy config + prose docs to match the shipped per-project data model (repo-bound → `<repo>/.ai-dev/state/`; global `/data` = fallback), and put global data on the host via bind-mount.

**Architecture:** No app logic changes. Compose swaps the `ai-dev-data` named volume for a `./data` host bind-mount; docs gain a two-tier data-model description; new doc-reconciliation tests lock the doc invariants in.

**Tech Stack:** Docker Compose YAML, Markdown, pytest.

## Global Constraints

- No application/logic changes; no Dockerfile change; no workflow-v2.md rewrite.
- Keep ALL existing `test_docs_reconciliation.py` invariants green (SQLite, intake front-door, single-task working, module tree, skills table exactly {start-project, review-debate, review-verification}, no live postgres, test count).
- Global `/data` env stays as the fallback; per-project override happens at runtime (unchanged).
- Preserve existing file content when editing (append/insert, don't clobber).

---

### Task 1: Compose bind-mount + `.gitignore` + `.env.example`

**Files:**
- Modify: `docker-compose.yml`
- Modify: `.gitignore`
- Modify: `.env.example`

- [ ] **Step 1: Bind-mount global data in `docker-compose.yml`**

Read the file first. Replace the named-volume mount line under `volumes:` — currently:
```yaml
      # SQLite DB + storage, persist qua restart/rebuild.
      - ai-dev-data:/data
```
with a host bind-mount + clarifying comment:
```yaml
      # Global fallback DB + storage (chỉ cho bot KHÔNG gắn repo / lệnh không --repo).
      # Per-project data nằm trong repo tại <repo>/.ai-dev/state/. Bind ra host.
      - ./data:/data
```

Then delete the now-unused top-level named-volume declaration at the end of the file:
```yaml
volumes:
  ai-dev-data:
```
(Remove those lines entirely. If it leaves a trailing blank section, that's fine.)

Leave the `environment:` block (including `DATABASE_URL`/`STORAGE_ROOT`) unchanged — it is the intended global fallback. Optionally the `DATABASE_URL`/`STORAGE_ROOT` lines may get a one-line `#` comment noting "global fallback", but do not change their values.

- [ ] **Step 2: gitignore the host data dir**

Read `.gitignore`; if it does not already contain a `/data/` entry, append one line `/data/` (preserve all existing entries). If `.gitignore` is missing, create it with `/data/\n`.

- [ ] **Step 3: Document the fallback vars in `.env.example`**

Append to `.env.example` (preserve existing content):
```
# Global fallback DB + storage — used only by bots with NO bound repo, and by
# CLI/webui runs without --repo. Repo-bound work auto-uses <repo>/.ai-dev/state/.
# (In Docker these are set to /data by docker-compose.yml.)
# DATABASE_URL=sqlite:///~/.ai-dev-system/control.db
# STORAGE_ROOT=~/.ai-dev-system/storage
```

- [ ] **Step 4: Validate YAML + no stale volume reference**

Run:
```bash
python -c "import yaml; yaml.safe_load(open('docker-compose.yml')); yaml.safe_load(open('docker-compose.override.yml')); print('yaml ok')"
grep -n "ai-dev-data" docker-compose.yml || echo "no ai-dev-data (good)"
grep -n "./data:/data" docker-compose.yml && echo "bind mount present"
```
Expected: `yaml ok`; `no ai-dev-data (good)`; `bind mount present`.

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml .gitignore .env.example
git commit -m "feat(deploy): bind-mount global data to host; document fallback vs per-project"
```

---

### Task 2: Two-tier data docs + doc-reconciliation tests

**Files:**
- Modify: `tests/unit/test_docs_reconciliation.py` (add 3 tests)
- Modify: `README.md`
- Modify: `SETUP.md`
- Modify: `docs/architecture.md`

**Interfaces:**
- Consumes: existing `_read(rel)` helper in `test_docs_reconciliation.py`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_docs_reconciliation.py`:

```python
# --------------------------------------------------------------------------- #
# Per-project data model (SP-4): docs must describe <repo>/.ai-dev/state + --repo
# --------------------------------------------------------------------------- #
def test_readme_documents_per_project_data():
    readme = _read("README.md")
    assert ".ai-dev/state" in readme, "README must document the per-project data path"
    assert ("--repo" in readme) or ("AIDEV_REPO" in readme), (
        "README must mention --repo / AIDEV_REPO"
    )


def test_setup_documents_per_project_and_fallback():
    setup = _read("SETUP.md")
    assert ".ai-dev/state" in setup, "SETUP must document the per-project data path"
    assert ("--repo" in setup) or ("AIDEV_REPO" in setup), (
        "SETUP must mention --repo / AIDEV_REPO"
    )
    assert ("fallback" in setup.lower()) or ("global" in setup.lower()), (
        "SETUP must explain the global fallback"
    )


def test_architecture_documents_per_project_data():
    arch = _read("docs/architecture.md")
    assert ".ai-dev/state" in arch, "architecture.md must document the per-project layout"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_docs_reconciliation.py -q -k "per_project or setup_documents or architecture_documents"`
Expected: 3 FAIL (docs don't mention `.ai-dev/state` yet).

- [ ] **Step 3: Add the README subsection**

In `README.md`, immediately after the `## Trạng thái` bullet list (after the `LLM provider: ...` line, before the `---` separator that precedes `## Bắt đầu`), insert:

```markdown

## Dữ liệu per-project

- **Per-project (mặc định khi gắn repo):** mỗi repo có DB + storage riêng tại `<repo>/.ai-dev/state/{control.db, storage/}` (tự khởi tạo, gitignore). Áp dụng cho bot Telegram gắn repo, và cho webui/CLI khi truyền `--repo <path>` hoặc đặt `AIDEV_REPO`.
- **Global (fallback):** bot không gắn repo / chạy không `--repo` dùng `~/.ai-dev-system/` (hoặc `/data` trong Docker).
```

- [ ] **Step 4: Add the SETUP section**

In `SETUP.md`, after the `## Database backend` section (after the "PostgreSQL backend đã bị bỏ…" line), insert:

```markdown

## Dữ liệu per-project

Khi gắn repo, hệ thống lưu DB + storage riêng cho từng dự án tại `<repo>/.ai-dev/state/{control.db, storage/}` — tự khởi tạo và gitignore (qua `<repo>/.ai-dev/.gitignore`).

- **Telegram bot gắn repo:** tự động per-project (không cần cấu hình thêm).
- **webui / CLI:** chạy per-project bằng `--repo <path>` hoặc biến môi trường `AIDEV_REPO=<path>` (vd `ai-dev --repo /path/to/repo intake ...`, hoặc `AIDEV_REPO=/path/to/repo ai-dev webui`).
- **Global (fallback):** bot không gắn repo / lệnh không có `--repo` dùng `~/.ai-dev-system/` (hoặc `/data` khi chạy Docker).
```

- [ ] **Step 5: Extend the architecture `db/` description**

In `docs/architecture.md`, find the `db/` module description line (mentions "Persistent storage duy nhất … stdlib `sqlite3`"). Append one sentence to that description:

```markdown
Dữ liệu tách theo dự án: mỗi repo có DB riêng tại `<repo>/.ai-dev/state/control.db` (storage kèm theo); bot/lệnh không gắn repo dùng DB global fallback.
```

(Add it within/after that existing sentence so the `db/` paragraph now states the two-tier layout. Do not remove the "Không có … PostgreSQL" clause — the no-postgres invariant must stay.)

- [ ] **Step 6: Run the new tests + verify existing doc invariants still pass**

Run: `python -m pytest tests/unit/test_docs_reconciliation.py -q -k "not test_readme_test_count_matches_collected_count"`
Expected: PASS (the 3 new tests + all other doc invariants; the test-count check is intentionally excluded here and handled in Task 3).

- [ ] **Step 7: Commit**

```bash
git add tests/unit/test_docs_reconciliation.py README.md SETUP.md docs/architecture.md
git commit -m "docs: document two-tier per-project data model + reconciliation tests"
```

---

### Task 3: Full suite + README test-count bump

**Files:**
- Modify: `README.md` (test count in `## Trạng thái`, only if changed)

- [ ] **Step 1: Run the full suite**

Run: `python -m pytest -q -p no:cacheprovider`
Expected: all pass EXCEPT `test_docs_reconciliation.py::test_readme_test_count_matches_collected_count` (stale count after the 3 new tests). No other failures — investigate any.

- [ ] **Step 2: Get the live collected count**

Run: `python -m pytest --collect-only -q -p no:cacheprovider` and read the final `N tests collected` line.

- [ ] **Step 3: Update the README count**

In `README.md`, `## Trạng thái`, set the `- **<N> tests** — …` line to the collected count (currently `1948`; +3 new).

- [ ] **Step 4: Verify reconciliation passes**

Run: `python -m pytest tests/unit/test_docs_reconciliation.py -q`
Expected: PASS (all, including the count check).

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs(readme): bump test count for per-project deploy/docs (SP-4)"
```

---

## Self-Review

**Spec coverage:**
- Compose bind-mount `./data:/data` + drop named volume + keep fallback env → Task 1 ✓
- `.gitignore` `/data/` → Task 1 ✓
- `.env.example` fallback comment → Task 1 ✓
- README/SETUP/architecture two-tier docs → Task 2 ✓
- New doc-reconciliation tests (README/SETUP/architecture mention `.ai-dev/state` + `--repo`/`AIDEV_REPO` + fallback) → Task 2 ✓
- Existing doc invariants preserved → Task 2 Step 6 (runs the whole file) ✓
- README count bump → Task 3 ✓
- Non-goals (no logic/Dockerfile/workflow-v2 change, no migration) honored ✓

**Placeholder scan:** none — every step has concrete content/commands.

**Type consistency:** N/A (docs/config). New tests use the existing `_read(rel)` helper; token checks (`.ai-dev/state`, `--repo`/`AIDEV_REPO`, `fallback`/`global`) match the exact strings inserted into README/SETUP/architecture in Task 2.
