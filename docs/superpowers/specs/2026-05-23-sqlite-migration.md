# Design Spec: SQLite Migration (M0.5)

**Date:** 2026-05-23
**Status:** Draft (user confirmed scope)
**Scope:** Replace PostgreSQL with SQLite as the only supported backend. Zero-config local-dev.

---

## Motivation

User wants "máy nào cũng dùng được" — current PG dependency creates onboarding friction. Removing PG → zero install (sqlite3 is in stdlib).

This is a **scope change** to the locked-decisions doc. Updated: decision #16's TEXT+CHECK pattern remains correct (SQLite favors this anyway). Decision #18 (linear flag order) unaffected.

---

## Goals

- SQLite-only backend, zero PG dependency
- Existing 60 integration tests + 232 unit tests continue to pass
- `DATABASE_URL` accepts `sqlite:///path/to.db` (only form)
- Default DB at `~/.ai-dev-system/store.db` (auto-created if missing)
- Schema migrations idempotent + SQLite-compatible
- Application invariants preserved (1-artifact-ACTIVE-per-type, etc)

## Non-goals

- No SQLAlchemy / no ORM
- No dual-backend support (keep API surface narrow)
- No data migration from existing PG installs (greenfield)

---

## Architectural Changes

### PG → SQLite type map

| PostgreSQL | SQLite | Notes |
|---|---|---|
| `UUID` | `TEXT` | Generate in Python (`uuid.uuid4().hex`) |
| `JSONB` | `TEXT` (JSON) | Use json1 functions (`json_extract`, `json_set`) |
| `UUID[]` | `TEXT` (JSON array) | Stored as `'["...","..."]'` |
| `TEXT[]` | `TEXT` (JSON array) | Same |
| `TIMESTAMPTZ` | `TEXT` (ISO 8601) | `CURRENT_TIMESTAMP` returns `YYYY-MM-DD HH:MM:SS` |
| `BIGINT` | `INTEGER` | SQLite INTEGER is 64-bit |
| `BOOLEAN` | `INTEGER` (0/1) | Convert in app layer |
| `gen_random_uuid()` | (app-side) | Repo generates UUID before INSERT |
| `now()` | `CURRENT_TIMESTAMP` | SQLite returns UTC |
| GIN index on array | (none) | Use json_each() in queries |
| Partial unique index | ✓ supported | `CREATE UNIQUE INDEX ... WHERE ...` works |
| `ON CONFLICT ... DO UPDATE` | ✓ supported | Same syntax (3.24+) |
| `RETURNING` clause | ✓ supported | SQLite 3.35+ |

### Parameter style

- PG: `cursor.execute("SELECT * FROM x WHERE y = %s", (val,))`
- SQLite: `cursor.execute("SELECT * FROM x WHERE y = ?", (val,))`

→ Convert every `%s` to `?` in repos. ~150 occurrences expected.

### Connection API differences

```python
# Old (psycopg)
conn = psycopg.connect(url, row_factory=psycopg.rows.dict_row)
row = conn.execute("SELECT 1 AS x").fetchone()  # row is dict-like
# row["x"] → 1

# New (sqlite3)
conn = sqlite3.connect(path)
conn.row_factory = sqlite3.Row  # dict-like access
row = conn.execute("SELECT 1 AS x").fetchone()
# row["x"] → 1  ✓ same access
```

→ Wrap in `db/connection.py` to keep `conn.execute(...)` API identical.

### JSON column access

```python
# Old (psycopg with JSONB column)
row = conn.execute("SELECT current_artifacts FROM runs WHERE run_id = %s", (rid,)).fetchone()
row["current_artifacts"]  # → dict (psycopg auto-parses)

# New (sqlite3 with TEXT column)
row = conn.execute("SELECT current_artifacts FROM runs WHERE run_id = ?", (rid,)).fetchone()
import json
artifacts = json.loads(row["current_artifacts"])
```

→ Wrap with helper `db_json(row, col)` that parses on read, dumps on write. Avoids scattering `json.loads()` calls.

### Array column access

```python
# Old (PG UUID[])
row["input_artifact_ids"]  # → list of str

# New (SQLite TEXT containing JSON array)
json.loads(row["input_artifact_ids"])  # → list of str
```

→ Same helper covers this.

---

## File-Level Change Plan

### Files to modify

| File | Change | Lines |
|---|---|---|
| `src/ai_dev_system/db/connection.py` | Replace psycopg with sqlite3, keep API | ~30 |
| `docs/schema/control-layer-schema.sql` | Full SQLite rewrite | ~350 |
| `docs/schema/migrations/v2-execution-runner.sql` | Rewrite | ~50 |
| `docs/schema/migrations/v3-debate-engine.sql` | Rewrite | ~50 |
| `docs/schema/migrations/v4-verification.sql` | Rewrite | ~30 |
| `docs/schema/migrations/v5-phase1-v2.sql` | Rewrite | ~100 |
| `src/ai_dev_system/db/repos/runs.py` | `%s` → `?`, json parse | ~200 |
| `src/ai_dev_system/db/repos/task_runs.py` | Same | ~200 |
| `src/ai_dev_system/db/repos/artifacts.py` | Same | ~150 |
| `src/ai_dev_system/db/repos/events.py` | Same | ~80 |
| `src/ai_dev_system/db/repos/escalations.py` | Same | ~80 |
| `src/ai_dev_system/db/repos/version_locks.py` | Same | ~50 |
| `src/ai_dev_system/cli/setup_wizard.py` | Default to SQLite path | ~30 |
| `src/ai_dev_system/config.py` | Validate sqlite:// scheme | ~10 |
| `pyproject.toml` | Remove psycopg dep | -1 line |
| `tests/conftest.py` (if exists) | Use sqlite fixture | ~50 |
| `tests/integration/*.py` | Update fixtures if needed | varied |
| `SETUP.md` | Drop PG instructions | ~30 |
| `CHANGELOG.md` | Document breaking change | +5 |

### Files NOT modified (no SQL inside)

- All eval modules just added
- `cli/core/*` (no DB)
- `feature_flags.py`
- Most of intake/spec/debate/verification code (uses repos, not raw SQL)

---

## Execution Order

| Step | File group | Test after |
|---|---|---|
| **S1** | `db/connection.py` — new sqlite3-backed Connection wrapper class | unit test (mock) |
| **S2** | `db/helpers.py` (new) — json read/write helpers | unit tests |
| **S3** | Rewrite `control-layer-schema.sql` for SQLite | apply to fresh DB, schema valid |
| **S4** | Rewrite v2/v3/v4 migrations | apply, idempotent re-run |
| **S5** | Rewrite v5-phase1-v2.sql for SQLite | apply, schema valid |
| **S6** | Update `config.py` + `setup_wizard.py` (SQLite default path) | manual run setup |
| **S7** | Rewrite `repos/runs.py` (~biggest, ~200 lines) | unit + 5 integration tests pass |
| **S8** | Rewrite `repos/task_runs.py` | unit + relevant integration |
| **S9** | Rewrite `repos/artifacts.py` + `events.py` + `escalations.py` + `version_locks.py` | full integration suite |
| **S10** | Update `tests/conftest.py` + integration fixtures | full suite green |
| **S11** | Remove `psycopg[binary]` from pyproject, regenerate lock | full suite still green |
| **S12** | Update `SETUP.md`, `README.md`, `CHANGELOG.md` | smoke `ai-dev setup` |

S1-S2 are setup. S3-S5 schema. S6 user-facing config. S7-S10 the bulk. S11-S12 cleanup.

---

## Key SQLite Rewrite Patterns

### Pattern 1: Partial unique with WHERE

```sql
-- PG: works
CREATE UNIQUE INDEX uq_artifacts_one_active_per_type
    ON artifacts(run_id, artifact_type)
    WHERE status = 'ACTIVE';

-- SQLite: identical syntax, works ✓
```

### Pattern 2: Array columns → TEXT JSON

```sql
-- PG
input_artifact_ids  UUID[]  NOT NULL DEFAULT '{}',

-- SQLite
input_artifact_ids  TEXT    NOT NULL DEFAULT '[]'
    CHECK (json_valid(input_artifact_ids)),
```

Repo writes use `json.dumps([uuid1, uuid2])`. Reads parse with `json.loads()`.

### Pattern 3: GIN array search → json_each

```sql
-- PG: which artifacts depend on X?
SELECT * FROM artifacts
WHERE input_artifact_ids @> ARRAY['<uuid>']::uuid[];

-- SQLite:
SELECT DISTINCT a.* FROM artifacts a
JOIN json_each(a.input_artifact_ids) je
WHERE je.value = ?;
```

### Pattern 4: Composite default JSON

```sql
-- PG: rich default with NOT NULL JSONB
current_artifacts JSONB NOT NULL DEFAULT '{
    "initial_brief_id": null,
    ...
}'::jsonb,

-- SQLite: same, but as TEXT
current_artifacts TEXT NOT NULL DEFAULT '{
  "initial_brief_id": null,
  "debate_report_id": null,
  ...
}'
CHECK (json_valid(current_artifacts)),
```

### Pattern 5: UUID generation

```python
# In repo, before INSERT:
import uuid
run_id = uuid.uuid4().hex   # 32-char hex string, no dashes
# (or str(uuid.uuid4()) for dashed form, either is fine — pick one and stick)
```

Schema column: `run_id TEXT PRIMARY KEY`. No default — app generates.

### Pattern 6: Cursor result access (dict-like)

```python
# Connection setup
conn.row_factory = sqlite3.Row

# Usage (same as psycopg dict_row)
row = conn.execute("SELECT run_id, status FROM runs WHERE run_id = ?", (rid,)).fetchone()
row["run_id"]  # ✓ works
dict(row)      # ✓ to plain dict
```

---

## Application Invariants (preserved)

The 4 invariants in `docs/schema/application-invariants.sql` are application-layer, not DB-enforced. They continue to hold.

The DB-level invariants enforced via partial unique indexes (one ACTIVE artifact per type per run) work identically in SQLite.

---

## Concurrency Note

SQLite has **single-writer concurrency** (write transactions serialize). For the AI dev system's use case (single user CLI, low write rate), this is fine. The existing concurrency code in `engine/worker.py` (locks, heartbeats) was designed for multi-worker PG — with SQLite, workers serialize naturally.

→ No code change needed for correctness, but high-throughput multi-worker scenarios won't scale. Acceptable for v1.

---

## DB Default Location

`~/.ai-dev-system/store.db` (auto-create directory + file on first connect).

`setup_wizard` flow simplified:
```
DATABASE_URL [sqlite:///~/.ai-dev-system/store.db]: <Enter accepts default>
STORAGE_ROOT [~/.ai-dev-system/storage]:
LLM Provider: ...
```

No DB host/port/password prompts.

---

## Risk Mitigation

| Risk | Mitigation |
|---|---|
| Existing 60 integration tests break | Run after each repo file rewrite, fix immediately |
| JSON read/write boilerplate explodes | Centralize in `db/helpers.py` (`load_json`, `dump_json` per column type) |
| Date/time format drift between PG ISO and SQLite ISO | Use `datetime.fromisoformat()` everywhere |
| Concurrent test runs collide on default db path | Tests use `:memory:` or per-test temp file |
| Forgotten `%s` somewhere | Grep audit: `grep -rn '%s' src/ai_dev_system/db/` after rewrite |

---

## Out of Scope

- Multi-tenant DB
- Encrypted SQLite (SEE / SQLCipher)
- WAL tuning beyond defaults
- DB GUI tools (use DB Browser for SQLite externally)
- Data migration from existing PG installs
- Cross-database joins
